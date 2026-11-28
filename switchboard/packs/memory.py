"""It forgets between sessions: search every past chat, check Claude Code's own memory, and keep
claude-mem to your chats.

Three parts, none of which calls a model.

1. Search (update_index, search). An index of Claude Code's local history on this Mac: your prompts,
   the assistant's text replies, chat titles, folders and dates. Not tool output, not thinking, not
   injected notices, not helpers' transcripts. SQLite with FTS5 (porter stemming, accents folded)
   from Python's standard library; when FTS5 is missing, a plain table searched by substring. The
   index lives in <data folder>/packs/memory-index.sqlite3, readable only by you. Updates are
   incremental: a chat file is read again only from where the last read stopped, and only when its
   size, change time or file id moved; a file whose last-read bytes changed is read again whole.
   Text of chats Claude Code has since deleted (cleanupPeriodDays) stays searchable.

   Key-like text never reaches the index: every message and chat name goes through the log's filter
   (core.redact: Anthropic keys and tokens, JSON web tokens, the value after bearer, token, secret, password
   or api_key, runs of 40 or more key-like characters) before it is stored, so a key once pasted into a
   prompt is not kept in a second file (private_text). An index made by an older build may hold such text:
   the first thing of this build that opens it erases it, and search builds it again. erase() takes the
   index away (its file and SQLite's files beside it, written over with zeros first); Claude Code's chats
   are not touched.

   Ranking: the evidence is the best messages, not chat-wide word counts. Real chat lengths are very
   skewed (most chats are a scripted run of one or two messages, a few run to a thousand), so counting
   a word across a whole chat and dividing by its length would let a one-message run that names a word once
   beat the long chat where the thing was worked out. Instead: idf of a query word = ln(1 + (N - df + 0.5)
   / (df + 0.5)), N chats, df the chats holding it (in a message, the title or the folder name). W = the
   idf of all query words found, H = the idf of the distinct query words one message holds; that
   message scores W x (H / W)^2, so a message with all the words scores W and words scattered one per
   message score little. A message over 2,000 bytes (a pasted file, a giant scripted prompt) counts
   (2000 / its bytes)^0.25 as much. A chat scores its best message + 1/2, 1/4, 1/8, 1/16 of its next
   four, plus the idf of each query word in its title or folder name: a long chat is not punished for
   its length, and cannot win by it either, since past five messages more text adds nothing and a
   giant chat still needs the words together in one message. Ties go to the newer chat. Chats with the
   same shown title in the same folder (a run repeated every day) are one result, the best of them,
   saying how many others it stands for. A chat with no title shows its first prompt, heading and list
   marks taken off. Common English words and words about chats ("chat", "conversation", "remember") are
   not searched for. The plain fallback ranks by the same rules (on substrings, without stemming).

2. Health (health). Which auto-memory index files (MEMORY.md) are over the limit Claude Code loads
   at the start of a session, and by how much. Read-only.

3. claude-mem, managed (cm_*). claude-mem (github.com/thedotmack/claude-mem, Apache-2.0) runs a
   second model on every tool call it sees. Switchboard turns on claude-mem's own documented switch
   CLAUDE_MEM_SKIP_SUBAGENT_OBSERVATIONS=true, so helper and workflow agents' tool calls are dropped
   before any model call, and adds a weekly cap: when claude-mem's observer used more than the share
   you set of all Claude Code use in the last 7 days, Switchboard sets CLAUDE_MEM_OBSERVER_ENABLED
   =false (claude-mem's own switch that stops new observation and summary model calls; search and
   saved memories stay) and puts it back when the share falls to the line again. Both are edits of
   claude-mem's settings file (~/.claude-mem/settings.json), member by member, undone exactly.
   cm_plan() itself never installs or runs claude-mem; installing it is packs/claudemem_install.py's, after the
   person agrees to its plan (packs/install.py).

Manual checks:
    python3 -m switchboard.packs.memory search "where did we fix the login loop"
    python3 -m switchboard.packs.memory index | erase | health | claude-mem
"""
import argparse
import calendar
import heapq
import json
import math
import os
import re
import sqlite3
import stat
import sys
import time
import urllib.parse

from switchboard import core
from switchboard.packs import common

INDEX_NAME = 'memory-index.sqlite3'
SCHEMA = 2                     # 2: key-like text is kept out (private_text); an index of 1 is erased and made again
MAX_TEXT = 200000              # characters kept from one message (a pasted file can be larger)
CUT_MARGIN = 8192              # read this much more, so a key at the cut is filtered whole before the cut (_store)
TITLE_WEIGHT = 1.0
SHARE = (0.5, 0.25, 0.125, 0.0625)  # what a chat's 2nd to 5th best messages add, as shares of their own scores
COVER_POWER = 2.0              # a message's score: all query words' idf x (its words' share of that idf) ** this
LONG_BYTES, LONG_POWER = 2000, 0.25  # a longer message counts (LONG_BYTES / its UTF-8 length) ** LONG_POWER as much
SNIPPET_TOKENS = 12
SNIPPET_SPAN = 120             # characters the plain fallback looks across for the most query words together
TITLE_CHARS = 80

# A classic English stop list (the one NLTK ships), plus words people use about chats themselves.
STOP = frozenset((
    'i me my myself we our ours ourselves you your yours yourself yourselves he him his himself she her hers '
    'herself it its itself they them their theirs themselves what which who whom this that these those am is are '
    'was were be been being have has had having do does did doing a an the and but if or because as until while '
    'of at by for with about against between into through during before after above below to from up down in out '
    'on off over under again further then once here there when where why how all any both each few more most '
    'other some such no nor not only own same so than too very s t can will just don should now d ll m o re ve y '
    'chat chats conversation conversations talked discussed remember').split())


# ---------------------------------------------------------------- where things are

def projects_root(claude_dir=None):
    return os.path.join(common.claude_dir(claude_dir), 'projects')


def index_path():
    return os.path.join(common.packs_dir(), INDEX_NAME)


def fts5_available():
    try:
        con = sqlite3.connect(':memory:')
        try:
            con.execute("CREATE VIRTUAL TABLE t USING fts5(a, tokenize='porter unicode61 remove_diacritics 2')")
        finally:
            con.close()
        return True
    except sqlite3.Error:
        return False


# ---------------------------------------------------------------- reading one chat file

MARKS = (b'"type":"user"', b'"type":"assistant"', b'"type":"custom-title"', b'"type":"ai-title"')
SPACED = tuple(m.replace(b'":"', b'": "') for m in MARKS)
CHUNK = 1 << 24
# A message that starts with one of these is Claude Code talking, not you: background task notices,
# command output, reminders, editor notices. Other tags (a pasted block, a command's name and
# arguments) are taken off and their words kept.
NOT_YOURS = ('<task-notification>', '<local-command-stdout>', '<local-command-stderr>', '<local-command-caveat>',
             '<system-reminder>', '<bash-stdout>', '<bash-stderr>', '<ide_opened_file>', '<ide_selection>',
             '<command-message>')
_TAG = re.compile(r'</?[A-Za-z][\w:.-]*(?:\s[^<>]{0,300})?/?>')
_SPACE = re.compile(r'\s+')
_TS = re.compile(r'(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$')


def parse_ts(value):
    """Epoch seconds from an ISO time (no zone means UTC), or None."""
    if not isinstance(value, str):
        return None
    m = _TS.match(value.strip())
    if not m:
        return None
    y, mo, d, h, mi, s, frac, zone = m.groups()
    try:
        t = calendar.timegm((int(y), int(mo), int(d), int(h), int(mi), int(s), 0, 0, 0))
    except (ValueError, OverflowError):
        return None
    if frac:
        t += float('0.' + frac)
    if zone and zone not in ('Z', 'z'):
        sign = -1 if zone[0] == '+' else 1
        z = zone[1:].replace(':', '')
        t += sign * (int(z[:2]) * 3600 + int(z[2:4]) * 60)
    return float(t)


def _clean(text):
    return _SPACE.sub(' ', _TAG.sub(' ', text)).strip()[:MAX_TEXT + CUT_MARGIN]


def prompt_text(content):
    """Your words in a user message's content, or '' when it holds none of yours."""
    parts = []
    if isinstance(content, str):
        parts = [content]
    elif isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get('type') == 'text' and isinstance(b.get('text'), str):
                parts.append(b['text'])
    keep = [p for p in parts if not p.lstrip().startswith(NOT_YOURS)]
    return _clean(' '.join(keep)) if keep else ''


def _spans(buf, end, marks):
    found = {}
    for n, mark in enumerate(marks):
        i = buf.find(mark, 0, end)
        while i != -1:
            s = buf.rfind(b'\n', 0, i) + 1
            e = buf.find(b'\n', i, end)
            if e == -1:
                e = end
            if s not in found:
                found[s] = (e, n)
            i = buf.find(mark, e, end)
    return sorted((s, e, n) for s, (e, n) in found.items())


def read_chat(path, offset=0):
    """What the index keeps from one chat file, read from byte `offset` to the last complete line.

    Returns {'msgs': [(role, ts, text)], 'custom': title or None, 'ai': title or None, 'cwd', 'first', 'last',
    'end': the offset to read from next time, 'bad': lines that did not parse}. role is 'you' or 'claude'.
    """
    out = {'msgs': [], 'custom': None, 'ai': None, 'cwd': None, 'first': None, 'last': None, 'end': offset,
           'bad': 0}
    seen = set()
    with open(path, 'rb') as f:
        if offset:
            f.seek(offset)
        base = offset                   # the file offset of buf[0]
        carry = b''
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            buf = carry + chunk if carry else chunk
            cut = buf.rfind(b'\n') + 1
            if cut == 0:
                carry = buf
                continue
            # Claude Code writes compact JSON; a file with spaced keys anywhere in this chunk is searched both ways
            marks = MARKS + SPACED if buf.find(b'"type": "', 0, cut) != -1 else MARKS
            for s, e, n in _spans(buf, cut, marks):
                _take(out, buf, s, e, n % len(MARKS), seen)
            base += cut
            carry = buf[cut:]
        out['end'] = base               # a last line with no newline yet is read next time
    return out


def _take(out, buf, s, e, n, seen):
    if n == 0 and buf.find(b'"tool_result"', s, e) != -1:
        return                      # a tool's output coming back: never read
    if n == 1 and buf.find(b'"text"', s, e) == -1:
        return                      # thinking or a tool call only
    try:
        d = json.loads(buf[s:e])
    except ValueError:
        out['bad'] += 1
        return
    if not isinstance(d, dict):
        return
    kind = d.get('type')
    if kind == 'custom-title':
        if isinstance(d.get('customTitle'), str) and d['customTitle'].strip():
            out['custom'] = d['customTitle'].strip()
        return
    if kind == 'ai-title':
        if isinstance(d.get('aiTitle'), str) and d['aiTitle'].strip():
            out['ai'] = d['aiTitle'].strip()
        return
    if kind not in ('user', 'assistant') or d.get('isSidechain'):
        return
    m = d.get('message')
    if not isinstance(m, dict):
        return
    if kind == 'user':
        if d.get('isMeta') or d.get('isCompactSummary'):
            return
        texts = [prompt_text(m.get('content'))]
        role = 'you'
    else:
        if m.get('model') == '<synthetic>':
            return
        texts = []
        for b in m.get('content') if isinstance(m.get('content'), list) else ():
            if isinstance(b, dict) and b.get('type') == 'text' and isinstance(b.get('text'), str):
                texts.append(_clean(b['text']))
        role = 'claude'
    ts = parse_ts(d.get('timestamp'))
    if out['cwd'] is None and isinstance(d.get('cwd'), str) and d['cwd'].strip():
        out['cwd'] = d['cwd']
    for t in texts:
        if not t:
            continue
        key = (role, m.get('id') if role == 'claude' else d.get('uuid'), t[:64], len(t))
        if key in seen:
            continue
        seen.add(key)
        out['msgs'].append((role, ts, t))
        if ts is not None:
            out['first'] = ts if out['first'] is None else min(out['first'], ts)
            out['last'] = ts if out['last'] is None else max(out['last'], ts)


# ---------------------------------------------------------------- what the index never keeps
#
# core.redact() is the log's filter. Two of its shapes also take words a person searches for, so those pieces are
# handed past it: a plain word after bearer, token, secret, password or api_key with only a space between ("token
# authentication", "secret configuration"), and a long name of words and numbers joined by _ or - (a test's name,
# "test_sign_in_after_2_tries_..."), which its 40-character rule would take. A value after ":" or "=" is always
# hidden, word-shaped or not. Long hashes (a commit id) are still taken: they are not words.

_KEY_WORD = r'(?i:\b(?:bearer|token|secret|password|api[_-]?key))'
_WORD_AFTER = re.compile(_KEY_WORD + r'\s+([a-z]+|[A-Z][a-z]+)(?![A-Za-z0-9_\-+/=]|\.[A-Za-z0-9_\-+/=])')
_ASSIGNED = re.compile(_KEY_WORD + r'["\']?\s*[:=]\s*["\']?([A-Za-z0-9_\-.+/=]{12,})')
_LONG_RUN = re.compile(r'(?<![A-Za-z0-9_\-+/])[A-Za-z0-9_\-]{40,}(?![A-Za-z0-9_\-+/=])')
_NAME_PART = re.compile(r'[A-Za-z]+[0-9]*|[0-9]+')
_JOIN = re.compile(r'[_-]')
_VALUE_JOIN = re.compile(r'[_.\-]')
# For a quick look before core.redact(): each byte of a key-like character becomes "a", every other byte a space.
_KEYISH = bytes(97 if chr(i).isascii() and (chr(i).isalnum() or chr(i) in '_-+') else 32 for i in range(256))


# Well-known credential shapes a person's prompt can hold that the log's filter was not written for (the log only
# ever holds Switchboard's own secrets). Each is hidden at index time, before core.redact() runs.
_PRIVATE_KEY = re.compile(r'-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----[\s\S]*?'
                          r'(?:-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|\Z)')     # PEM, OpenSSH and PGP
# scheme://user:PASSWORD@host, the password up to the last @ before a space (not a port: host:443/...)
_URL_PASSWORD = re.compile(r'(\b[A-Za-z][A-Za-z0-9+.\-]*://[^\s:/@]+:)(?!\d+/)(\S+)(?=@[^\s@]*(?:\s|$))')
_BASIC = re.compile(r'(?i)(authorization["\']?\s*[:=]\s*["\']?basic\s+)([A-Za-z0-9+/]{8,}={0,2})')
_AWS_SECRET = re.compile(r'(?i)(secret_?access_?key["\']?(?:\s*[:=]\s*|\s+)["\']?)([A-Za-z0-9/+=]{40})'
                         r'(?![A-Za-z0-9/+=])')
# NAME=value, NAME: value, "NAME": "value", where NAME holds key, token, secret, password, passwd, passphrase, pw
# or credential anywhere
_NAMED = re.compile(r'(?<![A-Za-z0-9_.\-])(["\']?)([A-Za-z0-9_.\-]*?'
                    r'(?i:key|token|secret|passw(?:or)?d|passphrase|pw|credentials?)'
                    r'[A-Za-z0-9_.\-]*)\1(\s*(?::(?![:=])|=(?![=>]))\s*["\']?)([^\s"\'`]{8,})')
_NAME_LOOKS_SET = re.compile(r'[_.\-]|[a-z][A-Z]|^[A-Z0-9]+$')     # DB_PASSWORD, apiKey, TOKEN: not a word in prose
_PREFIXED = re.compile(r'(?<![A-Za-z0-9_\-])(?:(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])|sk-[A-Za-z0-9_\-]{20,}'
                       r'|hf_[A-Za-z0-9]{30,}|glpat-[A-Za-z0-9_\-]{20,}|npm_[A-Za-z0-9]{30,}'
                       r'|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[bpars]-[A-Za-z0-9\-]{10,}'
                       r'|AIza[0-9A-Za-z_\-]{35}|[spr]k_live_[0-9A-Za-z]{10,})')
# Literal marks every pattern above or in core.redact() starts from (case as they match)
_MARKS_ANY = ('sk-', 'eyJ', 'PRIVATE KEY', 'AKIA', 'ASIA', 'gh', 'github_pat_', 'xox', 'AIza', 'k_live_', 'hf_',
              'glpat-', 'npm_')
_STRICT = re.compile(r'(?i)token|secret|pass|pw|credential')    # a name with one of these: its value always goes


def _named(m):
    """One NAME=value match: the value hidden, unless it is code or a reference (a call, an index, $VAR, {...}), or
    the colon form reads as prose ("the key: remember ..."): a plain lower-case name, no quotes, a value of letters."""
    quote, name, op, value = m.groups()
    if value[0] in '${' or '(' in value or '[' in value or value.startswith(('/', '~/', './')) or '://' in value:
        return m.group(0)
    if not _STRICT.search(name) and _is_name(value.rstrip(').,;]}'), _VALUE_JOIN):
        return m.group(0)               # key=operator.itemgetter, Primary key: customer_id_and_order_id
    if ':' in op and not quote and not _NAME_LOOKS_SET.search(name) and value.isalpha():
        return m.group(0)
    return m.group(0)[:m.start(4) - m.start(0)] + '[hidden]'


def _is_name(run, join):
    """run is a name a person types and searches for: words (letters, then digits if any) or numbers joined by the
    characters of join, as test_login_with_oauth2_works or react-native-v2-plan; never a part of 20 or more
    characters that mixes capitals and small letters (a random blob), and at least two parts or letters only."""
    parts = join.split(run)
    if not (len(parts) > 1 or run.isalpha()):
        return False
    for p in parts:
        if not _NAME_PART.fullmatch(p):
            return False
        if len(p) >= 20 and any(c.isupper() for c in p[1:]) and any(c.islower() for c in p):
            return False
    return True


def _may_hide(text):
    """False when nothing below could change text: none of the marks or words the patterns (these and
    core.redact()'s) start from, and no run of 40 key-like characters. Most messages are like that, and this look
    costs far less than the patterns."""
    if any(mark in text for mark in _MARKS_ANY) or ('://' in text and '@' in text):
        return True
    if b'a' * 40 in text.encode('utf-8', 'replace').translate(_KEYISH):
        return True
    low = text.casefold()
    if any(w in low for w in ('bearer', 'token', 'secret', 'password', 'api')):
        return True
    if 'basic' in low and 'authorization' in low:
        return True
    return (':' in text or '=' in text) and any(w in low for w in ('key', 'pass', 'pw', 'credential'))


def _words_kept(text):
    """The pieces of text that core.redact() would take but that are words: [(start, end)], in order."""
    spans = [m.span(1) for m in _WORD_AFTER.finditer(text)]
    for m in _LONG_RUN.finditer(text):
        run = m.group()
        if 'sk-ant-' not in run and _is_name(run, _JOIN):
            spans.append(m.span())
    if not spans:
        return spans
    values = [m.span(1) for m in _ASSIGNED.finditer(text)]
    return sorted(s for s in spans if not any(a < s[1] and s[0] < b for a, b in values))


def private_text(text):
    """text as the index keeps it: [hidden] where a key, token or password was (_hide_all), after a quick look
    (_may_hide) that lets most messages pass untouched."""
    if not text or not _may_hide(text):
        return text
    return _hide_all(text)


def _hide_all(text):
    """The well-known credential shapes above, then core.redact(), except the word-shaped pieces _words_kept()
    finds, which pass as they are."""
    text = _PRIVATE_KEY.sub('[hidden]', text)
    text = _URL_PASSWORD.sub(lambda m: m.group(1) + '[hidden]', text)
    text = _AWS_SECRET.sub(lambda m: m.group(1) + '[hidden]', text)
    text = _BASIC.sub(lambda m: m.group(1) + '[hidden]', text)
    text = _NAMED.sub(_named, text)
    text = _PREFIXED.sub('[hidden]', text)
    out, at = [], 0
    for a, b in _words_kept(text):
        if a < at:
            continue
        out.append(core.redact(text[at:a]))
        out.append(text[a:b])
        at = b
    out.append(core.redact(text[at:]))
    return ''.join(out)


# ---------------------------------------------------------------- the index

def _connect(path, write):
    if write:
        common.make_private_dir(os.path.dirname(os.path.abspath(path)))
        if not os.path.exists(path):
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
        if os.stat(path).st_mode & 0o077:
            os.chmod(path, 0o600)
        con = sqlite3.connect(path)
    else:
        con = sqlite3.connect('file:%s?mode=ro' % urllib.parse.quote(os.path.abspath(path)), uri=True)
    con.execute('PRAGMA busy_timeout = 5000')
    return con


def _schema(con, fts):
    have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}
    if 'meta' in have:
        got = dict(con.execute('SELECT k, v FROM meta'))
        if got.get('schema') != str(SCHEMA):
            raise common.Refused('The search index was made by another version of Switchboard; delete %s to '
                                 'rebuild it.' % con.execute('PRAGMA database_list').fetchone()[2])
        return got.get('fts') == '5'
    con.execute('CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)')
    con.execute('CREATE TABLE chats(id INTEGER PRIMARY KEY, session TEXT NOT NULL, project TEXT NOT NULL, folder TEXT, '
                'custom_title TEXT, ai_title TEXT, first_prompt TEXT, first_ts REAL, last_ts REAL, '
                'prompts INTEGER NOT NULL DEFAULT 0, replies INTEGER NOT NULL DEFAULT 0, UNIQUE(project, session))')
    con.execute('CREATE TABLE files(path TEXT PRIMARY KEY, chat INTEGER NOT NULL, size INTEGER, mtime_ns INTEGER, '
                'ino INTEGER, offset INTEGER, tail TEXT)')
    if fts:
        tok = "tokenize='porter unicode61 remove_diacritics 2'"
        con.execute('CREATE VIRTUAL TABLE msgs USING fts5(text, tag, chat UNINDEXED, role UNINDEXED, '
                    'ts UNINDEXED, %s)' % tok)
        con.execute('CREATE VIRTUAL TABLE titles USING fts5(title, %s)' % tok)
    else:
        con.execute('CREATE TABLE msgs(rowid INTEGER PRIMARY KEY, text TEXT, low TEXT, chat INTEGER, role TEXT, '
                    'ts REAL)')
        con.execute('CREATE INDEX msgs_chat ON msgs(chat)')
        con.execute('CREATE TABLE titles(rowid INTEGER PRIMARY KEY, title TEXT, low TEXT)')
    con.executemany('INSERT INTO meta(k, v) VALUES (?, ?)', [('schema', str(SCHEMA)), ('fts', '5' if fts else '0')])
    return fts


def _scan(root):
    """Every chat transcript under root: <project>/<session>.jsonl (helpers' files and set-aside copies are
    not chats). [(path, project, session)]"""
    out = []
    try:
        projects = sorted((e for e in os.scandir(root) if e.is_dir(follow_symlinks=False)), key=lambda e: e.name)
    except OSError:
        return out
    for p in projects:
        try:
            for e in os.scandir(p.path):
                n = e.name
                if n.endswith('.jsonl') and '.orphaned-' not in n and e.is_file(follow_symlinks=False):
                    out.append((e.path, p.name, n[:-len('.jsonl')]))
        except OSError:
            continue
    return sorted(out)


def _tail(path, end):
    if end <= 0:
        return ''
    with open(path, 'rb') as f:
        f.seek(max(0, end - 4096))
        return common.sha(f.read(end - max(0, end - 4096)))


def _title_text(row):
    title = row['custom_title'] or row['ai_title'] or ''
    folder = row['folder'] or ''
    parts = [p for p in folder.replace('\\', '/').split('/') if p][-2:]
    return ('%s %s' % (title, ' '.join(parts))).strip()


def update_index(root=None, index=None, fts=None):
    """Bring the index up to date with the chat files under root (default: Claude Code's projects folder).
    Returns counts and timings (no text)."""
    t0 = time.time()
    root = root or projects_root()
    index = index or index_path()
    want_fts = fts5_available() if fts is None else fts
    drop_old_index(index)
    con = _connect(index, True)
    st = {'files': 0, 'read': 0, 'unchanged': 0, 'reread_whole': 0, 'bytes_read': 0, 'messages_added': 0,
          'bad_lines': 0, 'vanished': 0, 'fts5': None}
    try:
        con.execute('BEGIN')
        st['fts5'] = fts = _schema(con, want_fts)
        known = {r[0]: r for r in con.execute('SELECT path, chat, size, mtime_ns, ino, offset, tail FROM files')}
        files = _scan(root)
        st['files'] = len(files)
        on_disk = set()
        for i, (path, project, session) in enumerate(files):
            on_disk.add(path)
            try:
                s = os.stat(path)
            except OSError:
                continue
            k = known.get(path)
            if k and k[2] == s.st_size and k[3] == s.st_mtime_ns and k[4] == s.st_ino:
                st['unchanged'] += 1
                continue
            # one chat per file: the same session id in two project folders is two chats
            key = (project, session)
            row = con.execute('SELECT id FROM chats WHERE project = ? AND session = ?', key).fetchone()
            if row is None:
                con.execute('INSERT INTO chats(project, session) VALUES (?, ?)', key)
                row = con.execute('SELECT id FROM chats WHERE project = ? AND session = ?', key).fetchone()
            chat = row[0]
            start = 0
            if k and k[4] == s.st_ino and s.st_size >= (k[5] or 0) and _tail(path, k[5] or 0) == (k[6] or ''):
                start = k[5] or 0
            elif k:
                st['reread_whole'] += 1
                if fts:
                    con.execute('DELETE FROM msgs WHERE rowid IN (SELECT rowid FROM msgs WHERE msgs MATCH ?)',
                                ('tag : "c%d"' % chat,))
                else:
                    con.execute('DELETE FROM msgs WHERE chat = ?', (chat,))
                con.execute('UPDATE chats SET prompts = 0, replies = 0, first_prompt = NULL, first_ts = NULL, '
                            'last_ts = NULL, custom_title = NULL, ai_title = NULL, folder = NULL WHERE id = ?',
                            (chat,))
                con.execute('DELETE FROM titles WHERE rowid = ?', (chat,))
            try:
                got = read_chat(path, start)
            except OSError:
                continue
            st['read'] += 1
            st['bytes_read'] += got['end'] - start
            st['bad_lines'] += got['bad']
            _store(con, chat, project, got, fts)
            st['messages_added'] += len(got['msgs'])
            con.execute('INSERT OR REPLACE INTO files(path, chat, size, mtime_ns, ino, offset, tail) '
                        'VALUES (?, ?, ?, ?, ?, ?, ?)',
                        (path, chat, s.st_size, s.st_mtime_ns, s.st_ino, got['end'], _tail(path, got['end'])))
            if i % 50 == 49:
                con.execute('COMMIT')
                con.execute('BEGIN')
        st['vanished'] = len(set(known) - on_disk)
        con.execute('COMMIT')
        st['chats'] = con.execute('SELECT count(*) FROM chats WHERE prompts + replies > 0').fetchone()[0]
        st['messages'] = con.execute('SELECT count(*) FROM msgs').fetchone()[0]
    except BaseException:
        if con.in_transaction:
            con.execute('ROLLBACK')
        raise
    finally:
        con.close()
    st['index_bytes'] = sum(os.path.getsize(p) for p in (index, index + '-journal', index + '-wal')
                            if os.path.exists(p))
    st['seconds'] = round(time.time() - t0, 3)
    return st


def _store(con, chat, project, got, fts):
    cur = con.execute('SELECT custom_title, ai_title, folder, first_prompt, first_ts, last_ts, prompts, replies '
                      'FROM chats WHERE id = ?', (chat,)).fetchone()
    custom, ai, folder, first_prompt, first_ts, last_ts, prompts, replies = cur
    rows = []
    for role, ts, text in got['msgs']:
        text = private_text(text)[:MAX_TEXT]
        if role == 'you':
            prompts += 1
            if first_prompt is None:
                first_prompt = text[:300]
        else:
            replies += 1
        rows.append((text, 'c%d' % chat, chat, role, ts) if fts else (text, text.lower(), chat, role, ts))
    if rows:
        if fts:
            con.executemany('INSERT INTO msgs(text, tag, chat, role, ts) VALUES (?, ?, ?, ?, ?)', rows)
        else:
            con.executemany('INSERT INTO msgs(text, low, chat, role, ts) VALUES (?, ?, ?, ?, ?)', rows)
    new_custom = private_text(got['custom']) or custom
    new_ai = private_text(got['ai']) or ai
    new_folder = folder or got['cwd']
    if got['first'] is not None:
        first_ts = got['first'] if first_ts is None else min(first_ts, got['first'])
        last_ts = got['last'] if last_ts is None else max(last_ts, got['last'])
    con.execute('UPDATE chats SET project = ?, folder = ?, custom_title = ?, ai_title = ?, first_prompt = ?, '
                'first_ts = ?, last_ts = ?, prompts = ?, replies = ? WHERE id = ?',
                (project, new_folder, new_custom, new_ai, first_prompt, first_ts, last_ts, prompts, replies, chat))
    if (new_custom, new_ai, new_folder) != (custom, ai, folder):
        t = _title_text({'custom_title': new_custom, 'ai_title': new_ai, 'folder': new_folder})
        con.execute('DELETE FROM titles WHERE rowid = ?', (chat,))
        if fts:
            con.execute('INSERT INTO titles(rowid, title) VALUES (?, ?)', (chat, t))
        else:
            con.execute('INSERT INTO titles(rowid, title, low) VALUES (?, ?, ?)', (chat, t, t.lower()))


# ---------------------------------------------------------------- search

def terms_of(query):
    """The words of a question that are searched for: lower case, common words dropped, each once."""
    words = re.findall(r'\w+', (query or '').lower())
    out = []
    for w in words:
        if w not in STOP and w not in out:
            out.append(w)
    if not out:
        out = [w for w in dict.fromkeys(words)]
    return out


def _date(ts):
    return time.strftime('%Y-%m-%d', time.localtime(ts)) if ts else None


_MARKS = re.compile(r'^(?:(?:#{1,6}|[-*+>])\s+)+')
_WORD = re.compile(r'\S+')


def fallback_title(text):
    """A chat's title when it has no name of yours or Claude Code's: its first prompt, white space made single,
    leading heading, list and quote marks ("# ", "## ", "- ", "> ") taken off, then the first 80 characters."""
    t = _MARKS.sub('', _SPACE.sub(' ', text or '').strip())
    return t[:TITLE_CHARS].rstrip()


def shown_title(custom, ai, first_prompt):
    return custom or ai or fallback_title(first_prompt) or '(untitled)'


def _matches(con, fts, words, total):
    """Every message that holds a query word: [(chat, rowid, ts, length in UTF-8 bytes, bit mask of the words
    it holds)], and for each word the chats whose title or folder name holds it. total: about how many
    messages the index holds. Each message is read once, and none is read when no query word is in it, unless
    more than a quarter of all messages hold one: then one pass over the table is cheaper than a lookup each."""
    out, in_title = [], []
    if fts:
        held = {}
        for i, w in enumerate(words):
            for (rid,) in con.execute('SELECT rowid FROM msgs WHERE msgs MATCH ?', ('text : "%s"' % w,)):
                held[rid] = held.get(rid, 0) | (1 << i)
            in_title.append(set(r[0] for r in con.execute('SELECT rowid FROM titles WHERE titles MATCH ?',
                                                          ('"%s"' % w,))))
        if held:
            if 4 * len(held) > total:
                rows = con.execute('SELECT rowid, chat, ts, length(CAST(text AS BLOB)) FROM msgs')
            else:
                rows = con.execute('SELECT rowid, chat, ts, length(CAST(text AS BLOB)) FROM msgs WHERE msgs MATCH ?',
                                   ('text : (%s)' % ' OR '.join('"%s"' % w for w in words),))
            for rid, chat, ts, length in rows:
                mask = held.get(rid)
                if mask:
                    out.append((chat, rid, ts, length, mask))
    else:
        found = ', '.join('instr(low, ?)' for _ in words)
        where = ' OR '.join('instr(low, ?) > 0' for _ in words)
        for row in con.execute('SELECT chat, rowid, ts, length(CAST(text AS BLOB)), %s FROM msgs WHERE %s'
                               % (found, where), list(words) + list(words)):
            mask = 0
            for i, at in enumerate(row[4:]):
                if at:
                    mask |= 1 << i
            out.append((row[0], row[1], row[2], row[3], mask))
        for w in words:
            in_title.append(set(r[0] for r in con.execute('SELECT rowid FROM titles WHERE instr(low, ?) > 0', (w,))))
    return out, in_title


def search(query, limit=5, index=None, marks=('[', ']')):
    """Chats that best match query, best first: [{'session', 'title', 'date', 'last', 'folder', 'project',
    'snippets', 'score', 'matched', 'alike', 'alike_last'}]. snippets holds at most two short pieces of the
    messages that hold the most query words, matches between `marks` ([brackets] unless other marks are given;
    the page passes two control characters it can tell from any text). matched is the date of the best of those
    messages. Chats with the same title in the same folder (a scripted run repeated every day) are one row:
    the best of them, with alike = how many others were folded into it and alike_last = the newest date among
    those (None when there are none)."""
    index = index or index_path()
    if not os.path.exists(index) or drop_old_index(index):
        return []
    words = terms_of(query)
    if not words:
        return []
    con = _connect(index, False)
    try:
        fts = dict(con.execute('SELECT k, v FROM meta')).get('fts') == '5'
        info = {}
        for r in con.execute('SELECT id, session, project, folder, custom_title, ai_title, first_prompt, first_ts, '
                             'last_ts, prompts + replies FROM chats WHERE prompts + replies > 0'):
            info[r[0]] = r
        n = len(info)
        if not n:
            return []
        found, in_title = _matches(con, fts, words, sum(r[9] for r in info.values()))
        per = {}                    # chat -> its messages that hold a query word
        for m in found:
            lst = per.get(m[0])
            if lst is not None:
                lst.append(m)
            elif m[0] in info:
                per[m[0]] = [m]
        in_title = [t & info.keys() for t in in_title]
        has = [len(t) for t in in_title]
        for chat, lst in per.items():
            union = 0
            for m in lst:
                union |= m[4]
            for i in range(len(words)):
                if union >> i & 1 and chat not in in_title[i]:
                    has[i] += 1
        idf = [math.log(1 + (n - df + 0.5) / (df + 0.5)) if df else 0.0 for df in has]
        whole = sum(idf)
        if not whole:
            return []
        worth = {}                  # word mask -> (score of a short message holding those words, how many words)
        msgs, score, cut = {}, {}, 1 + len(SHARE)
        for chat, lst in per.items():
            scored = []
            for _, rid, ts, length, mask in lst:
                w = worth.get(mask)
                if w is None:
                    held = sum(idf[i] for i in range(len(words)) if mask >> i & 1)
                    w = worth[mask] = (whole * (held / whole) ** COVER_POWER, bin(mask).count('1'))
                s = w[0] if length <= LONG_BYTES else w[0] * (float(LONG_BYTES) / length) ** LONG_POWER
                scored.append((s, w[1], ts or 0.0, rid))
            msgs[chat] = scored
            top = heapq.nlargest(cut, [m[0] for m in scored]) if len(scored) > cut else \
                sorted([m[0] for m in scored], reverse=True)
            score[chat] = top[0] + sum(f * v for f, v in zip(SHARE, top[1:]))
        for i, chats in enumerate(in_title):
            for chat in chats:
                score[chat] = score.get(chat, 0.0) + idf[i] * TITLE_WEIGHT
        ranked = sorted(score, key=lambda c: (-score[c], -(info[c][8] or 0), info[c][1], info[c][2]))
        rows, by_key = [], {}
        for c in ranked:
            r = info[c]
            title = shown_title(r[4], r[5], r[6])
            key = (title, r[3]) if r[3] else (title, None, r[2])
            row = by_key.get(key)
            if row is not None:
                row['alike'] += 1
                row['_alike_ts'] = max(row['_alike_ts'], r[8] or 0)
                continue
            if len(rows) >= max(0, int(limit)):
                continue                # past the last row shown: only the counts above still matter
            row = by_key[key] = {'session': r[1], 'title': title, 'date': _date(r[7]), 'last': _date(r[8]),
                                 'folder': r[3], 'project': r[2], 'score': round(score[c], 4), 'alike': 0,
                                 '_alike_ts': 0, '_chat': c}
            rows.append(row)
        for row in rows:
            c = row.pop('_chat')
            row['alike_last'] = _date(row.pop('_alike_ts')) if row['alike'] else None
            best = sorted(msgs.get(c, ()), key=lambda m: (-m[1], -m[0], -m[2], -m[3]))[:2]
            row['matched'] = _date(best[0][2]) if best else None
            row['snippets'] = _snippets(con, fts, [m[3] for m in best], words, info[c], marks)
        return rows
    finally:
        con.close()


def _snippets(con, fts, rowids, words, r, marks=('[', ']')):
    """Short pieces of the given messages, in that order, query words between marks."""
    got = []
    for rid in rowids:
        if fts:
            expr = 'text : (%s)' % ' OR '.join('"%s"' % w for w in words)
            row = con.execute("SELECT snippet(msgs, 0, ?, ?, '…', %d) FROM msgs WHERE msgs MATCH ? AND rowid = ?"
                              % SNIPPET_TOKENS, (marks[0], marks[1], expr, rid)).fetchone()
            piece = row[0] if row else None
        else:
            row = con.execute('SELECT text FROM msgs WHERE rowid = ?', (rid,)).fetchone()
            piece = _plain_snippet(row[0], words, marks) if row else None
        if piece:
            got.append(piece)
    if not got:
        got = [(r[4] or r[5] or r[6] or '')[:100]]
    return got[:2]


def _plain_snippet(text, words, marks):
    """The fallback's snippet: about SNIPPET_TOKENS words around the place where the most query words are
    within SNIPPET_SPAN characters, each match between marks."""
    low = text.lower()
    if len(low) != len(text):
        text = low                  # a letter whose lower case is longer: show the lower-cased text
    hits = []
    for i, w in enumerate(words):
        at, k = low.find(w), 0
        while at != -1 and k < 200:
            hits.append((at, at + len(w), i))
            at, k = low.find(w, at + len(w)), k + 1
    if not hits:
        return None
    hits.sort()
    best, best_n = 0, 0
    for a in range(len(hits)):
        seen = set()
        for b in range(a, len(hits)):
            if hits[b][1] - hits[a][0] > SNIPPET_SPAN:
                break
            seen.add(hits[b][2])
        if len(seen) > best_n:
            best, best_n = a, len(seen)
    first = hits[best][0]
    last = max(e for s, e, _ in hits[best:] if e - first <= SNIPPET_SPAN)
    r0 = text.rfind(' ', 0, max(0, first - 300)) + 1                 # only the words near the place, from a space
    r1 = text.find(' ', min(len(text), last + 300))
    toks = [(m.start(), m.end()) for m in _WORD.finditer(text, r0, len(text) if r1 == -1 else r1)]
    ti = next(k for k, (s, e) in enumerate(toks) if e > first)
    tj = next((k for k, (s, e) in enumerate(toks) if e >= last), len(toks) - 1)
    lo = max(0, ti - 2)
    hi = min(len(toks), max(tj + 1, lo + SNIPPET_TOKENS), lo + 2 * SNIPPET_TOKENS)
    a, b = toks[lo][0], toks[hi - 1][1]
    out, pos = [], a
    for s, e, _ in hits:
        if s < pos or e > b:
            continue
        out.append(text[pos:s] + marks[0] + text[s:e] + marks[1])
        pos = e
    out.append(text[pos:b])
    return ('…' if a > 0 else '') + ''.join(out) + ('…' if b < len(text) else '')


# ---------------------------------------------------------------- health of Claude Code's own memory index

LIMIT_LINES = 200
LIMIT_BYTES = 25 * 1024            # "25KB"; read as 25,600 bytes. 25,000 to 25,600 is reported as "maybe over"
LIMIT_BYTES_LOW = 25000
HEALTH_DOC = ('https://code.claude.com/docs/en/memory',
              'The first 200 lines of `MEMORY.md`, or the first 25KB, whichever comes first, are loaded at the '
              'start of every conversation. Content beyond that threshold is not loaded at session start.')
HEALTH_DOC2 = ('https://code.claude.com/docs/en/errors',
               'Only the content that loads counts toward the limits. YAML frontmatter and block-level HTML '
               'comments are stripped before the index is loaded, so they\'re excluded from the measurement.')


def loaded_part(raw):
    """The bytes of a MEMORY.md that count: without YAML frontmatter and block-level HTML comments (outside
    code fences)."""
    text = raw.decode('utf-8', 'replace').replace('\r\n', '\n')
    lines = text.split('\n')
    if lines and lines[0].strip() == '---':
        for i in range(1, len(lines)):
            if lines[i].strip() == '---':
                lines = lines[i + 1:]
                break
    out, fence, in_comment = [], None, False
    for line in lines:
        s = line.strip()
        if in_comment:
            if '-->' in s:
                in_comment = False
            continue
        if fence:
            out.append(line)
            if s.startswith(fence):
                fence = None
            continue
        if s.startswith('```') or s.startswith('~~~'):
            fence = s[:3]
            out.append(line)
            continue
        if s.startswith('<!--'):
            rest = s[4:]
            if '-->' in rest:
                if not rest.split('-->', 1)[1].strip():
                    continue            # a whole comment line
                out.append(line)
                continue
            in_comment = True
            continue
        out.append(line)
    return '\n'.join(out).encode('utf-8')


def memory_files(claude_dir=None, home=None):
    cd = common.claude_dir(claude_dir)
    out = []
    root = os.path.join(cd, 'projects')
    try:
        for e in sorted(os.scandir(root), key=lambda e: e.name):
            p = os.path.join(e.path, 'memory', 'MEMORY.md')
            if e.is_dir(follow_symlinks=False) and os.path.isfile(p):
                out.append({'path': p, 'project': e.name, 'where': 'project'})
    except OSError:
        pass
    try:
        with open(os.path.join(cd, 'settings.json'), 'rb') as f:
            s = json.loads(f.read().decode('utf-8'))
        d = s.get('autoMemoryDirectory') if isinstance(s, dict) else None
    except (OSError, ValueError, UnicodeDecodeError):
        d = None
    if isinstance(d, str) and (d.startswith('/') or d.startswith('~/')):
        d = os.path.join(home or common.home(), d[2:]) if d.startswith('~/') else d
        p = os.path.join(d, 'MEMORY.md')
        if os.path.isfile(p) and all(x['path'] != p for x in out):
            out.append({'path': p, 'project': None, 'where': 'autoMemoryDirectory'})
    return out


def health(claude_dir=None, home=None):
    """Each MEMORY.md and how it stands against the load limit. Reads only."""
    rows = []
    for f in memory_files(claude_dir, home):
        raw = common.read_bytes(f['path']) or b''
        part = loaded_part(raw)
        text = part.decode('utf-8', 'replace')
        lines = text.count('\n') + (1 if text and not text.endswith('\n') else 0)
        size = len(part)
        # what loads: the first 200 lines, then no more than 25KB of them; a line cut part way counts as lost
        kept_lines, used = 0, 0
        for line in text.split('\n')[:min(lines, LIMIT_LINES)]:
            n = len(line.encode('utf-8'))
            if used + n > LIMIT_BYTES:
                break
            used += n + 1
            kept_lines += 1
        over_lines = max(0, lines - LIMIT_LINES)
        over_bytes = max(0, size - LIMIT_BYTES)
        if over_lines or over_bytes:
            state = 'over'
        elif size > LIMIT_BYTES_LOW:
            state = 'maybe over'
        else:
            state = 'ok'
        rows.append(dict(f, lines=lines, bytes=size, file_bytes=len(raw), over_lines=over_lines,
                         over_bytes=over_bytes, lines_not_loaded=max(0, lines - kept_lines) if state == 'over' else 0,
                         state=state))
    return {'limit': {'lines': LIMIT_LINES, 'bytes': LIMIT_BYTES, 'bytes_low': LIMIT_BYTES_LOW},
            'doc': [{'url': HEALTH_DOC[0], 'quote': HEALTH_DOC[1]}, {'url': HEALTH_DOC2[0], 'quote': HEALTH_DOC2[1]}],
            'files': rows, 'over': sum(1 for r in rows if r['state'] == 'over'),
            'maybe_over': sum(1 for r in rows if r['state'] == 'maybe over')}


# ---------------------------------------------------------------- claude-mem, watching your chats only
#
# Everything below was read on 2026-11-13 from claude-mem's source at commit 0c488b5 (plugin v15.18.3) and
# Claude Code's hook docs. In short:
#
#   - claude-mem is a Claude Code plugin. Its hooks come from the plugin's hooks/hooks.json, so nothing
#     can stand in front of them short of editing the plugin's own files; Switchboard does not.
#   - Claude Code gives every hook that fires inside a helper (subagent, workflow agent) the fields
#     agent_id and agent_type. claude-mem drops a tool call that carries both, before any model call,
#     when its own setting CLAUDE_MEM_SKIP_SUBAGENT_OBSERVATIONS is "true" (off by default).
#   - CLAUDE_MEM_OBSERVER_ENABLED "false" stops new observation and session-summary model calls;
#     search and saved memories stay.
#   - Each hook is a new process that reads ~/.claude-mem/settings.json when it starts; a variable of the
#     same name in the hook's environment wins over the file.
#   - The observer is the Claude Agent SDK started in <data>/observer-sessions, so its transcripts land in
#     Claude Code's projects folder under that folder's name. That is what the weekly cap measures.

CM_PLUGIN = 'claude-mem@thedotmack'
CM_SKIP = 'CLAUDE_MEM_SKIP_SUBAGENT_OBSERVATIONS'
CM_SKIP_TYPES = 'CLAUDE_MEM_SKIP_AGENT_TYPES'
CM_OBSERVER = 'CLAUDE_MEM_OBSERVER_ENABLED'
CM_SKIP_TOOLS = 'CLAUDE_MEM_SKIP_TOOLS'
CM_EXCLUDED = 'CLAUDE_MEM_EXCLUDED_PROJECTS'
CM_DATA = 'CLAUDE_MEM_DATA_DIR'
CM_CONFIG = 'CLAUDE_MEM_CLAUDE_CONFIG_DIR'
CM_DEFAULTS = {CM_SKIP: 'false', CM_SKIP_TYPES: '', CM_OBSERVER: 'true',
               CM_SKIP_TOOLS: 'ListMcpResourcesTool,SlashCommand,Skill,TodoWrite,AskUserQuestion', CM_EXCLUDED: ''}
CM_SOURCE = 'https://github.com/thedotmack/claude-mem/tree/0c488b54e761d7f6f4091b3a6e1958712f13b19d'
CAP_DEFAULT = 10.0
WEEK = 7 * 86400

# What claude-mem registers in Claude Code (plugin/hooks/hooks.json) and what each one does with the input.
CM_HOOKS = (
    ('Setup', 'node scripts/version-check.js', 'checks its version; no model call'),
    ('SessionStart', 'claude-mem start / hook claude-code context',
     'starts its worker; on startup, clear and compact adds saved context to the chat'),
    ('UserPromptSubmit', 'claude-mem hook claude-code session-init', 'records the prompt; no model call'),
    ('PreToolUse (Read, Bash, Grep, Glob, memory tools)', 'claude-mem hook claude-code file-context',
     "adds a file's history; may refuse a whole-file Read of 32 KB or more it has notes on; skips helpers"),
    ('PostToolUse, PostToolUseFailure (every tool)', 'claude-mem hook claude-code observation',
     'sends the tool call to its observer model: the cost. Helpers are dropped first when '
     'CLAUDE_MEM_SKIP_SUBAGENT_OBSERVATIONS is "true"; nothing is sent when CLAUDE_MEM_OBSERVER_ENABLED is "false"'),
    ('Stop', 'claude-mem hook claude-code summarize',
     'a session summary by the observer model; skips helpers; off when CLAUDE_MEM_OBSERVER_ENABLED is "false"'),
    ('SubagentStop', 'claude-mem hook claude-code subagent-stop', 'notes that a helper finished; no model call'),
    ('SessionEnd', 'claude-mem hook claude-code session-end', 'closes the session'),
)

CM_INSTALL = (
    {'step': 'Needs Node.js 20.19 or later (20.x) or 22.12 or later; claude-mem installs Bun itself',
     'run': None},
    {'step': 'Install it for Claude Code, on your Claude plan, with no claude-mem account',
     'run': 'npx claude-mem install --ide claude-code --provider claude',
     'or': ['/plugin marketplace add thedotmack/claude-mem', '/plugin install claude-mem'],
     'then': 'Restart Claude Code.'},
    {'step': 'Optional: turn off its anonymous usage analytics (on by default)',
     'run': 'npx claude-mem telemetry disable'},
)
CM_REMOVE = ({'step': 'Remove claude-mem (plugin, configs and its hook launcher)', 'run': 'npx claude-mem uninstall'},)


def _coerce(v):
    """A setting as claude-mem reads it: JSON true/false become "true"/"false"."""
    if isinstance(v, bool):
        return 'true' if v else 'false'
    return v


def _expand(p, home):
    if p == '~':
        return home
    if p.startswith('~/'):
        return os.path.join(home, p[2:])
    return p


def _read_json(path):
    raw = common.read_bytes(path)
    if raw is None:
        return None
    try:
        obj = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def _nested(obj):
    """claude-mem keeps its keys under "env" when that object holds any CLAUDE_MEM_* key, else at the top."""
    env = obj.get('env') if isinstance(obj, dict) else None
    return isinstance(env, dict) and any(k.startswith('CLAUDE_MEM_') for k in env)


def _target(obj):
    if not isinstance(obj, dict):
        return {}
    return obj['env'] if _nested(obj) else obj


def project_folder_name(path):
    """Claude Code's folder under projects/ for a working directory: every character other than a letter or
    a digit becomes '-'."""
    return re.sub(r'[^A-Za-z0-9]', '-', path)


def cm_paths(environ=None, home=None):
    """claude-mem's data folder, settings file and observer folder, found the way claude-mem finds them."""
    environ = os.environ if environ is None else environ
    home = home or common.home()
    default = os.path.join(home, '.claude-mem')
    if environ.get(CM_DATA):
        data = _expand(environ[CM_DATA], home)
    else:
        v = _target(_read_json(os.path.join(default, 'settings.json')) or {}).get(CM_DATA)
        data = _expand(v, home) if isinstance(v, str) and v else default
    settings = os.path.join(data, 'settings.json')
    v = _target(_read_json(settings) or {}).get(CM_CONFIG)
    config = _expand(v, home) if isinstance(v, str) and v else (environ.get('CLAUDE_CONFIG_DIR')
                                                                or os.path.join(home, '.claude'))
    return {'data': data, 'settings': settings, 'observer': os.path.join(data, 'observer-sessions'),
            'observer_config': config}


def _hook_env(claude_dir, environ):
    """What a hook's environment holds for claude-mem's keys: Claude Code's user settings "env", then the
    environment Claude Code starts from (a settings value replaces a shell one)."""
    out = {}
    for k, v in (environ or {}).items():
        if k.startswith('CLAUDE_MEM_'):
            out[k] = (v, 'the environment Claude Code starts from')
    s = _read_json(os.path.join(common.claude_dir(claude_dir), 'settings.json')) or {}
    env = s.get('env') if isinstance(s.get('env'), dict) else {}
    for k, v in env.items():
        if k.startswith('CLAUDE_MEM_'):
            out[k] = (str(v), '"env" in %s' % os.path.join(common.claude_dir(claude_dir), 'settings.json'))
    return out


def cm_installed(claude_dir=None, environ=None, home=None):
    cd = common.claude_dir(claude_dir)
    ip = _read_json(os.path.join(cd, 'plugins', 'installed_plugins.json')) or {}
    plugins = ip.get('plugins') if isinstance(ip.get('plugins'), dict) else {}
    s = _read_json(os.path.join(cd, 'settings.json')) or {}
    enabled = s.get('enabledPlugins') if isinstance(s.get('enabledPlugins'), dict) else {}
    paths = cm_paths(environ, home)
    return {'registered': bool(plugins.get(CM_PLUGIN)), 'enabled': enabled.get(CM_PLUGIN),
            'settings_file': os.path.isfile(paths['settings']), 'data_folder': os.path.isdir(paths['data'])}


def _cm_doc(raw, path):
    obj = None
    if raw is not None:
        try:
            obj = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            obj = None
    inside = 'env' if isinstance(obj, dict) and _nested(obj) else None
    return common.JsonDoc(raw, path, inside), inside


def _cm_value(doc, key):
    m = doc.member(key)
    return (None, None) if m is None else (_coerce(m['value']), doc.text[m['v0']:m['v1']])


def _before_add(doc, raw):
    """What adding a member would lose, kept so taking it out again is exact: the empty object's inner text,
    and a blank file's bytes."""
    return {'empty_inner': doc.inner() if not doc.members else None,
            'blank_raw': raw.decode('utf-8') if raw is not None and not raw.strip() else None}


def cm_plan(on, cap_percent=None, claude_dir=None, state_path=None, environ=None, home=None, extra=None):
    """What turning managed claude-mem on or off would change. Changes nothing, runs nothing.

    on: Switchboard sets CLAUDE_MEM_SKIP_SUBAGENT_OBSERVATIONS to "true" in claude-mem's settings file (made
    if missing, so the gate is in place before the first session) and keeps a weekly cap of cap_percent of
    all Claude Code use (default 10). off: puts back every value Switchboard set, if it is still Switchboard's.

    extra: more of claude-mem's own settings to set the same way, as (key, JSON text, why). An install by
    Switchboard passes them (packs/claudemem_install.py); each is recorded and put back exactly like the gate.
    """
    environ = os.environ if environ is None else environ
    sp = state_path or common.default_state_path()
    paths = cm_paths(environ, home)
    path = paths['settings']
    out = {'on': bool(on), 'blocked': None, 'edits': [], 'forget': [], 'notes': [], 'install': [], 'remove': [],
           'cap_percent': None, 'settings_path': path, 'state_path': sp, 'inside': None, 'fingerprint': None,
           'state_sha': None,
           'made_file': False, 'made_dir': False, 'delete_file': False, 'installed': None, 'source': CM_SOURCE}
    try:
        st, raw_state = common.load_state(sp)
    except common.Refused as e:
        out['blocked'] = str(e)
        return out
    out['state_sha'] = common.sha(raw_state)
    rec = st.get('claude_mem') or {}
    owned = rec.get('owned') or {}
    out['installed'] = inst = cm_installed(claude_dir, environ, home)
    if on:
        cap = CAP_DEFAULT if cap_percent is None else cap_percent
        if isinstance(cap, bool) or not isinstance(cap, (int, float)) or not 0 < cap <= 100:
            out['blocked'] = 'The weekly cap must be a share above 0 and at most 100 (percent).'
            return out
        out['cap_percent'] = float(cap)
    if os.path.islink(path) or os.path.isdir(path):
        out['blocked'] = ('%s is a link or a folder. Switchboard does not edit linked files.' % path)
        return out
    raw = common.read_bytes(path)
    out['fingerprint'] = common.sha(raw)
    try:
        doc, inside = _cm_doc(raw, path)
        for k in (CM_SKIP, CM_OBSERVER) + tuple(e[0] for e in extra or ()):
            doc.member(k)
    except common.Refused as e:
        out['blocked'] = str(e)
        return out
    out['inside'] = inside
    env = _hook_env(claude_dir, environ)

    if on:
        if CM_SKIP in env and env[CM_SKIP][0] != 'true':
            out['blocked'] = ('%s=%s is set in %s. claude-mem reads that over its settings file, so the line '
                              'Switchboard would add could not take effect. Take it out there first.'
                              % (CM_SKIP, env[CM_SKIP][0], env[CM_SKIP][1]))
            return out
        if CM_OBSERVER in env:
            out['blocked'] = ('%s=%s is set in %s. claude-mem reads that over its settings file, so the weekly cap '
                              'could not turn watching off. Take it out there first.'
                              % (CM_OBSERVER, env[CM_OBSERVER][0], env[CM_OBSERVER][1]))
            return out
        val, cur_raw = _cm_value(doc, CM_SKIP)
        mine = owned.get(CM_SKIP)
        if CM_SKIP in env:
            out['notes'].append('%s is already "true" in %s; claude-mem uses that.' % (CM_SKIP, env[CM_SKIP][1]))
        elif val == 'true' and mine is None:
            out['notes'].append('%s is already "true" in %s; it stays yours.' % (CM_SKIP, path))
        elif val == 'true':
            pass
        elif mine is not None:
            out['forget'].append({'key': CM_SKIP, 'says': 'You changed %s since Switchboard set it.' % CM_SKIP})
            out['notes'].append('You set %s to %s since Switchboard set it; yours stays, so helpers are watched. '
                                'Set it to "true" yourself, or take the line out and turn this on again.'
                                % (CM_SKIP, cur_raw if cur_raw is not None else 'nothing'))
        else:
            out['edits'].append(dict(_before_add(doc, raw) if cur_raw is None else {}, **{
                'key': CM_SKIP, 'op': 'set', 'new_raw': '"true"', 'old_raw': cur_raw,
                'says': ('Sets %s to "true" in %s: claude-mem drops every tool call made by a helper or workflow '
                         'agent before it reaches its model, and keeps watching your own chats.' % (CM_SKIP, path))}))
            out['made_file'] = raw is None
            out['made_dir'] = not os.path.isdir(paths['data'])
        for key, want, why in extra or ():
            val, cur_raw = _cm_value(doc, key)
            mine = owned.get(key)
            if key in env:
                out['notes'].append('%s is set in %s; claude-mem uses that.' % (key, env[key][1]))
            elif mine is not None and cur_raw is not None and cur_raw == mine.get('new_raw'):
                pass
            elif mine is not None:
                out['forget'].append({'key': key, 'says': 'You changed %s since Switchboard set it.' % key})
            elif val is not None and val == _coerce(json.loads(want)):
                out['notes'].append('%s is already %s in %s; it stays yours.' % (key, want, path))
            else:
                out['edits'].append(dict(_before_add(doc, raw) if cur_raw is None else {}, **{
                    'key': key, 'op': 'set', 'new_raw': want, 'old_raw': cur_raw,
                    'says': 'Sets %s to %s in %s: %s.' % (key, want, path, why)}))
                out['made_file'] = out['made_file'] or raw is None
                out['made_dir'] = out['made_dir'] or not os.path.isdir(paths['data'])
        if not (inst['registered'] or inst['settings_file']):
            out['install'] = [dict(s) for s in CM_INSTALL]
            out['notes'].append('claude-mem is not installed yet. The gate is written first, so its first session is '
                                'already filtered.')
        elif inst['enabled'] is False:
            out['notes'].append('claude-mem is installed but turned off in Claude Code (enabledPlugins).')
        if raw is not None and out['edits'] and not os.access(path, os.W_OK):
            out['blocked'] = '%s is set read-only, so Switchboard leaves it alone.' % path
        return out

    # off
    if not rec:
        return out
    for key in sorted(owned, key=lambda k: (-(owned[k].get('at') or 0), k)):      # the last one set goes first
        own = owned[key]
        val, cur_raw = _cm_value(doc, key)
        if raw is not None and val == _coerce(json.loads(own['new_raw'])):
            out['edits'].append({'key': key, 'op': 'restore' if own.get('old_raw') is not None else 'remove',
                                 'new_raw': own.get('old_raw'), 'old_raw': cur_raw,
                                 'empty_inner': own.get('empty_inner'), 'blank_raw': own.get('blank_raw'),
                                 'says': ('Puts %s back to %s.' % (key, own['old_raw']) if own.get('old_raw')
                                          is not None else 'Takes out %s, which Switchboard added.' % key)})
        else:
            out['forget'].append({'key': key, 'says': '%s is not what Switchboard set any more; it stays as it is.'
                                                      % key})
    if out['edits'] and raw is not None:
        expect = json.loads(json.dumps(doc.obj))
        tgt = expect[inside] if inside else expect
        for e in out['edits']:
            if e['new_raw'] is None:
                tgt.pop(e['key'], None)
            else:
                tgt[e['key']] = json.loads(e['new_raw'])
        out['delete_file'] = bool(rec.get('made_file')) and expect == {}
        if not os.access(path, os.W_OK):
            out['blocked'] = '%s is set read-only, so Switchboard leaves it alone.' % path
    if inst['registered'] or inst['settings_file']:
        out['remove'] = [dict(s) for s in CM_REMOVE]
    if rec.get('paused'):
        out['notes'].append('The weekly cap had turned watching off; that is put back too.')
    return out


def _edit_settings(path, inside, edits, raw):
    """claude-mem's settings file after edits ([{'key', 'new_raw' (JSON text) or None to take it out}]),
    as bytes. Every step is checked by parsing the whole file again."""
    doc = common.JsonDoc(raw, path, inside)
    expect = json.loads(json.dumps(doc.obj))
    tgt = expect[inside] if inside else expect
    blank = None
    for e in edits:
        if e['new_raw'] is None:
            text = doc.remove(e['key'], e.get('empty_inner'))
            tgt.pop(e['key'], None)
            blank = e.get('blank_raw') if e.get('blank_raw') is not None else blank
        else:
            text = doc.set_raw(e['key'], e['new_raw'])
            tgt[e['key']] = json.loads(e['new_raw'])
        doc.text = doc.checked(text, expect)
        doc.parse()
    if blank is not None and expect == {}:
        return blank.encode('utf-8')          # the file was blank before Switchboard's first line
    return doc.text.encode('utf-8')


def _write_settings(plan_like, raw, new, delete, made_dir_path=None):
    path = plan_like['settings_path']
    if common.sha(common.read_bytes(path)) != common.sha(raw):          # last look before the swap
        raise common.Refused('%s changed while Switchboard was writing; nothing was changed.' % path)
    if delete:
        os.unlink(path)
        if made_dir_path:
            try:
                os.rmdir(made_dir_path)
            except OSError:
                pass            # something else lives there: it stays
        return
    d = os.path.dirname(path)
    if not os.path.isdir(d):
        common.make_private_dir(d)
    common.atomic_write(path, new, common.file_mode(path, 0o600))


def cm_apply(plan_):
    """Make a cm_plan() plan. Refuses (Refused, nothing written) when it is blocked, or when claude-mem's settings
    file or Switchboard's record changed since the plan was made. Returns {'changed': [paths]}."""
    if plan_.get('blocked'):
        raise common.Refused(plan_['blocked'])
    sp = plan_.get('state_path') or common.default_state_path()
    st, raw_state = common.load_state(sp)
    if common.sha(raw_state) != plan_['state_sha']:
        raise common.Refused("Switchboard's record changed since this plan was made. Make a new plan.")
    path = plan_['settings_path']
    raw = common.read_bytes(path)
    if common.sha(raw) != plan_['fingerprint']:
        raise common.Refused('%s changed since this plan was made (by you or by claude-mem). Nothing was written; '
                             'make a new plan.' % path)
    rec = st.get('claude_mem') or {}
    edits = plan_['edits']
    new = _edit_settings(path, plan_['inside'], edits, raw) if edits else raw
    wrote = False
    try:
        if edits:
            data_dir = os.path.dirname(path)
            _write_settings(plan_, raw, new, plan_.get('delete_file'),
                            data_dir if plan_.get('delete_file') and rec.get('made_dir') else None)
            wrote = True
        if plan_['on']:
            owned = dict(rec.get('owned') or {})
            for f in plan_['forget']:
                owned.pop(f['key'], None)
            for e in edits:
                owned[e['key']] = {'new_raw': e['new_raw'], 'old_raw': e['old_raw'], 'at': common.now(),
                                   'empty_inner': e.get('empty_inner'), 'blank_raw': e.get('blank_raw')}
            st['claude_mem'] = {'on': True, 'cap_percent': plan_['cap_percent'], 'settings_path': path,
                                'owned': owned, 'paused': rec.get('paused'),
                                'made_file': bool(rec.get('made_file') or plan_.get('made_file')),
                                'made_dir': bool(rec.get('made_dir') or plan_.get('made_dir')),
                                'at': common.now()}
        else:
            st['claude_mem'] = None
        common.save_state(sp, st)
    except BaseException:
        if wrote:
            try:
                if raw is None:
                    if os.path.lexists(path):
                        os.unlink(path)
                else:
                    common.make_private_dir(os.path.dirname(path))
                    common.atomic_write(path, raw, common.file_mode(path, 0o600))
            except OSError:
                pass
        raise
    return {'changed': [path] if wrote else []}


def cm_undo(claude_dir=None, state_path=None, environ=None, home=None):
    """Put back every claude-mem setting Switchboard changed (values the person changed since are left)."""
    return cm_apply(cm_plan(False, None, claude_dir, state_path, environ, home))


def cm_status(claude_dir=None, state_path=None, environ=None, home=None):
    environ = os.environ if environ is None else environ
    paths = cm_paths(environ, home)
    try:
        st, _ = common.load_state(state_path)
        rec = st.get('claude_mem')
    except common.Refused:
        rec = None
    obj = _target(_read_json(paths['settings']) or {})
    env = _hook_env(claude_dir, environ)

    def eff(k):
        if k in env:
            return env[k][0]
        v = _coerce(obj.get(k))
        return CM_DEFAULTS.get(k) if v is None else v
    return {'installed': cm_installed(claude_dir, environ, home), 'paths': paths, 'switch': bool(rec),
            'cap_percent': (rec or {}).get('cap_percent'), 'paused': (rec or {}).get('paused'),
            'helpers_dropped': eff(CM_SKIP) == 'true', 'watching': eff(CM_OBSERVER) != 'false',
            'hooks': [{'event': a, 'runs': b, 'does': c} for a, b, c in CM_HOOKS]}


# ---------------------------------------------------------------- the weekly cap

def cm_measure(since, until, claude_dir=None, environ=None, home=None, cache_path=None):
    """claude-mem's observer use as a share of all Claude Code use in [since, until), by weighted tokens
    (switchboard.usage.WEIGHT_FORMULA: the same weights as the total it is divided by, 1-hour writes and each
    model's own cache-read weight included)."""
    from switchboard import usage
    root = projects_root(claude_dir)
    paths = cm_paths(environ, home)
    cache_path = cache_path or os.path.join(common.packs_dir(), 'claude-mem-usage-cache.json')

    def observer_weight(proj_root):
        name = project_folder_name(paths['observer'])
        w, reqs, seen = 0.0, 0, set()
        try:
            dirs = [e.path for e in os.scandir(proj_root) if e.is_dir(follow_symlinks=False)
                    and (e.name == name or (len(name) > 200 and e.name.startswith(name[:200])))]
        except OSError:
            dirs = []
        for d in dirs:
            for dp, dn, fn in os.walk(d):
                dn.sort()
                for f in sorted(fn):
                    if not f.endswith('.jsonl'):
                        continue
                    try:
                        data = usage.read_transcript(os.path.join(dp, f))
                    except OSError:
                        continue
                    for r in data['reqs']:
                        if r[0] in seen or r[1] is None or not since <= r[1] < until:
                            continue
                        seen.add(r[0])
                        # With its 1-hour writes, and its cache reads at its own model's weight.
                        low = r[6] if usage.cache_read_weight(r[2]) != usage.WEIGHTS['cache_read'] else 0
                        w += usage.weight(list(r[3:7]) + [r[10] if len(r) > 10 else 0, low])
                        reqs += 1
        return w, reqs, dirs

    obs, reqs, dirs = observer_weight(root)
    total = usage.summarize(root, since, until, cache_path)['totals']['weight']
    other = os.path.join(paths['observer_config'], 'projects')
    if os.path.realpath(other) != os.path.realpath(root) and os.path.isdir(other):
        w2, r2, d2 = observer_weight(other)
        obs, reqs, dirs, total = obs + w2, reqs + r2, dirs + d2, total + w2
    return {'since': since, 'until': until, 'observer_weight': obs, 'observer_requests': reqs, 'total_weight': total,
            'share': (100.0 * obs / total) if total else 0.0, 'observer_folders': dirs}


def cm_cap_check(now=None, claude_dir=None, state_path=None, environ=None, home=None, cache_path=None,
                 measured=None):
    """Hold claude-mem to its weekly cap: over the line in the last 7 days, turn watching off
    (CLAUDE_MEM_OBSERVER_ENABLED "false"); back at or under it, put the value back. Only a value Switchboard set
    is ever put back. Returns {'action': None|'pause'|'resume'|'forget', 'share', 'cap', 'says'}."""
    environ = os.environ if environ is None else environ
    now = time.time() if now is None else now
    sp = state_path or common.default_state_path()
    st, raw_state = common.load_state(sp)
    rec = st.get('claude_mem')
    if not rec or not rec.get('cap_percent'):
        return {'action': None, 'share': None, 'cap': None, 'says': 'No weekly cap is set.'}
    m = measured if measured is not None else cm_measure(now - WEEK, now, claude_dir, environ, home, cache_path)
    share, cap = m['share'], rec['cap_percent']
    path = rec['settings_path']
    if os.path.islink(path) or os.path.isdir(path):
        return {'action': None, 'share': share, 'cap': cap, 'says': '%s is a link or a folder; left alone.' % path}
    raw = common.read_bytes(path)
    if raw is None:
        return {'action': None, 'share': share, 'cap': cap,
                'says': "claude-mem's settings file (%s) is gone; nothing to change." % path}
    doc, inside = _cm_doc(raw, path)
    val, cur_raw = _cm_value(doc, CM_OBSERVER)
    owned = dict(rec.get('owned') or {})
    paused = rec.get('paused')
    env = _hook_env(claude_dir, environ)
    out = {'action': None, 'share': share, 'cap': cap, 'says': None, 'measured': m}
    edits = []
    if not paused and share > cap:
        if CM_OBSERVER in env:
            out['says'] = ('Over the cap (%.1f%% > %g%%), but %s is set in %s, which claude-mem reads first; '
                           'Switchboard cannot turn watching off.' % (share, cap, CM_OBSERVER, env[CM_OBSERVER][1]))
            return out
        if val == 'false':
            out['says'] = 'Over the cap, and watching is already off (set by you).'
            return out
        keep = _before_add(doc, raw) if cur_raw is None else {'empty_inner': None, 'blank_raw': None}
        edits = [{'key': CM_OBSERVER, 'new_raw': '"false"', 'old_raw': cur_raw}]
        owned[CM_OBSERVER] = dict(keep, new_raw='"false"', old_raw=cur_raw, at=now)
        rec['paused'] = {'at': now, 'share': round(share, 3)}
        out['action'] = 'pause'
        out['says'] = ('claude-mem used %.1f%% of Claude Code use in the last 7 days, over your %g%%. Watching is '
                       'off until it falls back to %g%%.' % (share, cap, cap))
    elif paused and share <= cap:
        own = owned.pop(CM_OBSERVER, None)
        rec['paused'] = None
        if own is not None and val == 'false':
            edits = [{'key': CM_OBSERVER, 'new_raw': own.get('old_raw'), 'old_raw': cur_raw,
                      'empty_inner': own.get('empty_inner'), 'blank_raw': own.get('blank_raw')}]
            out['action'] = 'resume'
            out['says'] = 'claude-mem is back at %.1f%%, within your %g%%; watching is on again.' % (share, cap)
        else:
            out['action'] = 'forget'
            out['says'] = '%s was changed since Switchboard turned watching off; it stays as it is.' % CM_OBSERVER
    else:
        out['says'] = ('Watching is off by the cap (%.1f%% > %g%%).' % (share, cap) if paused
                       else 'Within the cap (%.1f%% of %g%%).' % (share, cap))
        return out
    rec['owned'] = owned
    st['claude_mem'] = rec
    wrote = False
    try:
        if edits:
            new = _edit_settings(path, inside, edits, raw)
            _write_settings({'settings_path': path}, raw, new, False)
            wrote = True
        st2, raw2 = common.load_state(sp)
        if common.sha(raw2) != common.sha(raw_state):
            raise common.Refused("Switchboard's record changed during the check; nothing was changed.")
        common.save_state(sp, st)
    except BaseException:
        if wrote:
            try:
                common.atomic_write(path, raw, common.file_mode(path, 0o600))
            except OSError:
                pass
        raise
    return out


# ---------------------------------------------------------------- what claude-mem would send to its model

def _glob_re(pattern, home):
    p = _expand(pattern.replace('\\', '/'), home).replace('\\', '/')
    rx = re.sub(r'[.+^${}()|[\]\\]', lambda m: '\\' + m.group(0), p)
    rx = re.sub(r'(^|/)\*\*(?=/)', lambda m: m.group(1) + '\0D', rx)
    rx = rx.replace('**', '\0G').replace('*', '[^/]*').replace('?', '[^/]').replace('\0G', '.*')
    rx = rx.replace('\0D/', '(?:.*/)?')
    return re.compile('^%s$' % rx, re.S)


def _excluded(cwd, patterns, home):
    path = cwd.replace('\\', '/')
    base = os.path.basename(path)
    for raw in (patterns or '').split(','):
        pat = raw.strip()
        if not pat:
            continue
        try:
            if _glob_re(pat, home).match(path) or _glob_re(pat, home).match(base):
                return True
            if pat.replace('\\', '/').endswith('/**') and _glob_re(pat[:-3], home).match(path.rstrip('/')):
                return True
        except re.error:
            continue
    return False


def _agent_field(v):
    return v if isinstance(v, str) and 0 < len(v) <= 128 else None


def cm_would_observe(event, settings=None, environ=None, home=None, cwd_fallback=None):
    """Would claude-mem send this Claude Code hook input to its observer model? A mirror of its source at
    commit 0c488b5, for a PostToolUse or PostToolUseFailure input:

      src/cli/hook-command.ts           observation off when CLAUDE_MEM_OBSERVER_ENABLED is "false", or the
                                        hook environment has CLAUDE_MEM_DISABLE_TOOL_HOOKS=1 or
                                        CLAUDE_MEM_DISABLE_OBSERVATION=1 (src/shared/hook-constants.ts)
      src/cli/adapters/claude-code.ts   agent_id / agent_type read when 1-128 characters
      src/cli/handlers/observation.ts   no tool name or no cwd: nothing; project not tracked: nothing;
                                        shouldSkipAgentObservation: nothing
      src/shared/should-track-project.ts  CLAUDE_MEM_INTERNAL=1, or a cwd in observer-sessions or in
                                        claude-mem's own plugin folders: nothing; CLAUDE_MEM_EXCLUDED_PROJECTS globs
      src/shared/subagent-predicate.ts  a helper's event carries BOTH agent_id and agent_type
      src/shared/should-skip-agent-observation.ts  CLAUDE_MEM_SKIP_SUBAGENT_OBSERVATIONS "true", or the type in
                                        CLAUDE_MEM_SKIP_AGENT_TYPES
      src/shared/is-tool-skipped.ts     CLAUDE_MEM_SKIP_TOOLS (worker side), case-insensitive

    Not covered: its memory-tool self-calls (isRecursiveMemoryTool), resolveHookProjectPath beyond normpath, and
    the worker's later steps. This is a reading of the source, tested on recorded inputs; only a live install
    proves claude-mem behaves the same.
    settings: claude-mem's settings object (flat or nested); environ: the hook's environment (wins over it);
    cwd_fallback: the hook process's own folder, used when the input has no cwd (None: assume it is tracked).
    """
    environ = environ or {}
    home = home or common.home()
    eff = dict(CM_DEFAULTS)
    for k, v in _target(settings or {}).items():
        eff[k] = _coerce(v)
    for k, v in environ.items():
        if k.startswith('CLAUDE_MEM_'):
            eff[k] = v
    for k in (CM_SKIP_TYPES, CM_EXCLUDED, CM_SKIP_TOOLS):
        if not isinstance(eff.get(k), str):
            eff[k] = ''             # not a string: this mirror reads it as empty (claude-mem's own handling not checked)
    if event.get('hook_event_name') not in ('PostToolUse', 'PostToolUseFailure'):
        return False
    # src/shared/hook-constants.ts isToolHookDisabledByEnv: environment-only opt-outs, exactly "1"
    if environ.get('CLAUDE_MEM_DISABLE_TOOL_HOOKS') == '1' or environ.get('CLAUDE_MEM_DISABLE_OBSERVATION') == '1':
        return False
    if eff.get(CM_OBSERVER) == 'false':
        return False
    tool = None
    for k in ('tool_name', 'toolName'):          # adapter: firstFilledString(r.tool_name, r.toolName)
        if isinstance(event.get(k), str) and event.get(k):
            tool = event[k]
            break
    if not tool:
        return False
    cwd = event.get('cwd') if isinstance(event.get('cwd'), str) and event.get('cwd') else cwd_fallback
    if environ.get('CLAUDE_MEM_INTERNAL') == '1':
        return False
    if cwd and not _tracked(os.path.normpath(cwd), eff, environ, home):
        return False
    return _agent_and_tool(event, eff, tool)


def _tracked(cwd, eff, environ, home):
    """src/shared/should-track-project.ts, after the CLAUDE_MEM_INTERNAL check."""
    data = _expand(environ[CM_DATA], home) if environ.get(CM_DATA) else (
        _expand(eff[CM_DATA], home) if isinstance(eff.get(CM_DATA), str) and eff.get(CM_DATA)
        else os.path.join(home, '.claude-mem'))
    config = environ.get('CLAUDE_CONFIG_DIR') or os.path.join(home, '.claude')
    for root in (os.path.join(data, 'observer-sessions'),
                 os.path.join(config, 'plugins', 'cache', 'thedotmack', 'claude-mem'),
                 os.path.join(config, 'plugins', 'marketplaces', 'thedotmack', 'plugin')):
        root = os.path.normpath(root)
        if cwd == root or cwd.startswith(root + os.sep):
            return False
    return not _excluded(cwd, eff.get(CM_EXCLUDED), home)


def _agent_and_tool(event, eff, tool):
    """src/shared/should-skip-agent-observation.ts and src/shared/is-tool-skipped.ts."""
    agent_id = _agent_field(event.get('agent_id')) or _agent_field(event.get('agentId'))
    agent_type = (_agent_field(event.get('agent_type')) or _agent_field(event.get('agentType'))
                  or _agent_field(event.get('subagentType')))
    if agent_id and agent_type:
        if eff.get(CM_SKIP) == 'true':
            return False
        if agent_type in [t.strip() for t in (eff.get(CM_SKIP_TYPES) or '').split(',') if t.strip()]:
            return False
    skip_tools = set(t.strip().lower() for t in (eff.get(CM_SKIP_TOOLS) or '').split(',') if t.strip())
    return tool.lower() not in skip_tools


# ---------------------------------------------------------------- the switch for search

def search_status(index=None):
    index = index or index_path()
    if not os.path.exists(index) or drop_old_index(index):
        return {'on': False, 'index': index}
    con = _connect(index, False)
    try:
        chats = con.execute('SELECT count(*) FROM chats WHERE prompts + replies > 0').fetchone()[0]
        msgs = con.execute('SELECT count(*) FROM msgs').fetchone()[0]
        fts = dict(con.execute('SELECT k, v FROM meta')).get('fts') == '5'
    finally:
        con.close()
    return {'on': True, 'index': index, 'chats': chats, 'messages': msgs, 'fts5': fts,
            'bytes': os.path.getsize(index)}


def search_on(root=None, index=None):
    """Turn search on: build the index (or bring it up to date). Reads Claude Code's history; writes only the
    index in Switchboard's data folder."""
    return update_index(root, index)


def search_off(index=None):
    """Turn search off: delete the index (Switchboard's own file; Claude Code's history is not touched)."""
    return erase(index)


def erase(index=None):
    """Erase the search index: its file and SQLite's -journal, -wal and -shm beside it, each written over with zeros
    and then removed, so its text is not left behind in the file's old space. Claude Code's chats are not touched;
    search builds the index again the next time it is used. {'removed': [paths], 'bytes': the size of what was
    erased}. A link in place of any of them is refused (common.Refused) before anything changes: what it points to is
    not Switchboard's to write or remove. A file that cannot be removed raises OSError."""
    index = index or index_path()
    paths = (index, index + '-journal', index + '-wal', index + '-shm')
    for p in paths:
        if os.path.islink(p):
            raise common.Refused('The search index at %s is a link, so Switchboard leaves it and the file it points '
                                 'to as they are; remove them yourself.' % p)
    gone, size = [], 0
    for p in paths:
        try:
            st = os.lstat(p)
        except OSError:
            continue
        if stat.S_ISDIR(st.st_mode):
            continue
        if stat.S_ISREG(st.st_mode):
            size += st.st_size
            _zero(p, st.st_size)
        os.unlink(p)
        gone.append(p)
    return {'removed': gone, 'bytes': size}


def _zero(path, size):
    """Write zeros over a file's bytes, in place, and wait for the disk. Never follows a link."""
    try:
        fd = os.open(path, os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0))
    except OSError:
        return
    try:
        block = b'\0' * min(size, 1 << 20)
        left = size
        while left > 0:
            left -= os.write(fd, block[:min(left, len(block))])
        os.fsync(fd)
    except OSError:
        pass                        # removed all the same
    finally:
        os.close(fd)


def drop_old_index(index=None):
    """Erase an index made by an older build (another schema number): it may hold text this build keeps out. Safe
    when the page starts: a missing, part-made or busy file is left as it is, it waits at most a fraction of a second
    for another process, and it never raises. True when it was erased."""
    index = index or index_path()
    try:
        if not os.path.isfile(index):
            return False
        con = _connect(index, False)
        try:
            con.execute('PRAGMA busy_timeout = 200')
            got = dict(con.execute('SELECT k, v FROM meta'))
        finally:
            con.close()
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return False                # no meta table yet (a first build part-way), not a database, or held by another
    if got.get('schema') == str(SCHEMA):
        return False
    try:
        erase(index)
    except (OSError, common.Refused):
        return False                # the next one to open it tries again; a link is never followed
    return True


def _size_words(n):
    if n >= 1000000:
        return '%.1f MB' % (n / 1e6)
    return '%d KB' % max(1, round(n / 1e3))


def erase_line(got):
    """The one line `erase` prints for erase()'s answer."""
    if got['removed']:
        return ('Erased the search index of your past chats (%s). Search builds it again the next time you use it.'
                % _size_words(got['bytes']))
    return 'There is no search index of your past chats to erase. Search builds it the next time you use it.'


# ---------------------------------------------------------------- command line

def _print_results(results, out):
    if not results:
        out.write('No chat matched.\n')
        return
    for i, r in enumerate(results, 1):
        out.write('%d. %s  ·  %s  ·  %s\n' % (i, r['title'], r['date'] or '?', r['folder'] or r['project'] or '?'))
        if r.get('alike'):
            out.write('     and %d more like it, the last on %s\n' % (r['alike'], r['alike_last'] or '?'))
        if r.get('matched') and r['matched'] != r['date']:
            out.write('     best match on %s\n' % r['matched'])
        for s in r['snippets']:
            out.write('     %s\n' % s)
        out.write('     claude --resume %s\n' % r['session'])


def main(argv=None, out=None):
    out = out or sys.stdout
    ap = argparse.ArgumentParser(prog='python3 -m switchboard.packs.memory')
    sub = ap.add_subparsers(dest='cmd')
    s = sub.add_parser('search', help='search past chats')
    s.add_argument('query')
    s.add_argument('--limit', type=int, default=5)
    s.add_argument('--no-update', action='store_true', help='search the index as it is')
    s.add_argument('--json', action='store_true')
    s.add_argument('--index', default=None, help=argparse.SUPPRESS)
    s.add_argument('--root', default=None, help=argparse.SUPPRESS)
    i = sub.add_parser('index', help='bring the search index up to date')
    i.add_argument('--index', default=None, help=argparse.SUPPRESS)
    i.add_argument('--root', default=None, help=argparse.SUPPRESS)
    e = sub.add_parser('erase', help='erase the search index (your chats are not touched)')
    e.add_argument('--index', default=None, help=argparse.SUPPRESS)
    h = sub.add_parser('health', help="check Claude Code's memory index files (reads only)")
    h.add_argument('--json', action='store_true')
    sub.add_parser('claude-mem', help='what claude-mem would watch here (reads only)')
    a = ap.parse_args(argv)
    if a.cmd == 'search':
        if not a.no_update:
            update_index(a.root, a.index)
        res = search(a.query, a.limit, a.index)
        if a.json:
            out.write(json.dumps(res, indent=1, ensure_ascii=False) + '\n')
        else:
            _print_results(res, out)
        return 0
    if a.cmd == 'index':
        out.write(json.dumps(update_index(a.root, a.index), indent=1) + '\n')
        return 0
    if a.cmd == 'erase':
        try:
            out.write(erase_line(erase(a.index)) + '\n')
        except common.Refused as e:
            out.write('%s\n' % e)
            return 1
        except OSError as e:
            out.write('Could not remove the search index of your past chats (%s): %s.\n'
                      % (getattr(e, 'filename', None) or a.index or index_path(), e.strerror or e))
            return 1
        return 0
    if a.cmd == 'health':
        h = health()
        if a.json:
            out.write(json.dumps(h, indent=1) + '\n')
        else:
            out.write('Loads the first %d lines or 25KB of each MEMORY.md (%s).\n' % (LIMIT_LINES, HEALTH_DOC[0]))
            for r in h['files']:
                out.write('%-10s %4d lines %7d bytes  %s\n' % (r['state'], r['lines'], r['bytes'], r['path']))
            out.write('%d over, %d maybe over, of %d.\n' % (h['over'], h['maybe_over'], len(h['files'])))
        return 0
    if a.cmd == 'claude-mem':
        out.write(json.dumps(cm_status(), indent=1) + '\n')
        return 0
    ap.print_help(out)
    return 2


if __name__ == '__main__':
    sys.exit(main())
