"""Claude Code chats across a switch: the hooks that tell a chat, in plain words, what Switchboard's moves mean
for it.

They go into Claude Code's user settings (the file the status line and the notification hooks use), each running
the same command:

  SessionStart        at the start, and after /clear or a compaction: Claude Code here moves between the person's
                      accounts; an artifact or Claude Doc made under another account reads as "not found" but is not
                      deleted, and no new login is needed; the exact commands (switchboard owner / open / run), and
                      `switchboard chats`; to a resumed chat, the account it started under and where its last
                      request went
  UserPromptSubmit    once per move per chat: "Switchboard moved Claude Code from A to B at 3:02 PM", with where this
                      chat's own last request went, what a move costs it, what it made under each account (artifacts,
                      Claude Docs, watches) and how to reach each as its owner
  PostToolUse and     on the Artifact, ArtifactComments and ArtifactData tools and every claude.ai connector
  PostToolUseFailure  (mcp__claude_ai_*, Claude Docs among them): an answer that says "not found" (or "another
                      organization") gets the item's owner and the exact commands; a connector that failed in Claude
                      Code's own words in a chat that moved gets, once per connector per move and only when something
                      sets the accounts apart (connector_context), what is known: where it last answered, the account
                      the chat is on and since when, that connectors are added per account on claude.ai, whether that
                      account has had it answer on this Mac in the page's window, and the way through (a tool's error
                      reaches only PostToolUseFailure, an answer that reads as one only PostToolUse)
  PreToolUse          on Claude in Chrome's tools: when the extension's account is not the one Claude Code is on and
                      that account has room, asks the engine for a need switch (api.request_need_switch, at most once
                      in 10 minutes); otherwise says plainly what to do

Which account a chat is on, and was on, comes from the chat's own file (chatfile.py: Claude Code writes the login it
started under and each change of login into it), read on from where the last look stopped: the chat's own record
keeps how far it read and the account lines it found (or the page's chats.json starts it), so only new bytes are
read; a first look with neither reads a 4 MB tail at most. Accounts are named through chats.json's organizations. When
the file says nothing (or names no account Switchboard keeps), or its newest change is under 90 seconds old and is not
the account Switchboard moved to (a flutter may still fold back), it is "not known", and Switchboard's own timeline
speaks instead: the account a chat started under is never taken for the one it is on now. One move is told once,
whichever of the two noticed it first.

PostToolUse also notes, without a word to the session, each artifact a session publishes, creates or updates (and each
Claude Doc it makes): one line in hooks/published.jsonl with its link and id, the local file the answer names, the
time and the session (published_line). The page reads those lines within seconds (artifacts.take_published): a new
page opens as its owner at once, and gets its picture (previews.py) while its file is still there. A claude.ai
connector's answer is noted the same way, in the chat's own record only: when that connector last answered here.

What they print is Claude Code's hookSpecificOutput.additionalContext, with hookEventName (Claude Code 2.1.309 and
2.1.325, read with `strings`: all five events take additionalContext). Nothing else: no decision, no
permissionDecision (these hooks never allow or block a tool), and the exit code is always 0.

Their words are plain, name the owner and the exact command, never ask the person to sign in or to run /login, and
never say an item may be deleted.

They read ~/.switchboard/owners.json (written by the page: artifacts.owner_map), ~/.switchboard/chats.json (the
page's look at every chat: chatfile.load_index), the chat's own file (its account lines, from where the last look
stopped; its tail for what it made, only on the first prompt after a move) and their own small records in
~/.switchboard/hooks/, which are all they write: one hook at a time reads, changes and writes a chat's record (a
short lock, hooks/.records.lock; a hook that would wait longer says nothing that time, and a connector's answer note is
skipped). The hot path imports only os, sys, time, chatfile and statusline's JSON reader (and re, for a publish's
answer; fcntl, for the lock). They stay quiet when settings.json says "session_hooks": false; a helper agent hears of
no move and of no connector; a chat on its own login (CLAUDE_CONFIG_DIR elsewhere: a switch doesn't move it) gets one
line saying so at its start, and its record says "own": true from its start or first prompt (chats.own_logins).

Turning them on and off is plan, then apply, with an exact undo, the way notify_hooks does it: install_plan() and
uninstall_plan() change nothing; apply() keeps the one backup beside the file, keeps a record of what it added
(session-hooks-install.json), writes atomically and reads the file back. Only "hooks" changes, byte for byte as
notify_hooks edits it (its splicing helpers are used as they are). When the status line or the notification hooks
are in the same file, whichever of Switchboard's pieces goes last puts the file back exactly as it was before the
first came: a piece that goes first hands what it knows to the ones still there.
"""
import json
import os
import sys
import time

try:
    from . import chatfile
    from . import statusline as _sl
except ImportError:             # run by Claude Code's hook: this file's folder is on the path (command())
    import chatfile
    import statusline as _sl

MARK = '# switchboard sessions'
EVENTS = ('SessionStart', 'UserPromptSubmit', 'PostToolUse', 'PostToolUseFailure', 'PreToolUse')
ITEM_TOOLS = '^(Artifact|ArtifactComments|ArtifactData|mcp__claude_ai_.*)$'     # every claude.ai connector
CHROME_TOOLS = '^mcp__claude-in-chrome__.*$'
MATCHERS = {'PostToolUse': ITEM_TOOLS, 'PostToolUseFailure': ITEM_TOOLS, 'PreToolUse': CHROME_TOOLS}
TIMEOUTS = {'SessionStart': 10, 'UserPromptSubmit': 5, 'PostToolUse': 10, 'PostToolUseFailure': 10, 'PreToolUse': 30}
LIMITS = {'PreToolUse': 27}     # the program ends itself after this long (else 8 s), under each hook's timeout
NEED_EVERY = 600                # a need switch for Claude in Chrome is asked for at most once in 10 minutes
NEED_BUDGET = 20.0              # seconds the engine may take to answer one
KEEP = 7 * 86400                # a session's record is forgotten a week after its last use
PRUNE_EVERY = 3600
MAX_INPUT = 32 << 20
TAIL = 4 << 20                  # bytes of a chat's file read for what it made before a move, and at most on a first look
CARRY_MAX = 16 << 20            # new bytes a look reads on from the last one; past that it starts again from a tail
STEPS_KEPT = 64                 # account lines kept in a chat's record (changes only)
SAVE_EVERY = 1 << 20            # a look that read this much new is saved even when nothing else changed
KEPT = 32                       # connectors remembered per chat
CAP = 2400                      # characters of what a hook says
MOVE_NEAR = 3600                # a connector failure this soon after a chat's move gets account words by itself
SERVER = 'MCP server "claude.ai '
FAILED = (('" session expired', 'session expired'), ('" is not connected', 'not connected'),
          ('" connection closed before the tool call', 'connection closed before the tool call'))
AHEAD = 5
CHECK = 'SwitchboardCheck'
CHECKED = 'switchboard sessions ok'
RECORD = 'session-hooks-install.json'
NOT_FOUND = ('not found', 'not_found', 'no such artifact', 'no such document', 'has not been shared with',
             'not one this account can open', 'another organization', 'other organization', 'other_org',
             "another of the user's organizations", 'http 404', 'page not found')
PUBLISHED = 'published.jsonl'   # what this Mac's sessions published, one line each (published_line)
PUBLISHED_MAX = 256 << 10       # past this size it becomes published.jsonl.1 (the one before that goes)
PUBLISHED_LOCK = '.published.lock'
RECORDS_LOCK = '.records.lock'  # one hook at a time reads, changes and writes a chat's record (hooks/<chat>.json)
RECORD_WAIT = 1.0               # seconds a hook waits for it; then it says nothing this time (the next one will)
NOTE_WAIT = 0.25                # seconds an answer's note waits for it; then the note is skipped
PUBLISH_TOOLS = ('Artifact', 'mcp__claude_ai_Claude_Docs__batch', 'mcp__claude_ai_Claude_Docs__create')
LINE_MAX = 4096                 # a publish line longer than this is not written
FILE_MAX = 1024                 # characters of a local file's path kept in a line
TITLE_MAX = 200
PlanError = _sl.PlanError

# A note for this chat from Switchboard's other assistants (`switchboard back`): fixed words, times, a hop id and a
# file's path, said once with the chat's next prompt, first. The four names below repeat switchboard/providers/
# contract.py's (this hot path never imports that package); a test ties them.
PROVIDER_NOTES = True           # these hooks deliver such a note (the providers' bridge asks for this name)
NOTE_DIR = 'hooks/notes'        # <data>/hooks/notes/<session id>.json, 0600, written by the providers' bridge
NOTE_V = 1                      # {"v": 1, "text": str, "path": str, "hop": str, "made_at": float}
NOTE_TEXT_MAX = 2000            # characters of "text"
NOTE_KEEP = 7 * 86400           # a note no prompt picked up is dropped unsaid after this
NOTE_READ = 16 << 10            # bytes of a note file read at most
NOTE_MIN = 200                  # characters a note needs beside a switch notice; with less it waits one prompt


# ---------- small helpers of the hook program ----------

def hooks_dir():
    return os.path.join(_sl.data_dir(), 'hooks')


def _read(path):
    try:
        with open(path, 'rb') as f:
            rec = _sl._loads(f.read(16 << 20))
    except (OSError, ValueError, RecursionError, UnicodeDecodeError):
        return {}
    return rec if isinstance(rec, dict) else {}


def _write(path, rec):
    """Atomically, readable only by you, dated by the record's own clock (its 'at'), so pruning reads the same time."""
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    tmp = '%s.%d.tmp' % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(_sl._dumps(rec).encode('utf-8'))
        at = _sl._num(rec.get('at'))
        if at is not None:
            os.utime(tmp, (at, at))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _lock(name, wait=0.0):
    """An exclusive lock on hooks/<name> (flock, as the publish record's rotation takes its own), as a descriptor for
    _unlock, or None when another hook holds it for longer than `wait` seconds: a hook has seconds, so it never waits
    longer than that."""
    import fcntl
    try:
        os.makedirs(hooks_dir(), mode=0o700, exist_ok=True)
        fd = os.open(os.path.join(hooks_dir(), name), os.O_WRONLY | os.O_CREAT, 0o600)
    except OSError:
        return None
    end = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            if time.monotonic() >= end:
                os.close(fd)
                return None
            time.sleep(0.02)


def _unlock(fd):
    """Let a lock from _lock go (closing its descriptor does)."""
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass


def hooks_on():
    """False when the session hooks are turned off in Switchboard (settings.json "session_hooks": false): they stay
    quiet even if they could not be taken out of Claude Code's settings."""
    return _read(os.path.join(_sl.data_dir(), 'settings.json')).get('session_hooks') is not False


def owners():
    """The page's owner map (artifacts.owner_map), or {}."""
    m = _read(os.path.join(_sl.data_dir(), 'owners.json'))
    return m if m.get('v') == 1 else {}


def _s(x):
    return x if isinstance(x, str) and x else None


def _clean(text, limit=120):
    """A name or link from a file, made safe to put in a sentence: no control characters, not too long."""
    if not isinstance(text, str):
        return ''
    text = ''.join(c for c in text if c >= ' ' and c != '\x7f' and not '\x80' <= c <= '\x9f').strip()
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _when(t, now):
    """'3:02 PM', or 'Mon 3:02 PM' when it was not today."""
    lt, ln = time.localtime(t), time.localtime(now)
    hm = time.strftime('%I:%M %p', lt).lstrip('0')
    return hm if (lt.tm_year, lt.tm_yday) == (ln.tm_year, ln.tm_yday) else '%s %s' % (time.strftime('%a', lt), hm)


def _clock(env):
    """The time. Tests fix it with SWITCHBOARD_HOOKS_NOW."""
    try:
        t = float(env.get('SWITCHBOARD_HOOKS_NOW') or 'none')
        if t == t and abs(t) < 1e12:
            return t
    except ValueError:
        pass
    return time.time()


def _session_path(event):
    name = _sl._session_file_name(event.get('session_id')) if isinstance(event, dict) else None
    return os.path.join(hooks_dir(), name) if name else None


def _switched_at(m, account):
    """When Claude Code moved to `account` (the newest timeline entry for it), or None."""
    for entry in reversed(m.get('timeline') or []):
        if isinstance(entry, list) and len(entry) == 2 and entry[0] == account:
            t = _sl._num(entry[1])
            return t
    return None


def _others(m, active):
    accts = m.get('accounts') if isinstance(m.get('accounts'), dict) else {}
    return [e for e in accts if e != active and isinstance(accts[e], dict) and not accts[e].get('removed')][:6]


def _browser(m):
    return _clean(m.get('browser')) or 'Chrome'


def prune(folder, now):
    """Session records over a week old go, and so does the publish record's older half (published.jsonl.1) a week
    after its last line; the publish record itself becomes that older half once its first line is a week old. Each is
    dated by its own clock (a line's time). One look at most every hour."""
    marker = os.path.join(folder, '.pruned')
    try:
        if 0 <= now - os.stat(marker).st_mtime < PRUNE_EVERY:
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
                    record = e.name.endswith('.json') and e.name != 'chrome-need.json'
                    if not e.name.startswith('.') and (record or e.name == PUBLISHED + '.1') \
                            and e.is_file(follow_symlinks=False) \
                            and not -AHEAD <= now - e.stat(follow_symlinks=False).st_mtime <= KEEP:
                        os.unlink(e.path)
                        gone += 1
                except OSError:
                    pass
    except OSError:
        pass
    gone += _prune_notes(os.path.join(folder, os.path.basename(NOTE_DIR)), now)
    first = _first_line_time(os.path.join(folder, PUBLISHED))
    if first is not None and now - first > KEEP:
        _rotate_published(os.path.join(folder, PUBLISHED), now, old=True)
    return gone


def _prune_notes(folder, now):
    """Notes no prompt picked up within NOTE_KEEP (dated by their file's time, which the bridge sets to the note's
    own), and a note's half-written temporary file as old, go."""
    gone = 0
    try:
        with os.scandir(folder) as it:
            for e in it:
                try:
                    hidden = e.name.startswith('.')
                    ours = e.name.endswith('.tmp') if hidden else e.name.endswith('.json')
                    if ours and e.is_file(follow_symlinks=False) \
                            and not -AHEAD <= now - e.stat(follow_symlinks=False).st_mtime <= NOTE_KEEP:
                        os.unlink(e.path)
                        gone += 1
                except OSError:
                    pass
    except OSError:
        pass
    return gone


# ---------- what a session published: one line each in hooks/published.jsonl ----------

def published_path():
    """The publish record: one line per artifact a session published, created or updated (published_line)."""
    return os.path.join(hooks_dir(), PUBLISHED)


def _first_line_time(path):
    """The time of the record's first line, or None."""
    try:
        with open(path, 'rb') as f:
            head = f.read(LINE_MAX + 1).split(b'\n', 1)[0]
        rec = _sl._loads(head)
    except (OSError, ValueError, RecursionError, UnicodeDecodeError):
        return None
    return _sl._num(rec.get('t')) if isinstance(rec, dict) else None


def _rotate_published(path, now, old=False):
    """published.jsonl becomes published.jsonl.1 (the one before goes) when it is past PUBLISHED_MAX, or when `old`
    says its first line is a week old. One hook at a time: when another one holds the lock, it is left to that one.
    True when it was moved."""
    try:
        before = os.stat(path)
    except OSError:
        return False
    if before.st_size < PUBLISHED_MAX and not old:
        return False
    import fcntl
    try:
        fd = os.open(os.path.join(os.path.dirname(path), PUBLISHED_LOCK), os.O_WRONLY | os.O_CREAT, 0o600)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            now_is = os.stat(path)
        except OSError:
            return False
        if (now_is.st_dev, now_is.st_ino) != (before.st_dev, before.st_ino):
            return False                        # another hook moved it a moment ago
        os.replace(path, path + '.1')
        return True
    except OSError:
        return False
    finally:
        os.close(fd)                            # closing it lets the lock go


def _art_link(text):
    """(the link written plainly, its id) when `text` is a claude.ai artifact link, else None."""
    import re
    if not isinstance(text, str) or len(text) > 400:
        return None
    m = re.match(r'https://claude\.ai/(code/)?artifact/([A-Za-z0-9_-]{16,128})(?![A-Za-z0-9_-])', text.strip())
    return ('https://claude.ai/%sartifact/%s' % (m.group(1) or '', m.group(2)), m.group(2)) if m else None


def _local_file(path, cwd):
    """A local file's path as an answer named it, made absolute and plain (a relative one is taken from the session's
    folder), or None: never one with a control character, never longer than FILE_MAX."""
    if not isinstance(path, str) or not path or len(path) > FILE_MAX or any(c < ' ' or c == '\x7f' for c in path):
        return None
    if path.startswith('~'):
        path = os.path.expanduser(path)
    if not os.path.isabs(path):
        if not isinstance(cwd, str) or not os.path.isabs(cwd):
            return None
        path = os.path.join(cwd, path)
    path = os.path.normpath(path)
    return path if len(path) <= FILE_MAX else None


def _title(x):
    return _clean(x, TITLE_MAX) or None if isinstance(x, str) else None


def _publish_answer(resp, inp, cwd):
    """(link, id, how, file, title) from the Artifact tool's answer to a publish, or None. Claude Code hands a hook the
    tool's own answer: {url, path, artifact_id, title, updated, ...}; an answer written out as text ("Published <file>
    at <link>", "Created a new Artifact at <link>", "Updated the Artifact at <link>") is read too."""
    import re
    given = 'created' if inp.get('type_url') else 'updated' if inp.get('url') else None
    if isinstance(resp, dict):
        link = _art_link(resp.get('url'))
        if link is None and isinstance(resp.get('artifact_id'), str):
            link = _art_link('https://claude.ai/artifact/' + resp['artifact_id'])
        if link is not None:
            how = given or ('updated' if resp.get('updated') is True else 'published')
            return link[0], link[1], how, _local_file(resp.get('path'), cwd), _title(resp.get('title'))
    head = re.compile(r'(?:Published (?P<file>.+?) at|(?P<made>Created a new Artifact at)|(?P<upd>Updated the Artifact '
                      r'at)) (?P<url>https://claude\.ai/(?:code/)?artifact/[A-Za-z0-9_-]{16,128})(?![A-Za-z0-9_-])')
    texts = []
    _texts(resp, texts, [20000])
    for s in texts:
        m = head.match(s.lstrip())
        link = _art_link(m.group('url')) if m else None
        if link is not None:
            how = 'created' if m.group('made') else 'updated' if m.group('upd') else given or 'published'
            return link[0], link[1], how, _local_file(m.group('file'), cwd) if m.group('file') else None, None
    return None


def _doc_answer(resp, inp):
    """(link, id, 'doc', None, title) when a Claude Docs answer says a doc was made, else None."""
    import re
    box = inp.get('container') if isinstance(inp.get('container'), dict) else {}
    title = _title(box['create'].get('name')) if isinstance(box.get('create'), dict) else _title(inp.get('name'))
    for d in (resp, resp.get('structuredContent') if isinstance(resp, dict) else None):
        made = d.get('created') if isinstance(d, dict) else None
        frame = made.get('frame') if isinstance(made, dict) else None
        link = _art_link(frame.get('url')) if isinstance(frame, dict) else None
        if link is not None:
            return link[0], link[1], 'doc', None, title
    doc = re.compile(r'"frame":\{"slug":"[^"]+","url":"(https://claude\.ai/(?:code/)?artifact/[A-Za-z0-9_-]{16,128})"')
    texts = []
    _texts(resp, texts, [20000])
    for s in texts:
        if s.startswith('{"verdict"') and '"created":' in s[:4000]:
            m = doc.search(s)
            link = _art_link(m.group(1)) if m else None
            if link is not None:
                return link[0], link[1], 'doc', None, title
    return None


def published_line(event, now):
    """The record's line for a PostToolUse answer that published, created or updated an artifact, or made a Claude
    Doc: {'v', 't', 'id', 'url', 'how' ('published', 'updated', 'created' or 'doc'), 'file' (the local file the answer
    names, or None), 'title', 'sid' (the session)}. None for any other answer, a failed one, and an asset upload."""
    tool = event.get('tool_name')
    if tool not in PUBLISH_TOOLS:
        return None
    inp = event.get('tool_input') if isinstance(event.get('tool_input'), dict) else {}
    resp = event.get('tool_response')
    if isinstance(resp, dict) and (resp.get('isError') is True or resp.get('is_error') is True):
        return None
    if tool == 'Artifact':
        if inp.get('action') not in (None, 'publish') or inp.get('asset') is True:
            return None
        got = _publish_answer(resp, inp, event.get('cwd'))
    else:
        got = _doc_answer(resp, inp)
    if got is None:
        return None
    url, aid, how, file, title = got
    sid = event.get('session_id')
    sid = sid if isinstance(sid, str) and 0 < len(sid) <= 128 and set(sid) <= _sl.ID_CHARS else None
    return {'v': 1, 't': round(float(now), 3), 'id': aid, 'url': url, 'how': how, 'file': file, 'title': title,
            'sid': sid}


def note_published(line, now):
    """Add one line to the record: owner-only, the whole line in one write to a file opened for appending, so lines
    from sessions at the same moment never mix and a reader takes only whole lines. The file is dated by the line's
    own clock. True when written."""
    data = (_sl._dumps(line) + '\n').encode('utf-8')
    if len(data) > LINE_MAX:
        return False
    folder = hooks_dir()
    os.makedirs(folder, mode=0o700, exist_ok=True)
    path = os.path.join(folder, PUBLISHED)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    try:
        os.utime(path, (now, now))
    except OSError:
        pass
    _rotate_published(path, now)
    return True


def _note_publish(event, env, now):
    """PostToolUse: (it was a publish, a line was written). A session on its own login is not noted: Switchboard's
    accounts are not what it runs on. Never raises."""
    try:
        now = _clock(env) if now is None else now
        line = published_line(event, now)
        if line is None:
            return False, False
        if _sl.own_profile(env):
            return True, False
        wrote = note_published(line, now)
        prune(hooks_dir(), now)
        return True, wrote
    except Exception:  # noqa: BLE001 - a record that can't be written never touches the session's turn
        return False, False


# ---------- which account the chat is on: its own file (chatfile), carried on from the last look ----------

def _index_loader():
    """chats.json (the page's look at every chat: chatfile.load_index), read at most once per hook run and only when
    asked for. None when there is none that can be read."""
    box = []

    def get():
        if not box:
            box.append(chatfile.load_index(chatfile.index_path(_sl.data_dir()), loads=_sl._loads))
        return box[0]
    return get


class Look(object):
    """What a chat's own file says about its account, as far as Switchboard can name it.

    read      the chatfile.Read, carried on (its steps: every change of account it holds, oldest first)
    settled   chatfile.settle(read.steps): a flutter folded into where the chat came to rest
    org       the organization it is on now by its own file, or None when that is not known (no account line, or its
              newest change is under chatfile.SETTLE seconds old and is not where Switchboard moved Claude Code)
    since     when it came to that organization
    dirty     the chat's record (rec['cf']) changed enough to be saved"""

    __slots__ = ('read', 'settled', 'org', 'since', 'dirty', '_index', '_orgs')

    def __init__(self, read, index):
        self.read, self._index, self._orgs = read, index, None
        self.settled = chatfile.settle(read.steps)
        self.org = self.since = None
        self.dirty = False

    def orgs(self):
        """{organization id: account email} from chats.json, or {}."""
        if self._orgs is None:
            doc = self._index()
            o = doc.get('orgs') if isinstance(doc, dict) else None
            self._orgs = dict((k, _clean(v)) for k, v in o.items() if isinstance(k, str) and isinstance(v, str) and
                              _clean(v)) if isinstance(o, dict) else {}
        return self._orgs

    def name(self, org):
        """The account an organization is, or None when Switchboard can't name it."""
        return self.orgs().get(org) if isinstance(org, str) else None

    def org_of(self, email):
        return next((o for o, e in self.orgs().items() if e == email), None) if email else None

    def now_name(self):
        return self.name(self.org)

    def prev(self):
        """The organization it was on before its last move by its own file, or None."""
        return self.settled[-2][1] if self.org is not None and len(self.settled) >= 2 else None

    def known(self):
        """Its own file names at least one account Switchboard keeps."""
        return bool(self.name(self.read.owner) or any(self.name(o) for _, o in self.settled))


def look_chat(event, rec, index, now, active):
    """The chat's own account record (a Look), or None when the hook was given no file to read. It carries on from the
    chat's record (rec['cf'], the same shape as a chats.json entry, plus the file and its inode), else from chats.json's
    entry for this chat, reading only the bytes after where that look stopped; with neither (or a gap of more than
    CARRY_MAX, or another file), it reads the file's last TAIL bytes. rec['cf'] is brought up to date in place."""
    path = event.get('transcript_path')
    if not isinstance(path, str) or not os.path.isabs(path):
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    old = rec.get('cf') if isinstance(rec.get('cf'), dict) else {}
    r = None
    if old.get('file') == path and old.get('ino') == st.st_ino:
        r = _on_from(old, st.st_size)
    if r is None:
        doc = index()
        chats = doc.get('chats') if isinstance(doc, dict) else None
        e = chats.get(event.get('session_id')) if isinstance(chats, dict) else None
        if isinstance(e, dict) and e.get('file') == path:
            r = _on_from(e, st.st_size)
    if r is not None:
        r = chatfile.read(path, start=r.end, into=r, loads=_sl._loads)
    else:
        r = chatfile.tail(path, TAIL, loads=_sl._loads)
    cf = {'file': path, 'ino': st.st_ino, 'end': r.end, 'owner': r.owner,
          'steps': [[t, o] for t, o in r.steps[-STEPS_KEPT:]]}
    del r.steps[:-STEPS_KEPT]
    lk = Look(r, index)
    lk.dirty = any(cf[k] != old.get(k) for k in ('file', 'ino', 'owner', 'steps')) or \
        not isinstance(old.get('end'), int) or cf['end'] - old['end'] >= SAVE_EVERY
    rec['cf'] = cf
    if lk.settled:
        t_last, org_last = r.steps[-1]
        young = t_last is not None and now - t_last < chatfile.SETTLE
        if not young or org_last == lk.org_of(active):
            lk.since, lk.org = lk.settled[-1]
    return lk


def _on_from(saved, size):
    """A chatfile.Read carried on from a saved look (the chat's record or its chats.json entry), or None when that
    look can't be carried on (past the file's end, or more than CARRY_MAX behind it)."""
    r = chatfile.resume_from(saved)
    if 0 < r.end <= size and size - r.end <= CARRY_MAX:
        return r
    return None


def chat_story(look, active):
    """What the chat's own file says about the accounts it was on, as one sentence, or None when that is nothing
    beside the account Claude Code is on. Never names the account it started under as the one it is on."""
    started, last = look.name(look.read.owner), look.now_name()
    seen = []
    for e in [started] + [look.name(o) for _, o in look.settled] + [last]:
        if e and e not in seen:
            seen.append(e)
    others = [e for e in seen if e != active]
    if started and started != active:
        s = 'This chat started under %s' % started
        if last == started:
            s += ', and its last request was still under %s' % last
        elif last and last != active:
            s += ', and its last request was under %s' % last
        elif last:
            s += ', and is on %s now' % last
    elif last and last != active:
        s = "This chat's last request was under %s" % last
    elif others:
        s = 'This chat was on %s earlier' % ', '.join(others)
    else:
        return None
    if not others:
        return s + '.'
    if len(others) == 1:
        return '%s; what it made under %s belongs to %s.' % (s, others[0], others[0])
    return '%s; what it made under each of %s belongs to that account.' % (s, ', '.join(others))


# ---------- what each hook says ----------

CHATS_LINE = "`switchboard chats` shows what every account's chats are doing."
COST_LINE = ("The model's private reasoning from before the move stays with %s and may be missing from here on; "
             "everything said and done in this chat is still here.")


def commands_line(owner='<owner>', url='<link>'):
    return ('Find its owner: `switchboard owner %s`. Open it in the owner\'s browser profile: `switchboard open %s`. '
            'Read or change it as the owner: `switchboard run %s -- -p "<what to do with %s>"`.' % (url, url, owner, url))


def start_context(event, m, rec, own, now, look=None):
    """SessionStart: what this chat should know about Switchboard, in a few sentences. `look`: what the chat's own file
    says (look_chat), for a resumed chat's accounts; without it, Switchboard's own record of the chat."""
    if own:
        return ('Switchboard: this chat runs on its own login (CLAUDE_CONFIG_DIR is set to its own folder), so '
                'Switchboard\'s moves between accounts do not change it. An artifact or Claude Doc belongs to the '
                'account that made it; to find or open one as its owner: `switchboard owner <link>`, '
                '`switchboard open <link>`.')
    active = _s(m.get('active'))
    out = []
    if active:
        # The others are counted, never named: this text is kept in the chat's file and goes with it.
        accts = m.get('accounts') if isinstance(m.get('accounts'), dict) else {}
        n = len([e for e in accts if e != active and isinstance(accts[e], dict) and not accts[e].get('removed')])
        out.append('Switchboard moves Claude Code between the person\'s Claude accounts when one runs low (now on '
                   '%s%s).' % (_clean(active), '; %d other account%s' % (n, '' if n == 1 else 's') if n else ''))
    else:
        out.append('Switchboard moves Claude Code between the person\'s Claude accounts when one runs low.')
    out.append('An artifact or Claude Doc belongs to the account that made it. One made under another account reads '
               'here as "not found", "no such artifact" or "another organization": it is not deleted, and no new '
               'login is needed, so do not run /login or /logout and do not ask the person to log in.')
    out.append(commands_line())
    out.append(CHATS_LINE)
    if active:
        acct = (m.get('accounts') or {}).get(active) if isinstance(m.get('accounts'), dict) else None
        acct = acct if isinstance(acct, dict) else {}
        p = _s(acct.get('opens'))
        chrome = (m.get('chrome') or {}).get('profiles') if isinstance(m.get('chrome'), dict) else None
        if p:
            has = ((chrome or {}).get(p) or {}).get('extension') if isinstance(chrome, dict) else None
            out.append('Claude in Chrome follows the account Claude Code is on: for %s it is the %s profile "%s"%s.' % (
                _clean(active), _browser(m), _clean(acct.get('profile_name') or p),
                ' (the Claude extension is not in that profile yet)' if has is False else ''))
    out.append(_history(look, rec, active, 'before'))
    return ' '.join(x for x in out if x)


def _history(look, rec, active, word):
    """The accounts this chat was on besides the one Claude Code is on: by its own file when that names any account
    (chat_story), else by Switchboard's record of it ("This chat was on X before"), else None."""
    if look is not None and look.known():
        return chat_story(look, _clean(active) if active else None)
    was = _s(rec.get('account'))
    if active and was and was != active:
        return 'This chat was on %s %s; what it made then belongs to %s.' % (_clean(was), word, _clean(was))
    return None


def made_here(transcript, before, limit=3):
    """What this chat made or watches, from its file's tail: [(url, kind: 'artifact' | 'Claude Doc', watched)], newest
    last, only from before `before` (epoch seconds). Read once per move, never on every prompt."""
    return [x[:3] for x in _made(transcript, before, limit)]


def _made(transcript, before, limit=3):
    """made_here, each with the time it was made: [(url, kind, watched, time)]."""
    import re
    from datetime import datetime
    if not isinstance(transcript, str) or not os.path.isabs(transcript):
        return []
    try:
        with open(transcript, 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - TAIL))
            raw = f.read(TAIL)
    except OSError:
        return []
    art = r'(https://claude\.ai/(?:code/)?artifact/[A-Za-z0-9_-]{16,})'
    head = re.compile(r'(?:Published .+? at|Created a new Artifact at|Updated the Artifact at) ' + art)
    doc = re.compile(r'"frame":\{"slug":"[^"]+","url":"' + art + '"')
    found = {}
    for line in raw.split(b'\n'):
        if b'claude.ai/' not in line or b'tool_result' not in line:
            continue
        try:
            d = _sl._loads(line)
        except (ValueError, RecursionError, UnicodeDecodeError):
            continue
        if not isinstance(d, dict) or d.get('type') != 'user':
            continue
        try:
            t = datetime.fromisoformat(str(d.get('timestamp')).replace('Z', '+00:00')).timestamp()
        except ValueError:
            continue
        if before is not None and t >= before:
            continue
        content = (d.get('message') or {}).get('content') if isinstance(d.get('message'), dict) else None
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict) or block.get('type') != 'tool_result' or block.get('is_error'):
                continue
            text = block.get('content')
            if isinstance(text, list):
                text = '\n'.join(x.get('text', '') for x in text if isinstance(x, dict) and isinstance(x.get('text'), str))
            if not isinstance(text, str):
                continue
            mm = head.match(text)
            if mm:
                watched = 'Live subscription: arming' in text or 'Live subscription: already connected' in text
                found[mm.group(1)] = (t, 'artifact', watched or found.get(mm.group(1), (0, '', False))[2])
                continue
            if text.startswith('{"verdict"') and '"created":' in text[:4000]:
                md = doc.search(text)
                if md:
                    found[md.group(1)] = (t, 'Claude Doc', False)
    items = sorted(found.items(), key=lambda kv: kv[1][0])
    return [(url, kind, watched, t) for url, (t, kind, watched) in items][-limit:]


def _owner_of_url(m, url):
    aid = url.rstrip('/').rsplit('/', 1)[-1]
    x = (m.get('artifacts') or {}).get(aid) if isinstance(m.get('artifacts'), dict) else None
    return _s(x.get('o')) if isinstance(x, dict) else None


def switch_notice(event, m, rec, now, look=None):
    """UserPromptSubmit after a move this chat hasn't been told about: (one context line, the switch time it tells),
    or None. Two sources, one notice: Switchboard's timeline (the account it moved Claude Code to is not the one the
    chat was last told about), and the chat's own file (`look`: it came to rest on an account other than the one
    recorded at its last look, and not the one it was told about). Accounts the chat's file names come first."""
    active, was = _s(m.get('active')), _s(rec.get('account'))
    if not was:
        return None
    org = look.org if look is not None else None
    own = look.now_name() if org is not None and (org != rec.get('org') or (active and active != was)) else None
    own_news = bool(own) and org != rec.get('org') and own != _clean(was)
    sb_news = bool(active) and active != was
    if not own_news and not sb_news:
        return None
    at = _switched_at(m, active) if sb_news else None
    if sb_news and not own_news and at is not None and rec.get('told') == at:
        return None
    a, w = _clean(active), _clean(was)
    sb_when = _when(at, now) if at else 'just now'
    if own_news:
        own_when = _when(look.since, now) if look.since is not None else 'a moment ago'
        came = look.name(look.prev()) or w
    if sb_news and own_news and own == a:
        head = 'Switchboard moved Claude Code from %s to %s at %s; this chat has been on %s since %s.' % (
            came, a, sb_when, a, own_when)
        frm, to = came, a
    elif sb_news and own_news:
        head = ("This chat has been on %s since %s by its own file (it was on %s before), and Switchboard moved Claude "
                "Code to %s at %s." % (own, own_when, came, a, sb_when))
        frm, to = own, a
    elif sb_news and own == w:
        head = "Switchboard moved Claude Code from %s to %s at %s; this chat's last request was still under %s." % (
            w, a, sb_when, w)
        frm, to = w, a
    elif sb_news and own == a:
        head = 'Switchboard moved Claude Code from %s to %s at %s; this chat is on %s.' % (w, a, sb_when, a)
        frm, to = w, a
    elif sb_news and own:
        head = "Switchboard moved Claude Code from %s to %s at %s; this chat's last request was under %s." % (
            w, a, sb_when, own)
        frm, to = own, a
    elif sb_news:
        head = 'Switchboard moved Claude Code from %s to %s at %s.' % (w, a, sb_when)
        frm, to = w, a
    else:                       # own_when is the chat's first request on `own`, not the moment its login changed
        head = 'This chat has been on %s since %s by its own file (it was on %s before)%s.' % (
            own, own_when, came, '; Switchboard has Claude Code on %s' % a if a else '')
        frm, to = came, own
    text = ('%s Do not run /login and do not ask the person to log in: %s already has its login. Artifacts and Claude '
            'Docs made before then belong to %s, so from here they can read as "not found"; they are not deleted. Open '
            'one as %s: `switchboard open <link>`. Change one as %s: `switchboard run %s -- -p "<request>"`. %s' % (
                head, to, frm, frm, frm, frm, COST_LINE % frm))
    text += _made_line(event, m, look, to, w, None if look is not None and look.known() else at)
    return text, at if at is not None else rec.get('told')


def _made_line(event, m, look, to, was, before):
    """' Made in this chat under A: <link> (artifact); ...' for what the chat made under an account other than `to`:
    whose each is comes from the page's map, else from the chat's own file at the time it was made (chatfile.on_at),
    else from Switchboard's record (`was`, for what was made before the switch at `before`)."""
    groups = []
    for url, kind, watched, t in _made(event.get('transcript_path'), before):
        owner = _owner_of_url(m, url)
        if owner is None and look is not None and look.known():
            owner = look.name(chatfile.on_at(look.settled, t))
        owner = _clean(owner) if owner else was
        if owner == to:
            continue
        part = '%s (%s%s)' % (url, kind, ', watched: ask to watch it again as %s' % owner if watched else '')
        for g in groups:
            if g[0] == owner:
                g[1].append(part)
                break
        else:
            groups.append((owner, [part]))
    return ''.join(' Made in this chat under %s: %s.' % (o, '; '.join(parts)) for o, parts in groups)


def _texts(x, out, budget):
    """Every string in a tool's answer, in order, up to `budget` characters in all."""
    if budget[0] <= 0:
        return
    if isinstance(x, str):
        out.append(x[:budget[0]])
        budget[0] -= len(x)
    elif isinstance(x, dict):
        for v in x.values():
            _texts(v, out, budget)
    elif isinstance(x, list):
        for v in x[:200]:
            _texts(v, out, budget)


def says_not_found(event):
    """True when a tool's answer says the item can't be seen from this login. A tool's error is read whole; an answer
    that is not an error only in its first 400 characters, since a document's own text may contain these words."""
    name = event.get('hook_event_name')
    if name == 'PostToolUseFailure':
        text = event.get('error') if isinstance(event.get('error'), str) else ''
        window = text[:4000].lower()
    else:
        resp = event.get('tool_response')
        failed = isinstance(resp, dict) and (resp.get('isError') is True or resp.get('is_error') is True)
        out = []
        _texts(resp, out, [20000])
        window = ' '.join(out)[:4000 if failed else 400].lower()
    return any(p in window for p in NOT_FOUND)


def _item_not_found(event):
    """says_not_found, only for a call of the Artifact tools or Claude Docs that named a claude.ai artifact link: any
    other connector's "not found" (a file, an email, a web page) says nothing about another account."""
    tool = event.get('tool_name') if isinstance(event.get('tool_name'), str) else ''
    item = tool in ('Artifact', 'ArtifactComments', 'ArtifactData') or tool.startswith('mcp__claude_ai_Claude_Docs__')
    return item and bool(_links_in(event.get('tool_input'))) and says_not_found(event)


def _links_in(tool_input):
    """claude.ai artifact links named in a tool's input (its url, from_url, or a Claude Doc's container id)."""
    import re
    out, strings = [], []
    _texts(tool_input, strings, [20000])
    for s in strings:
        for mm in re.finditer(r'https://claude\.ai/(?:code/)?artifact/[A-Za-z0-9_-]{16,}', s):
            if mm.group(0) not in out:
                out.append(mm.group(0))
    box = tool_input.get('container') if isinstance(tool_input, dict) else None
    cid = box.get('id') if isinstance(box, dict) else None
    if isinstance(cid, str) and re.match(r'^[A-Za-z0-9_-]{16,}$', cid):
        url = 'https://claude.ai/artifact/' + cid
        if not any(u.endswith('/' + cid) for u in out):
            out.append(url)
    return out


def not_found_context(event, m, rec, own=False, look=None):
    """PostToolUse / PostToolUseFailure on an item tool that said "not found": who owns it and the commands. None
    when the answer says nothing like that, or the owner is the account Claude Code is on (then it is not a switch)."""
    if not says_not_found(event):
        return None
    tool = event.get('tool_name') if isinstance(event.get('tool_name'), str) else ''
    item = 'Claude Doc' if 'Claude_Docs' in tool else 'artifact'
    links = _links_in(event.get('tool_input'))
    url = links[0] if links else '<link>'
    active = _s(m.get('active'))
    owner = _owner_of_url(m, url) if links else None
    if own:
        return ('Switchboard: this chat runs on its own login (CLAUDE_CONFIG_DIR), and this %s belongs to %s. It '
                'is not deleted, and no new login is needed: do not run /login or /logout. %s' % (
                    item, _clean(owner) if owner else 'another account', commands_line(owner or '<owner>', url)))
    if owner and active and owner == active:
        return None
    if owner:
        acct = (m.get('accounts') or {}).get(owner) if isinstance(m.get('accounts'), dict) else None
        acct = acct if isinstance(acct, dict) else {}
        where = (' (it opens in the %s profile "%s")' % (_browser(m), _clean(acct.get('profile_name') or acct.get('opens')))
                 if _s(acct.get('opens')) else '')
        return ('Switchboard: this %s belongs to %s, and Claude Code is on %s now (Switchboard moves Claude Code '
                'between the person\'s accounts), so this login can\'t see it. It is not deleted, and no new login is '
                'needed: do not run /login or /logout. Open it as %s%s: `switchboard open %s`. Read or change it as '
                '%s: `switchboard run %s -- -p "<what to do with %s>"`.' % (
                    item, _clean(owner), _clean(active) or 'another account', _clean(owner), where, url, _clean(owner),
                    _clean(owner), url))
    started = _history(look, rec, active, 'earlier')
    return ('Switchboard: Claude Code moves between the person\'s Claude accounts%s. %s %s made under another of them '
            'reads as "not found" from here. That does not mean it was deleted, and no new login is needed, so do not '
            'run /login or /logout.%s %s' % (' (now on %s)' % _clean(active) if active else '',
                                              'An' if item == 'artifact' else 'A', item,
                                              ' ' + started if started else '', commands_line(url=url)))


# ---------- a claude.ai connector that failed after a move ----------

def _same_name(a, b):
    def bare(s):
        return ''.join(c for c in s.lower() if c.isalnum())
    return bare(a) == bare(b)


def connector_failure(event):
    """(the connector's name as Claude Code says it, what happened, the connector as chatfile names it) when a
    claude.ai connector's call failed in one of Claude Code's own words (FAILED: `MCP server "claude.ai Google Drive"
    session expired`, `... is not connected`, `... connection closed before the tool call ...`), else None. A tool's
    error is read in its first 4000 characters; an answer that is not marked as an error only in its first 400 (the
    connector's own content may quote the words), and the name must be the tool's own connector."""
    key = chatfile.connector(event.get('tool_name'))
    if key is None:
        return None
    if event.get('hook_event_name') == 'PostToolUseFailure':
        window = event.get('error')[:4000] if isinstance(event.get('error'), str) else ''
    else:
        resp = event.get('tool_response')
        failed = isinstance(resp, dict) and (resp.get('isError') is True or resp.get('is_error') is True)
        out = []
        _texts(resp, out, [20000])
        window = ' '.join(out)[:4000 if failed else 400]
    i = window.find(SERVER)
    while i >= 0:
        j = window.find('"', i + len(SERVER))
        name = window[i + len(SERVER):j] if j > 0 else ''
        if 0 < len(name) <= 120 and _same_name(name, key):
            for words, what in FAILED:
                if window.startswith(words, j):
                    return _clean(name, 80), what, key
        i = window.find(SERVER, i + 1)
    return None


def _note_answer(event, env, now):
    """A claude.ai connector answered in this chat: when, in the chat's own record (rec['ok'][connector]), without a
    word. A chat on its own login is not noted (a move doesn't move it). Read, changed and written under the records'
    lock, so it never undoes another hook's write; when that lock is held past NOTE_WAIT, the note is skipped (the
    one write a hook may drop). Never raises."""
    lock = None
    try:
        key = chatfile.connector(event.get('tool_name'))
        path = _session_path(event)
        if key is None or path is None or _sl.own_profile(env):
            return
        now = _clock(env) if now is None else now
        lock = _lock(RECORDS_LOCK, NOTE_WAIT)
        if lock is None:
            return
        rec = _read(path)
        ok = rec.get('ok') if isinstance(rec.get('ok'), dict) else {}
        ok[key] = now
        rec.update(ok=_newest(ok), v=1, at=now)
        _write(path, rec)
    except Exception:  # noqa: BLE001 - a note that can't be written never touches the chat's turn
        pass
    finally:
        _unlock(lock)


def _newest(d, n=KEPT):
    """The `n` entries with the newest times (a chat's connectors)."""
    if len(d) <= n:
        return d
    return dict(sorted(d.items(), key=lambda kv: _sl._num(kv[1]) or 0)[-n:])


def _before(m, account):
    """The account Claude Code was on before its newest move to `account`, by the page's timeline, or None."""
    tl = [e for e in m.get('timeline') or [] if isinstance(e, list) and len(e) == 2]
    for i in range(len(tl) - 1, -1, -1):
        if tl[i][0] == account:
            return _s(tl[i - 1][0]) if i > 0 else None
    return None


def last_move(m, rec, look):
    """The chat's last move: (time, from, to, by: 'own' | 'switchboard', where its last request went when that is not
    `to`), or None when no move is known. Two sources, the newer wins: the chat's own file, when it shows a move between
    accounts Switchboard can name; and Switchboard's timeline, when it moved Claude Code after this chat began (the
    chat's record has a start). The same move seen by both is the chat's own (its first request there comes later)."""
    own = None
    if look is not None and look.org is not None and len(look.settled) >= 2:
        to = look.name(look.org)
        if to and look.since is not None:
            own = (look.since, look.name(look.prev()) or 'another account', to, 'own', None)
    active = _s(m.get('active'))
    at = _switched_at(m, active) if active else None
    started = _sl._num(rec.get('started'))
    if at is None or started is None or at <= started or (own is not None and own[0] >= at - chatfile.SETTLE):
        return own
    was = _s(rec.get('account'))
    last = look.now_name() if look is not None and look.org is not None else None
    frm = _clean(last if last and last != _clean(active) else
                 was if was and was != active else _before(m, active) or 'another account')
    return at, frm, _clean(active), 'switchboard', last if last and last != _clean(active) else None


def _whole_record(doc):
    """(since, at) when chats.json was written by a look that read every chat of its window whole ("done"): its
    "connectors" then hold every answer from `since` to `at`, so a connector missing there did not answer on this Mac
    in that window. None when that is not so, or not known."""
    if not isinstance(doc, dict) or doc.get('done') is not True:
        return None
    since, at = _sl._num(doc.get('since')), _sl._num(doc.get('at'))
    if since is None or at is None or at - since < 3600:
        return None
    return since, at


def _window(since, at):
    """'the last 7 days' for a record's window."""
    days = int((at - since) / 86400.0 + 1e-6)
    if days >= 1:
        return 'the last %d day%s' % (days, '' if days == 1 else 's')
    hours = int((at - since) / 3600.0 + 1e-6)
    return 'the last %d hour%s' % (hours, '' if hours == 1 else 's')


def _as_at(look, t):
    """The account this chat was on at time `t` by its own file, when the account lines read reach back to then
    (a read that began in a tail says nothing about before its first line), else None."""
    if look is None or t is None or not look.settled or look.settled[0][0] is None or t < look.settled[0][0]:
        return None
    return look.name(chatfile.on_at(look.settled, t))


def connector_context(event, m, rec, now, failed, look, index):
    """A claude.ai connector failed (connector_failure): what the chat should know, once per connector per move, or
    None. Account words are said only after a move, and only when one of these is so: (a) the connector answered in
    this chat before its last move and not since; (b) chats.json, from a look that read every chat of its window,
    shows it answered under another account in that window and never under the one this chat is on; (c) the move was
    within MOVE_NEAR seconds. Otherwise nothing: Claude Code's own error stands (an expiry on the same account, or a
    failure long after a move with nothing known about it). Each sentence is a fact the hook has, never a cause."""
    name, what, key = failed
    move = last_move(m, rec, look)
    if move is None:
        return None
    t, frm, to, by, last = move
    ok = rec.get('ok') if isinstance(rec.get('ok'), dict) else {}
    answered = _sl._num(ok.get(key))
    if answered is not None and answered >= t:
        return None
    told = rec.get('ctold') if isinstance(rec.get('ctold'), dict) else {}
    if told.get(key) == t:
        return None
    doc = index()
    orgs = look.orgs() if look is not None else \
        dict((k, _clean(v)) for k, v in ((doc or {}).get('orgs') or {}).items() if isinstance(v, str)) \
        if isinstance((doc or {}).get('orgs'), dict) else {}
    conns = doc.get('connectors') if isinstance(doc, dict) and isinstance(doc.get('connectors'), dict) else None
    to_org = next((o for o, e in orgs.items() if e == to), None)
    whole = _whole_record(doc) if conns is not None else None
    here = None                     # when `to` last had it answer on this Mac; False: not in a window read whole
    if conns is not None and to_org is not None:
        d = conns.get(to_org)
        here = _sl._num(d.get(key)) if isinstance(d, dict) else None
        if here is None and whole is not None:
            here = False
    best = None                     # its newest answer on this Mac under another account the record can name
    for org, d in (conns or {}).items():
        tt = _sl._num(d.get(key)) if isinstance(d, dict) else None
        e = orgs.get(org)
        if tt is not None and e and e != to and (best is None or tt > best[0]):
            best = (tt, e)
    mine = _as_at(look, answered)   # the account this chat was on when it last answered here, by its own file
    if not (answered is not None
            or (here is False and best is not None and best[0] >= whole[0])
            or 0 <= now - t <= MOVE_NEAR):
        return None
    rec['ctold'] = _newest(dict(told, **{key: t}))
    out = [('Switchboard: %s did not answer here (%s).' % (name, what), False)]
    if answered is not None:
        out.append(('%s last answered in this chat at %s%s.' % (name, _when(answered, now),
                                                               ', as %s' % mine if mine else ''), True))
    if best is not None and not (mine == best[1] and best[0] - answered < 60):
        out.append(('%s last answered under %s at %s.' % (name, best[1], _when(best[0], now)), True))
    if by == 'own':                 # its time is the chat's own first request on `to`
        out.append(('This chat has been on %s since %s, and was on %s before.' % (to, _when(t, now), frm), False))
    else:                           # Switchboard's own switch time
        out.append(('Switchboard moved Claude Code from %s to %s at %s%s.' % (
            frm, to, _when(t, now),
            " (this chat's last request was %sunder %s)" % ('still ' if last == frm else '', last) if last else ''),
            False))
    if here:
        said = '%s has had %s answer on this Mac (last at %s), so the person can reconnect it with /mcp.' % (
            to, name, _when(here, now))
    elif here is False:
        said = ('%s has not had %s answer on this Mac in %s (as of %s); if %s has it on claude.ai, the person can '
                'reconnect it with /mcp.' % (to, name, _window(*whole), _when(whole[1], now), to))
    else:
        said = 'Whether %s has %s is not known here; if it does, the person can reconnect it with /mcp.' % (to, name)
    out.append(('claude.ai connectors are added per account on claude.ai. ' + said, False))
    use = best[1] if best is not None else mine if mine and mine != to else None
    if use:
        out.append(('To use it as %s now, without moving anything: `switchboard run %s -- -p "<request>"`.' % (
            use, use), False))
    out.append(('No new login is needed: do not run /login or /logout.', False))
    while len(' '.join(s for s, _ in out)) > CAP and any(extra for _, extra in out):
        out.remove(next(x for x in out if x[1]))      # past the cap, where it last answered goes first
    return ' '.join(s for s, _ in out)


def _need_file():
    return os.path.join(hooks_dir(), 'chrome-need.json')


def _need_in_process(accounts, followers, why_of):
    """Ask the engine for a need switch to the first follower that has room, in this process: (account, result) or
    (None, None). Run by the need switch's own process (need_command), never by the hook itself."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        parent = os.path.dirname(here)
        if parent not in sys.path:
            sys.path.append(parent)
        from switchboard.accounts import api
        for e in followers:
            acct = accounts.get(e) if isinstance(accounts.get(e), dict) else {}
            aid = _s(acct.get('id'))
            if not aid:
                try:
                    aid = api.find(e).id
                except Exception:  # noqa: BLE001 - an account the engine doesn't hold is skipped
                    continue
            r = api.request_need_switch(aid, why_of(e))
            if r is not None and getattr(r, 'ok', False):
                return e, r
    except Exception:  # noqa: BLE001 - the engine can't be asked: the session is told plainly instead
        pass
    return None, None


# The need switch's own process: runs _need_in_process and answers in one JSON line ({"went": account or null}).
NEED_CHILD = ('import json, sys\n'
              'sys.path.insert(0, sys.argv[1])\n'
              'from switchboard import session_hooks as h\n'
              'a = json.loads(sys.argv[2])\n'
              'went = h._need_in_process(a["accounts"], a["followers"], lambda e: a["why"].get(e) or "")[0]\n'
              'try:\n'
              '    sys.stdout.write(json.dumps({"went": went}) + "\\n")\n'
              '    sys.stdout.flush()\n'
              'except Exception:\n'
              '    pass\n')


def need_command(parent, accounts, followers, why_of):
    """The command line of the need switch's own process: this program's Python, isolated, the folder that holds the
    switchboard package, and what to ask (account ids and reasons, no secret)."""
    ask = {'accounts': dict((e, {'id': _s((accounts.get(e) if isinstance(accounts.get(e), dict) else {}).get('id'))})
                            for e in followers),
           'followers': list(followers), 'why': dict((e, why_of(e)) for e in followers)}
    return [sys.executable, '-I', '-S', '-B', '-c', NEED_CHILD, parent, json.dumps(ask)]   # as the hook itself runs


def _ask_need_switch(accounts, followers, why_of, budget=NEED_BUDGET, command=None):
    """Ask the engine for a need switch to the first follower that has room. (account, True) or (None, None).

    The switch runs in a process of its own, in a session of its own: this hook ends (or Claude Code ends it) when its
    time is up, and a switch cut off part-way would leave Claude Code half moved until the engine's journal is next
    settled. The hook waits up to `budget` seconds for the answer; past that the switch goes on by itself."""
    import subprocess
    if command is None:
        command = need_command(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), accounts, followers,
                               why_of)
    try:
        proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                close_fds=True, start_new_session=True)
    except OSError:
        return None, None
    try:
        out, _ = proc.communicate(timeout=budget)
    except subprocess.TimeoutExpired:
        return None, None                       # still switching: it finishes on its own
    lines = (out or b'').decode('utf-8', 'replace').strip().splitlines()
    try:
        got = json.loads(lines[-1]) if lines else {}
    except ValueError:
        got = {}
    went = _s(got.get('went')) if isinstance(got, dict) else None
    return (went, True) if went else (None, None)


def chrome_context(event, m, rec, own, now, ask=None):
    """PreToolUse on Claude in Chrome: None while the extension is in the profile of the account Claude Code is on (or
    nothing is known), else what Switchboard did about it and what to do, said once per state per session."""
    if own:
        return None
    active = _s(m.get('active'))
    accts = m.get('accounts') if isinstance(m.get('accounts'), dict) else {}
    chrome = m.get('chrome') if isinstance(m.get('chrome'), dict) else {}
    profiles = chrome.get('profiles') if isinstance(chrome.get('profiles'), dict) else {}
    if not active or not profiles:
        return None
    mine = accts.get(active) if isinstance(accts.get(active), dict) else {}
    myp = _s(mine.get('opens'))
    app = _browser(m)
    if chrome.get('host') is False:
        key = 'host'
        text = ('Claude in Chrome can\'t reach Claude Code on this Mac yet: Claude Code\'s link to %s is not set up. '
                'The person can run /chrome in Claude Code once to set it up. Do not run /login or /logout.' % app)
    elif myp and (profiles.get(myp) or {}).get('extension') is True:
        return None
    else:
        followers = []
        for p, v in sorted(profiles.items()):
            e = _s(v.get('account')) if isinstance(v, dict) else None
            if isinstance(v, dict) and v.get('extension') is True and e and e != active and e not in followers \
                    and (accts.get(e) or {}).get('opens') == p:
                followers.append(e)
        key = '%s>%s' % (active, ','.join(followers))
        if rec.get('chrome') == key and 0 <= now - (_sl._num(rec.get('chrome_at')) or 0) < NEED_EVERY:
            return None
        went = None
        auto = m.get('auto') is True            # a need switch only while automatic switching is on (the page's map)
        if followers and auto:
            need = _read(_need_file())
            last = _sl._num(need.get('at'))
            if last is None or not 0 <= now - last < NEED_EVERY:
                why_of = lambda e: 'Claude in Chrome follows %s (the extension is in its profile)' % e  # noqa: E731
                went, _ = (ask or _ask_need_switch)(accts, followers, why_of)
                try:
                    _write(_need_file(), {'v': 1, 'at': now, 'to': went or followers[0], 'went': bool(went)})
                except OSError:
                    pass
            elif need.get('went') and need.get('to') == active:
                return None                     # it went a moment ago; the login reaches open sessions within ~30 s
            elif need.get('went') and need.get('to') in followers:
                went = need['to']               # another session asked a moment ago; the page's map hasn't caught up
        store = _s(chrome.get('store'))         # the extension's Web Store page, as the page names it (browser.py)
        plain = bool(myp) and len(myp) <= 40 and all(c.isascii() and (c.isalnum() or c in ' _-') for c in myp)
        if plain and store and store.startswith('https://') and not set(store) & set('"`$\\ \'\n'):
            add = ('Add the extension to %s\'s profile: `open -na "%s" --args --profile-directory="%s" %s` (the '
                   'person presses Add to Chrome there), and keep %s open in that profile.' % (
                       _clean(active), 'Google Chrome' if app == 'Chrome' else app, myp, store, app))
        elif myp:
            add = ('Add the Claude extension to %s\'s %s profile "%s" (Add to this profile, on Switchboard\'s page), '
                   'and keep %s open in that profile.' % (_clean(active), app, myp, app))
        else:
            add = ('No %s profile is signed in as %s yet: the person can press Set up for it on Switchboard\'s page.'
                   % (app, _clean(active)))
        if went:
            p = next((q for q, v in profiles.items() if isinstance(v, dict) and v.get('account') == went), '')
            theirs = accts.get(went) if isinstance(accts.get(went), dict) else {}
            text = ('Switchboard moved Claude Code to %s so Claude in Chrome can connect: the Claude extension is in '
                    'the %s profile "%s", which is signed in to claude.ai as %s. Open chats pick up the new login '
                    'within about 30 seconds; if a browser tool says it is not connected, wait a moment and try it '
                    'again. Do not run /login or /logout.' % (_clean(went), app, _clean(theirs.get('profile_name') or p),
                                                              _clean(went)))
        elif followers and not auto:
            text = ('Claude in Chrome connects only through a %s profile signed in to claude.ai as the account Claude '
                    'Code is on (now %s). The extension is in the profile of %s. Automatic switching is off, so '
                    'Switchboard does not move Claude Code there by itself: the person can switch on Switchboard\'s '
                    'page, or run `switchboard switch %s`. Until then the browser tools may say they are not connected. '
                    'Do not run /login or /logout. %s' % (app, _clean(active), ', '.join(_clean(e) for e in followers),
                                                          _clean(followers[0]), add))
        elif followers:
            text = ('Claude in Chrome connects only through a %s profile signed in to claude.ai as the account Claude '
                    'Code is on (now %s). The extension is in the profile of %s, and Switchboard could not move Claude '
                    'Code there just now (it moves only to an account with room, at most once in 10 minutes), so the '
                    'browser tools may say they are not connected. Do not run /login or /logout. %s' % (
                        app, _clean(active), ', '.join(_clean(e) for e in followers), add))
        else:
            text = ('Claude in Chrome connects only through a %s profile signed in to claude.ai as the account Claude '
                    'Code is on (now %s), with the Claude extension in it. Do not run /login or /logout. %s' % (
                        app, _clean(active), add))
    if rec.get('chrome') == key and 0 <= now - (_sl._num(rec.get('chrome_at')) or 0) < NEED_EVERY:
        return None
    rec['chrome'], rec['chrome_at'] = key, now
    return text


def emit(name, text, out=None):
    out = out or sys.stdout
    out.write(_sl._dumps({'hookSpecificOutput': {'hookEventName': name, 'additionalContext': text[:CAP]}}) + '\n')
    out.flush()


def _note_path(event):
    """This chat's note file, named through the same check as its record (so ../x or any other shape is None)."""
    name = _sl._session_file_name(event.get('session_id')) if isinstance(event, dict) else None
    return os.path.join(_sl.data_dir(), NOTE_DIR, name) if name else None


def _note_for(event, now):
    """(file, words) of the note waiting for this chat, or None. A note past NOTE_KEEP (or dated ahead of the clock)
    is removed unsaid; a file that is not a whole note of version NOTE_V is left for prune and says nothing."""
    path = _note_path(event)
    if not path:
        return None
    try:
        with open(path, 'rb') as f:
            raw = f.read(NOTE_READ + 1)
        doc = _sl._loads(raw) if len(raw) <= NOTE_READ else None
    except (OSError, ValueError, RecursionError, UnicodeDecodeError):
        return None
    if not isinstance(doc, dict) or doc.get('v') != NOTE_V:
        return None
    made, text = _sl._num(doc.get('made_at')), doc.get('text')
    if made is None or not isinstance(text, str):
        return None
    if not -AHEAD <= now - made <= NOTE_KEEP:
        _drop_note(path)
        return None
    text = _clean(text[:NOTE_TEXT_MAX], NOTE_TEXT_MAX)
    return (path, text) if text else None


def _drop_note(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _cut_words(text, room):
    """text in at most `room` characters, cut at a word with '…' when it is longer."""
    if len(text) <= room:
        return text
    cut = text[:max(room - 1, 0)]
    space = cut.rfind(' ')
    if space > room // 2:
        cut = cut[:space]
    return cut.rstrip() + '…'


def with_note(note, notice=None):
    """What a prompt's hook says when a note waits: the note first, then the switch notice. The notice is built
    first and kept whole; the note gets what is left of CAP, cut at a word. None when that is under NOTE_MIN: the
    note then waits for the next prompt, which the notice (said once) no longer shares."""
    if not notice:
        return _cut_words(note, CAP)
    room = CAP - len(notice) - 2
    if room < NOTE_MIN:
        return None
    return _cut_words(note, room) + '\n\n' + notice


def hook(stdin=None, env=None, now=None, out=None, ask=None):
    """What Claude Code runs: one hook input on stdin. Prints at most one JSON answer and returns 0, whatever
    happens."""
    env = os.environ if env is None else env
    lock = None
    try:
        raw = (stdin if stdin is not None else sys.stdin.buffer).read(MAX_INPUT)
        event = _sl._loads(raw) if raw and raw.strip() else None
        if not isinstance(event, dict):
            return 0
        name = event.get('hook_event_name')
        if name == CHECK:
            (out or sys.stdout).write(CHECKED + '\n')
            return 0
        if name not in EVENTS or not hooks_on():
            return 0
        tool = event.get('tool_name')
        connector = chatfile.connector(tool) is not None
        if name == 'PostToolUse' and tool in PUBLISH_TOOLS and _note_publish(event, env, now)[0]:
            if connector:
                _note_answer(event, env, now)
            return 0                            # a publish: noted without a word (the page reads it within seconds)
        helper = event.get('agent_id') is not None      # a helper agent: told about items and Chrome, not moves
        if helper and name in ('SessionStart', 'UserPromptSubmit'):
            return 0
        failed = None
        if name in ('PostToolUse', 'PostToolUseFailure'):
            failed = connector_failure(event) if connector else None
            if failed is None and connector and name == 'PostToolUse':
                _note_answer(event, env, now)   # it answered here: when, without a word
            if failed is not None and (helper or _sl.own_profile(env)):
                failed = None                   # no word about connectors to a helper or a chat on its own login
            if failed is None and not _item_not_found(event):
                return 0                        # the usual case: nothing more is read
        now = _clock(env) if now is None else now
        own = _sl.own_profile(env)
        m = owners()
        path = _session_path(event)
        if path and name != 'PreToolUse':
            lock = _lock(RECORDS_LOCK, RECORD_WAIT)     # the chat's record is read, changed and written alone
            if lock is None and name != 'SessionStart':
                return 0                                # nothing said now, so nothing is told twice: the next one will
        rec = _read(path) if path else {}
        # A provider's note, for any chat but a helper (a note is not about a login move): read under the records
        # lock, said first, removed once said.
        note = _note_for(event, now) if name == 'UserPromptSubmit' and lock is not None else None
        index = _index_loader()
        active = _s(m.get('active'))
        look = None if own or name == 'PreToolUse' else look_chat(event, rec, index, now, active)
        text, changed = None, bool(look is not None and look.dirty)
        if name in ('SessionStart', 'UserPromptSubmit') and own and rec.get('own') is not True:
            rec.update(own=True, session_id=event.get('session_id'))    # where `switchboard chats` looks (own_logins)
            changed = True
        elif name in ('SessionStart', 'UserPromptSubmit') and not own and 'own' in rec:
            del rec['own']                              # opened again where it follows Claude Code's login
            changed = True
        if name == 'SessionStart':
            text = start_context(event, m, rec, own, now, look)
            if not own and active:
                rec.update(account=active, told=_switched_at(m, active))
            rec.setdefault('started', now)
            changed = True
        elif name == 'UserPromptSubmit' and not own:    # a chat on its own login hears of no move
            if not rec.get('account'):
                if active:
                    rec.update(account=active, told=_switched_at(m, active), started=now)
                    changed = True
            else:
                said = switch_notice(event, m, rec, now, look)
                if said:
                    text, at = said
                    rec.update(account=active or rec['account'], told=at)
                    changed = True
        elif name in ('PostToolUse', 'PostToolUseFailure'):
            if failed is not None:
                before = dict(rec.get('ctold') or {}) if isinstance(rec.get('ctold'), dict) else {}
                text = connector_context(event, m, rec, now, failed, look, index)
                changed = changed or rec.get('ctold') != before
            if text is None and _item_not_found(event):
                text = not_found_context(event, m, rec, own, look)
                move = 'own' if own else '%s %s' % (active, _switched_at(m, active))
                told = rec.get('ntold') if isinstance(rec.get('ntold'), dict) and rec.get('nmove') == move else {}
                url = _links_in(event.get('tool_input'))[0]
                if text and url in told:
                    text = None                 # said once for this link since the last move
                elif text:
                    told[url] = now
                    rec.update(ntold=_newest(told), nmove=move)
                    changed = True
        if note is not None:
            said = with_note(note[1], text)
            if said is None:
                note = None                     # no room beside the notice now: it waits for the next prompt
            else:
                text = said
        if name in ('SessionStart', 'UserPromptSubmit') and look is not None and look.org is not None \
                and rec.get('org') != look.org:
            rec['org'] = look.org           # where the chat was at its last look (a tool's hook never moves this on:
            changed = True                  # the next prompt's notice would miss a move it hasn't told)
        if name == 'PreToolUse':
            before = dict(rec)
            text = chrome_context(event, m, rec, own, now, ask=ask)    # may wait on the engine: not under the lock
            changed = rec != before
            if path and changed:
                lock = _lock(RECORDS_LOCK, RECORD_WAIT)
                if lock is None:
                    return 0                    # not noted, so not said either: never said twice
                fresh = _read(path)             # its own two keys onto the record as it is now
                fresh.update(chrome=rec.get('chrome'), chrome_at=rec.get('chrome_at'))
                rec = fresh
        wrote = bool(path and changed and lock is not None)
        if wrote:
            rec['v'], rec['at'] = 1, now
            try:
                _write(path, rec)
            except OSError:
                pass
        if note is not None:
            emit(name, text, out)               # said, then removed, while the records lock is held: said once
            text = None
            _drop_note(note[0])
        _unlock(lock)
        lock = None
        if wrote:
            prune(hooks_dir(), now)
        if text:
            emit(name, text, out)
    except BaseException:
        pass
    finally:
        _unlock(lock)
    return 0


def run():
    """The command's entry point: ends itself before its hook's timeout, then hook()."""
    try:
        try:
            import _signal as sig
        except ImportError:
            import signal as sig
        sig.alarm(8)
    except Exception:
        sig = None
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT)
    except Exception:
        raw = b''
    if sig is not None and b'"PreToolUse"' in raw:
        try:
            sig.alarm(LIMITS['PreToolUse'])
        except Exception:
            pass

    class _Once(object):
        def read(self, n=-1):
            return raw
    hook(stdin=_Once())


# ---------- turning them on and off ----------

def _nh():
    try:
        from . import notify_hooks
    except ImportError:
        import notify_hooks
    return notify_hooks


def settings_path():
    """Claude Code's user settings file: $CLAUDE_CONFIG_DIR/settings.json, else ~/.claude/settings.json."""
    return _sl.settings_path()


def record_path():
    """What Switchboard added to each settings file, so taking the hooks out removes exactly that."""
    return os.path.join(_sl.data_dir(), RECORD)


def command(python=None, folder=None):
    """The shell command each hook runs: this Python (its real path), this file's folder at the end of the path, -I
    and -S as for the notification hooks; a moved install does nothing; `|| true` keeps the exit 0 when the Python is
    gone. The trailing comment marks it as Switchboard's."""
    import json
    import shlex
    python = python or sys.executable or '/usr/bin/python3'
    folder = folder or os.path.dirname(os.path.abspath(__file__))
    code = ('import sys; D = %s; sys.path[:] = [p for p in sys.path if p] + [D]; import os; '
            'os.path.isfile(os.path.join(D, "session_hooks.py")) and __import__("session_hooks").run()'
            % json.dumps(folder))
    return '%s -I -S -c %s || true %s' % (shlex.quote(python), shlex.quote(code), MARK)


def wanted(command_line):
    """{event: the matcher group Switchboard adds for it}."""
    out = {}
    for e in EVENTS:
        g = {'hooks': [{'type': 'command', 'command': command_line, 'timeout': TIMEOUTS[e]}]}
        if e in MATCHERS:
            g = dict({'matcher': MATCHERS[e]}, **g)
        out[e] = g
    return out


def is_ours(handler):
    """True for a hook handler whose command ends with this file's mark."""
    return isinstance(handler, dict) and isinstance(handler.get('command'), str) \
        and handler['command'].rstrip().endswith(MARK)


def precompile():
    """Bytecode for the two files the hooks load (statusline.precompile)."""
    folder = os.path.dirname(os.path.abspath(__file__))
    return [_sl.precompile(script=os.path.join(folder, name)) for name in ('session_hooks.py', 'statusline.py')]


def verify(command_line, timeout=30):
    """Run the hook command once, as Claude Code runs it (through a shell), with the check input Claude Code never
    sends. None when it answers, else why not, in one line."""
    import json
    import shlex
    import subprocess
    try:
        r = subprocess.run(['/bin/sh', '-c', command_line], input=json.dumps({'hook_event_name': CHECK}).encode(),
                           capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "The session hooks didn't run here: they took over %d s. Nothing was changed." % timeout
    except OSError as e:
        return "The session hooks didn't run here (%s). Nothing was changed." % (e.strerror or e)
    if r.stdout.decode('utf-8', 'replace').strip() == CHECKED:
        return None
    try:
        python = shlex.split(command_line)[0]
    except (ValueError, IndexError):
        python = ''
    if not (os.path.isfile(python) and os.access(python, os.X_OK)):
        return "The session hooks didn't run here: the Python they run (%s) is not there. Nothing was changed." % python
    said = [x for x in r.stderr.decode('utf-8', 'replace').splitlines() if x.strip()]
    return "The session hooks didn't run here (%s). Nothing was changed." % (said[-1].strip()[:160] if said else
                                                                             'no answer')


def turn(on, python=None):
    """Put the hooks in (on) or take them out. On: plan, run the command once (verify), apply, read back; raises
    PlanError with one plain line when a step can't be done (nothing was changed then). Off: never raises for Claude
    Code's file; what could not be taken out is said in 'left' (the hooks stay quiet anyway once settings.json says
    "session_hooks": false), and every other settings file the record names is cleared too ('also'). Returns
    {'changed', 'backup', 'message', 'off_by', 'left', 'also'}."""
    path = settings_path()
    left, also = None, []
    if on:
        cmd = command(python=python)
        plan = install_plan(path, command_line=cmd)
        if not plan.get('ok'):
            raise PlanError(plan['message'])
        if plan.get('change'):
            why = verify(cmd)
            if why:
                raise PlanError(why)
        r = apply(plan)
        precompile()
    else:
        r, left = _off(path)
        here = os.path.realpath(path)
        for target in sorted(t for t in _records() if t != here):
            o, why = _off(target)
            if o.get('changed'):
                also.append(o['path'])
                r = dict(r, changed=True, backup=r.get('backup') or o.get('backup'))
            if why:
                left = '; '.join(x for x in (left, why) if x)
    return dict(r, off_by=status(path)['off_by'], left=left, also=also)


def _off(path):
    plan = uninstall_plan(path)
    r = {'changed': False, 'path': plan.get('path'), 'backup': None, 'message': plan.get('message')}
    if not plan.get('ok'):
        return r, (plan['message'] if _sl.record_names(record_path(), plan['target']) else None)
    if not plan.get('change'):
        try:
            _drop_record(plan['target'])
        except OSError:
            pass
        return r, None
    try:
        return apply(plan), None
    except PlanError as e:
        why = str(e)
    except Exception as e:  # noqa: BLE001 - off never raises: what was left is said instead
        why = "Switchboard couldn't take its session hooks out of %s (%s)." % (path, e)
    return r, (why if status(path)['in'] is not False else None)


def status(path=None):
    """{'in': True when all its hooks are in the settings file, False when not, None when the file can't be read;
    'works': they are in and the Python and folder they name are there; 'current': every group of Switchboard's own
    has today's matcher (an install by an older version may not: install_plan or upgrade() brings it up to date);
    'off_by': 'disableAllHooks' or 'allowManagedHooksOnly' when Claude Code runs no hook of yours, else None;
    'path'}."""
    f = _settings(path or settings_path())
    off = _nh()._off_by(None if f.get('error') else f['obj'])
    if f.get('error'):
        return {'in': None, 'works': False, 'current': None, 'off_by': off, 'path': f['path']}
    found = _found(f['obj'])
    whole = all(found.get(e) for e in EVENTS)
    works = whole and all(_runs(c) for e in EVENTS for c in found[e])
    return {'in': whole, 'works': works, 'current': _matchers_current(f['obj']), 'off_by': off, 'path': f['path']}


def hooks_run_from_here(path=None):
    """True when every one of Switchboard's hooks in the settings file names this file's own folder, so the hooks
    Claude Code runs are this copy's. False when any names another folder: another copy of Switchboard is the one
    installed (a build run from its own folder beside it would leave notes its hooks never read). None when the file
    cannot be read or holds none of Switchboard's hooks."""
    import json
    import re
    import shlex
    f = _settings(path or settings_path())
    if f.get('error'):
        return None
    found = _found(f['obj'])
    commands = [c for e in EVENTS for c in found.get(e, [])]
    if not commands:
        return None
    here = os.path.realpath(os.path.dirname(os.path.abspath(__file__)))
    for cmd in commands:
        try:
            words = shlex.split(cmd)
            mm = re.search(r'D = ("(?:[^"\\]|\\.)*")', words[4]) if len(words) > 4 and words[3] == '-c' else None
            folder = json.loads(mm.group(1)) if mm else None
        except ValueError:
            return False
        if not isinstance(folder, str) or os.path.realpath(folder) != here:
            return False
    return True


def _wholly_ours(g):
    """A matcher group whose every handler is Switchboard's: only such a group's matcher is Switchboard's to change."""
    hs = g.get('hooks') if isinstance(g, dict) else None
    return isinstance(hs, list) and bool(hs) and all(is_ours(h) for h in hs)


def _matchers_current(obj):
    """Every group of Switchboard's own that has a matcher has today's (MATCHERS)."""
    hooks = obj.get('hooks') if isinstance(obj, dict) else None
    if not isinstance(hooks, dict):
        return True
    for e, want in MATCHERS.items():
        for g in hooks.get(e) if isinstance(hooks.get(e), list) else []:
            if _wholly_ours(g) and 'matcher' in g and g['matcher'] != want:
                return False
    return True


def upgrade(path=None):
    """An install made by an older Switchboard brought up to date in place (the PostToolUse matchers, which now take
    every claude.ai connector), keeping the command it runs. Only when all of Switchboard's session hooks are
    in and they are not turned off in Switchboard ("session_hooks": false); it never puts them in, and never raises.
    The undo stays exact: only Switchboard's own groups change. True when it changed the file."""
    try:
        if not hooks_on():
            return False
        path = path or settings_path()
        f = _settings(path)
        if f.get('error'):
            return False
        found = _found(f['obj'])
        if not all(found.get(e) for e in EVENTS) or _matchers_current(f['obj']):
            return False
        cmds = set(c for e in EVENTS for c in found[e])
        plan = install_plan(path, command_line=cmds.pop() if len(cmds) == 1 else command())
        if not plan.get('ok') or not plan.get('change'):
            return False
        return bool(apply(plan).get('changed'))
    except Exception:  # noqa: BLE001 - the hooks as they were still run; only the connectors don't reach them
        return False


def repair(python=None):
    """At the page's start: Switchboard's session hooks whose Python is gone (an app that held it was removed, an
    update chose another) run on this one instead, through install_plan, verify and apply as ever (checked, backed up,
    recorded for the undo). Only a file where all of them are in, each exactly the command Switchboard's record says it
    put there; a hook of yours is never touched. Looks at this process's settings file and every other one the record
    names. Never raises. Returns one log line for each file it changed, or could not change."""
    python = python or sys.executable
    if not python or _sl.python_gone(python) or not hooks_on():     # turned off in Switchboard: never written again
        return []
    said = []
    here = os.path.realpath(settings_path())
    for target in [here] + sorted(t for t in _records() if t != here):
        try:
            f = _settings(target)
            found = {} if f.get('error') else _found(f['obj'])
            rec = _records().get(f['target'])
            cmds = set(c for e in EVENTS for c in found.get(e, []))
            if not rec or not all(found.get(e) for e in EVENTS) or cmds != {rec.get('installed')} or \
                    not _sl.python_gone(rec['installed']):
                continue
            cmd = command(python=python)
            plan = install_plan(target, command_line=cmd)
            if plan.get('ok') and not plan.get('change'):
                continue
            why = verify(cmd) if plan.get('ok') else plan.get('message')
            if why is None:
                apply(plan)
                said.append('the session hooks in %s run on %s now: the Python they named is gone' % (target, python))
                continue
        except PlanError as e:
            why = str(e)
        except Exception as e:  # noqa: BLE001 - the hooks stay as they were; the page starts either way
            why = repr(e)
        said.append('the session hooks in %s name a Python that is gone and were left as they are: %s' % (target, why))
    return said


def _runs(cmd):
    import json
    import re
    import shlex
    try:
        words = shlex.split(cmd)
        mm = re.search(r'D = ("(?:[^"\\]|\\.)*")', words[4]) if len(words) > 4 and words[3] == '-c' else None
        folder = json.loads(mm.group(1)) if mm else None
    except ValueError:
        return False
    return bool(words) and os.path.isfile(words[0]) and os.access(words[0], os.X_OK) and isinstance(folder, str) \
        and os.path.isfile(os.path.join(folder, 'session_hooks.py'))


# ---------- reading and editing the settings file (notify_hooks' byte-exact splicing) ----------

def _settings(path):
    """Read and understand a settings file. Never raises for a bad file: it says what is wrong instead."""
    nh = _nh()
    path = os.path.abspath(os.path.expanduser(path))
    f = {'path': path, 'target': os.path.realpath(path)}
    if os.path.islink(path) and not os.path.exists(path):
        return dict(f, error="%s is a link to a file that isn't there, so Switchboard leaves it alone." % path)
    try:
        raw = _sl._read_bytes(f['target'])
    except OSError as e:
        return dict(f, error="Can't read %s: %s" % (path, e.strerror or e))
    f['digest'] = _sl._digest(raw)
    if raw is not None and raw.startswith(b'\xef\xbb\xbf'):
        return dict(f, error='%s starts with a byte-order mark, so Switchboard leaves it alone.' % path)
    try:
        text = raw.decode('utf-8') if raw is not None else ''
    except UnicodeDecodeError:
        return dict(f, error='%s is not UTF-8 text, so Switchboard leaves it alone.' % path)
    f.update(text=text, exists=raw is not None, blank=not text.strip())
    if f['blank']:
        f['obj'] = {}
        return f
    dec = nh._decoder()
    try:
        obj = dec.decode(text)
    except (ValueError, RecursionError):
        return dict(f, error=nh.INVALID % path)
    if not isinstance(obj, dict):
        return dict(f, error="%s doesn't hold a JSON object, so Switchboard leaves it alone." % path)
    try:
        members, _ = nh._object_at(text, nh._skip(text, 0), dec)
        h = nh._one(members, 'hooks', '%s has "hooks" twice. Remove one first.' % path)
        if h is not None:
            if not isinstance(obj['hooks'], dict):
                raise nh._Shape('In %s, "hooks" is not an object, so Switchboard leaves the file alone.' % path)
            inner, _ = nh._object_at(text, h['v0'], dec)
            for e in EVENTS:
                if nh._one(inner, e, '%s has "%s" twice in "hooks". Remove one first.' % (path, e)) is not None \
                        and not isinstance(obj['hooks'][e], list):
                    raise nh._Shape('In %s, "%s" is not a list, so Switchboard leaves the file alone.' % (path, e))
    except nh._Shape as e:
        return dict(f, error=str(e))
    except (ValueError, RecursionError):
        return dict(f, error=nh.INVALID % path)
    f['obj'] = obj
    return f


def _found(obj, events=EVENTS):
    """{event: [the commands of this file's handlers in it]}."""
    out = {}
    hooks = obj.get('hooks') if isinstance(obj, dict) else None
    if not isinstance(hooks, dict):
        return out
    for e in events if events is not None else list(hooks):
        for g in hooks.get(e) if isinstance(hooks.get(e), list) else []:
            for h in g.get('hooks') if isinstance(g, dict) and isinstance(g.get('hooks'), list) else []:
                if is_ours(h):
                    out.setdefault(e, []).append(h['command'])
    return out


def _put_in(text, want, cmd, made):
    """Text with this file's groups added (or its commands brought up to date) in place; made records what was
    created."""
    import json
    nh = _nh()
    dec, nl = nh._decoder(), nh._nl(text)
    w = nh._Where(text, dec)
    unit = nh._unit(text, w.o, w.top)
    if w.hooks is None:
        made['hooks'], made['events'] = True, list(EVENTS)
        return nh._add_member(text, w.o, w.end, w.top, 'hooks', dict((e, [want[e]]) for e in EVENTS), unit, nl, True)
    for e in EVENTS:
        w = nh._Where(text, dec)
        m = w.event(e)
        if m is None:
            text = nh._add_member(text, w.hooks['v0'], w.hend, w.inner, e, [want[e]], unit, nl, w.spread(w.hooks))
            if e not in made['events']:
                made['events'].append(e)
            continue
        groups, end = nh._array_at(text, m['v0'], dec)
        ours, spans = False, []
        for g0, _ in groups:
            hs = nh._object_at(text, g0, dec)[0] if text[g0] == '{' else []
            hm = next((x for x in hs if x['key'] == 'hooks'), None)
            if hm is None or text[hm['v0']] != '[':
                continue
            for h0, _ in nh._array_at(text, hm['v0'], dec)[0]:
                value = dec.raw_decode(text, h0)[0]
                if is_ours(value):
                    ours = True
                    if value['command'] != cmd:
                        c = next(x for x in reversed(nh._object_at(text, h0, dec)[0]) if x['key'] == 'command')
                        spans.append((c['v0'], c['v1'], json.dumps(cmd, ensure_ascii=False)))
            mm = next((x for x in hs if x['key'] == 'matcher'), None)
            if e in MATCHERS and mm is not None and _wholly_ours(dec.raw_decode(text, g0)[0]) and \
                    dec.raw_decode(text, mm['v0'])[0] != MATCHERS[e]:
                spans.append((mm['v0'], mm['v1'], json.dumps(MATCHERS[e], ensure_ascii=False)))
        if not ours:
            text = nh._add_element(text, m['v0'], end, groups, want[e], unit, nl, w.spread(m))
            continue
        for a, b, new in sorted(spans, reverse=True):
            text = text[:a] + new + text[b:]
    return text


def _take_out(text, made):
    """Text with every handler of this file's taken out (a group left with none of yours goes with it), then the
    events and "hooks" Switchboard made, once they are empty."""
    nh = _nh()
    dec = nh._decoder()
    while True:
        w = nh._Where(text, dec)
        cut = None
        for m in w.inner:
            if text[m['v0']] != '[':
                continue
            groups, end = nh._array_at(text, m['v0'], dec)
            for gi in range(len(groups) - 1, -1, -1):
                g0 = groups[gi][0]
                if text[g0] != '{':
                    continue
                hm = next((x for x in nh._object_at(text, g0, dec)[0] if x['key'] == 'hooks'), None)
                if hm is None or text[hm['v0']] != '[':
                    continue
                handlers, hend = nh._array_at(text, hm['v0'], dec)
                mine = [i for i, (h0, _) in enumerate(handlers) if is_ours(dec.raw_decode(text, h0)[0])]
                if mine and len(mine) == len(handlers):
                    cut = nh._drop_element(text, m['v0'], end, groups, gi)
                elif mine:
                    cut = nh._drop_element(text, hm['v0'], hend, handlers, mine[-1])
                if cut is not None:
                    break
            if cut is not None:
                break
        if cut is None:
            break
        text = cut
    for e in reversed(EVENTS):
        w = nh._Where(text, dec)
        m = w.event(e)
        if m is not None and e in made.get('events', []) and text[m['v0']] == '[' and not nh._array_at(text, m['v0'], dec)[0]:
            text = nh._drop_member(text, w.hooks['v0'], w.hend, w.inner, w.inner.index(m), made.get('hooks_inner'))
    w = nh._Where(text, dec)
    if w.hooks is not None and made.get('hooks') and not w.inner:
        text = nh._drop_member(text, w.o, w.end, w.top, w.top.index(w.hooks), made.get('inner'))
    return text


def _with_ours(obj, want, cmd):
    import copy
    o = copy.deepcopy(obj)
    hooks = o.get('hooks')
    if hooks is None:
        o['hooks'] = dict((e, [want[e]]) for e in EVENTS)
        return o
    for e in EVENTS:
        groups = hooks.get(e)
        if groups is None:
            hooks[e] = [want[e]]
            continue
        mine = [h for g in groups if isinstance(g, dict) and isinstance(g.get('hooks'), list) for h in g['hooks'] if is_ours(h)]
        for g in groups:
            if e in MATCHERS and _wholly_ours(g) and 'matcher' in g:
                g['matcher'] = MATCHERS[e]
        for h in mine:
            h['command'] = cmd
        if not mine:
            groups.append(want[e])
    return o


def _without_ours(obj, made):
    import copy
    o = copy.deepcopy(obj)
    hooks = o.get('hooks')
    if not isinstance(hooks, dict):
        return o
    for e, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        kept = []
        for g in groups:
            if isinstance(g, dict) and isinstance(g.get('hooks'), list):
                mine = [h for h in g['hooks'] if is_ours(h)]
                if mine and len(mine) == len(g['hooks']):
                    continue
                if mine:
                    g = dict(g, hooks=[h for h in g['hooks'] if not is_ours(h)])
            kept.append(g)
        hooks[e] = kept
    for e in EVENTS:
        if e in made.get('events', []) and hooks.get(e) == []:
            del hooks[e]
    if made.get('hooks') and hooks == {}:
        del o['hooks']
    return o


# ---------- the plans ----------

SHARED = ('file_was', 'inner', 'hooks_inner')       # what the first of Switchboard's pieces in a file hands the next


def _neighbours(target):
    """The other pieces of Switchboard in this settings file, by their records: {'notify': record or None,
    'statusline': record or None}."""
    return {'notify': _nh()._records().get(target), 'statusline': _sl._records().get(target)}


def install_plan(path=None, command_line=None):
    """The exact change that puts the session hooks in. Changes nothing by itself."""
    nh = _nh()
    cmd = command_line or command()
    want = wanted(cmd)
    f = _settings(path or settings_path())
    if f.get('error'):
        return nh._refused(f, 'install')
    found = _found(f['obj'])
    if all(found.get(e) for e in EVENTS) and all(c == cmd for e in EVENTS for c in found[e]) and \
            _matchers_current(f['obj']):
        return nh._nothing(f, 'install', "Switchboard's session hooks are already on.")
    rec = _records().get(f['target']) or {}
    made = {'hooks': bool(rec.get('made_hooks')), 'events': list(rec.get('made_events') or [])}
    record = {'installed': cmd}
    near = _neighbours(f['target'])
    for k in SHARED:
        if k in rec:
            record[k] = rec[k]
        else:
            for other in (near['notify'], near['statusline']):     # the file as it was before Switchboard came
                if isinstance(other, dict) and k in other:
                    record[k] = other[k]
                    break
    if isinstance(near['notify'], dict) and near['notify'].get('made_hooks'):
        record['hooks_by_switchboard'] = True       # "hooks" came with the notification hooks: it goes with the last
    shared_events = sorted(set(rec.get('events_by_switchboard') or []) |
                           set(e for e in ((near['notify'] or {}).get('made_events') or []) if e in EVENTS))
    if shared_events:
        record['events_by_switchboard'] = shared_events     # e.g. UserPromptSubmit, made by the notification hooks
    expect = _with_ours(f['obj'], want, cmd)
    if f['blank']:
        text = '{\n  "hooks": %s\n}\n' % nh._render(expect['hooks'], '  ', '  ', '\n')
        made = {'hooks': True, 'events': list(EVENTS)}
        record['file_was'] = f['text'] if f['exists'] else None
    else:
        try:
            w = nh._Where(f['text'], nh._decoder())
            if not w.top and 'inner' not in record:
                record['inner'] = f['text'][w.o + 1:w.end - 1]
            if w.hooks is not None and not w.inner and 'hooks_inner' not in record:
                record['hooks_inner'] = f['text'][w.hooks['v0'] + 1:w.hend - 1]
            text = _put_in(f['text'], want, cmd, made)
        except (ValueError, RecursionError, KeyError, StopIteration):
            text = None
    record.update(made_hooks=made['hooks'], made_events=made['events'])
    msg = "Switchboard's session hooks are updated to run from here." if found else "Switchboard's session hooks go on."
    return nh._change(f, 'install', text, expect, record, msg)


def uninstall_plan(path=None):
    """The exact change that takes the session hooks out, and only them. Changes nothing by itself."""
    nh = _nh()
    f = _settings(path or settings_path())
    if f.get('error'):
        return nh._refused(f, 'uninstall')
    if not _found(f['obj'], events=None):
        return nh._nothing(f, 'uninstall', "Switchboard's session hooks aren't on.")
    rec = _records().get(f['target']) or {}
    near = _neighbours(f['target'])
    others_here = bool(near['notify']) and bool(nh._found(f['obj'])) or \
        (bool(near['statusline']) and _sl.is_ours(f['obj'].get('statusLine')))
    # An event list or "hooks" that Switchboard made (this piece, or the notification hooks before it) goes once empty.
    made = {'hooks': bool(rec.get('made_hooks')) or (bool(rec.get('hooks_by_switchboard')) and not
                                                     (near['notify'] and nh._found(f['obj']))),
            'events': sorted(set(rec.get('made_events') or []) | set(rec.get('events_by_switchboard') or [])),
            'inner': rec.get('inner'), 'hooks_inner': rec.get('hooks_inner')}
    try:
        text = _take_out(f['text'], made)
    except (ValueError, RecursionError, KeyError, StopIteration):
        text = None
    plan = nh._change(f, 'uninstall', text, _without_ours(f['obj'], made), None, "Switchboard's session hooks go off.")
    if plan['ok'] and 'file_was' in rec and plan['expect'] == {} and not others_here:
        was = rec['file_was']
        plan.update(delete=was is None, text=was if was is not None else '', change=True,
                    message="Switchboard's session hooks go off, and the settings file it made goes too."
                    if was is None else plan['message'])
    if plan['ok']:
        plan['handover'] = _handover(rec, near)
    return plan


def _handover(rec, near):
    """What this piece knows about the file before Switchboard came, for each piece still in it: {'notify': {...},
    'statusline': {...}}, only the keys each lacks."""
    out = {}
    for name, keys in (('notify', SHARED + ('made_hooks', 'made_events')), ('statusline', ('file_was', 'inner'))):
        other = near.get(name)
        if not isinstance(other, dict):
            continue
        give = {}
        for k in keys:
            if k == 'made_hooks':
                if (rec.get('made_hooks') or rec.get('hooks_by_switchboard')) and not other.get('made_hooks'):
                    give['made_hooks'] = True
            elif k == 'made_events':
                theirs = set(other.get('made_events') or [])
                mine = (set(rec.get('made_events') or []) | set(rec.get('events_by_switchboard') or [])) & \
                    set(_nh().EVENTS)
                if mine - theirs:
                    give['made_events'] = [e for e in _nh().EVENTS if e in theirs | mine]
            elif k in rec and k not in other:
                give[k] = rec[k]
        if give:
            out[name] = give
    return out


def _give(handover, target):
    """Hand what this piece knew to the pieces still in the file: their own records gain it, so whichever goes last
    puts the file back as it was."""
    nh = _nh()
    if handover.get('notify'):
        recs = nh._records()
        if isinstance(recs.get(target), dict):
            recs[target] = dict(recs[target], **handover['notify'])
            nh._put_records(recs)
    if handover.get('statusline'):
        recs = _sl._records()
        if isinstance(recs.get(target), dict):
            recs[target] = dict(recs[target], **handover['statusline'])
            _sl._put_records(recs)


def apply(plan):
    """Make a plan's change: check the file is still as planned, keep the one backup beside it, write atomically, read
    it back. Returns {'changed', 'path', 'backup', 'message'}. Raises PlanError when it refuses."""
    if not plan.get('ok'):
        raise PlanError(plan.get('message') or 'This plan cannot be applied.')
    if not plan.get('change'):
        return {'changed': False, 'path': plan['path'], 'backup': None, 'message': plan['message']}
    target = plan['target']
    changed = "Claude Code's settings file changed just now. Nothing was written; try again."
    try:
        now_bytes = _sl._read_bytes(target)
    except OSError as e:
        raise PlanError("Can't read %s: %s" % (plan['path'], e.strerror or e))
    if _sl._digest(now_bytes) != plan['digest']:
        raise PlanError(changed)
    if now_bytes is not None and not os.access(target, os.W_OK):
        raise PlanError("Claude Code's settings file is set read-only, so Switchboard leaves it alone.")
    backup = None
    before = _records()
    try:
        if now_bytes is not None:
            backup = _sl.backup_path(plan['path'])
            _sl._write_atomic(backup, now_bytes, 0o600)
        if plan['action'] == 'install':
            _save_record(target, plan['record'])
    except OSError as e:
        raise PlanError(_sl.cant_write(plan['path'], e))
    try:
        if _sl._digest(_sl._read_bytes(target)) != plan['digest']:
            raise PlanError(changed)
        if plan.get('delete'):
            os.unlink(target)
        else:
            try:
                mode = os.stat(target).st_mode & 0o7777
            except OSError:
                mode = 0o600
            _sl._write_atomic(target, plan['text'].encode('utf-8'), mode)
    except BaseException as e:
        if plan['action'] == 'install':
            try:
                _put_records(before)
            except OSError:
                pass
        if isinstance(e, OSError):
            raise PlanError(_sl.cant_write(plan['path'], e))
        raise
    if not plan.get('delete'):
        check = _settings(plan['path'])
        if check.get('error') or check['obj'] != plan['expect']:
            raise PlanError('The settings file did not read back as planned. Its copy from before is beside it, '
                            'ending in .switchboard-backup.')
    if plan['action'] == 'uninstall':
        try:
            _give(plan.get('handover') or {}, target)
        except OSError:
            pass
        try:
            _drop_record(target)
        except OSError:
            pass
    return {'changed': True, 'path': plan['path'], 'backup': backup, 'message': plan['message']}


def _records():
    import json
    try:
        with open(record_path()) as fh:
            data = json.load(fh)
        return dict(data['installs']) if isinstance(data.get('installs'), dict) else {}
    except (OSError, ValueError, AttributeError, KeyError, TypeError):
        return {}


def _put_records(recs):
    import json
    _sl._write_atomic(record_path(), json.dumps({'v': 1, 'installs': recs}, indent=1).encode('utf-8'), 0o600)


def _save_record(target, record):
    recs = _records()
    recs[target] = dict(record, at=round(time.time()))
    _put_records(recs)


def _drop_record(target):
    recs = _records()
    if recs.pop(target, None) is not None:
        _put_records(recs)


if __name__ == '__main__':
    run()
