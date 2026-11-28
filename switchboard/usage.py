"""The work under History: where your Claude Code time went, read from this Mac's own history.

Reads Claude Code's local transcripts. Nothing leaves the Mac, no model is called,
and no message text, prompt, email or token is kept, cached or printed. What is
kept: numbers, ids (hashed), model names, tool names, chat titles and helper labels.

What the history looks like (Claude Code, 2026-11):

    <root>/<project>/<session>.jsonl                                   your chat
    <root>/<project>/<session>/subagents/agent-<id>.jsonl              a helper the chat started
    <root>/<project>/<session>/subagents/workflows/wf_<id>/agent-<id>.jsonl   a workflow agent
    each agent-<id>.jsonl has a sibling agent-<id>.meta.json (agentType, description,
    workflowPhase, model, spawnDepth)
    <root>/<project>/<session>/workflows/wf_<id>.json                  the workflow's run record
                                                                       (only workflowName is read)

How it counts:

- One request is one (message.id, requestId) pair on a "type":"assistant" line. Claude Code
  writes a reply once per content block, and a fork copies its parent's requests into its
  own file, so the same pair can sit on many lines in many files. It counts once.
- A request's tokens are the largest value seen for each usage field, and its time is the
  earliest timestamp seen. Timestamps are UTC; one without a zone is read as UTC.
- A copied request belongs to the copy nearest the chat: the chat file first, then agents
  by spawnDepth, then by path order.
- A tool call is one tool_use id, counted once.
- Lines whose model is "<synthetic>" are Claude Code's own notices (errors, stops) with no
  usage. They are not requests and are skipped.
- A request is in the window when since_ts <= its time < until_ts, so a window can cut
  through a chat or a workflow.
- When asked (series_since), the same pass also puts each request in its local hour and its
  local day, from series_since to until_ts: the series the page draws under the accounts. A
  bucket holds since <= time < end like the window, so the hours and the days each add up to
  the requests from series_since to until_ts, and every request is in exactly one hour.

Shares are by weighted tokens (WEIGHTS below), not by Anthropic's own limit accounting,
which is not published per model. The weights are today's prices against an input token of
the request's own model: a cache read is 0.05 on Opus 5.5 and Sonnet 5.5 ($0.20 against $4,
$0.10 against $2) and on any model whose price is not known, 0.025 on Fable 5.2 and Mythos 5.2
($0.20 against $8; CACHE_READ_BY_MODEL); a cache write 1.25 for a 5-minute write and 2 for a
1-hour one (WRITE_1H; a write whose kind the transcript doesn't say counts as a 5-minute one),
output 5. Token counts by kind stay in the result (cache_write_1h is the part of cache_write
written for an hour, cache_read_low the part of cache_read read at 0.025), so the weighting can
change later. A Fable token and an Opus token of the same kind still count the same.

Speed: a per-file cache keyed on (path, size, mtime) keeps only per-file numbers. A file
that changed is read again whole. The cache is JSON, written atomically, rebuilt if broken.

Manual check:  python3 -m switchboard.usage --days 7 [--json]
"""
import argparse
import bisect
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

CACHE_VERSION = 2              # 2: each request also keeps its 1-hour cache writes (weights at today's prices)

TOKEN_KINDS = ('input', 'output', 'cache_write', 'cache_read')
WEIGHTS = {'input': 1.0, 'output': 5.0, 'cache_write': 1.25, 'cache_read': 0.05}
WRITE_1H = 2.0                      # a 1-hour cache write, against an input token (a 5-minute one is WEIGHTS' 1.25)
# The models whose cache read has a weight of its own, against an input token of the same model. From Anthropic's
# published model prices (its API reference, read 2026-11-03), dollars a million tokens: Fable 5.2 and Mythos 5.2
# input $8, cache read $0.20 (0.025); Opus 5.5 $4 and $0.20, Sonnet 5.5 $2 and $0.10 (both WEIGHTS' 0.05). Output,
# 5-minute and 1-hour writes are the same against input on all of them. A model not named here keeps 0.05.
CACHE_READ_LOW = 0.025
CACHE_READ_BY_MODEL = {'claude-fable-5-2': CACHE_READ_LOW, 'claude-mythos-5-2': CACHE_READ_LOW}
# What a request keeps: cache_write_1h is the part of cache_write kept an hour, cache_read_low the part of cache_read
# read on a model of CACHE_READ_BY_MODEL.
TOKENS = TOKEN_KINDS + ('cache_write_1h', 'cache_read_low')
WEIGHT_FORMULA = ('input + 1.25 x cache write (2 x for a 1-hour write) + 0.05 x cache read '
                  '(0.025 x on Fable 5.2 and Mythos 5.2) + 5 x output')
WEIGHT_NOTE = ("Shares are by weighted tokens (%s), not by Anthropic's own limit accounting, "
               'which is not published per model.' % WEIGHT_FORMULA)

FAMILIES = ('Fable', 'Opus', 'Sonnet', 'Haiku', 'Other')   # biggest first
WHO = ('chat', 'helpers', 'workflows')
WHO_LABEL = {'chat': 'Your chat turns', 'helpers': 'Helpers', 'workflows': 'Workflows'}
KINDS = ('build', 'fix', 'research', 'review', 'docs', 'tests', 'other', 'not labelled')
UNSET_MODELS = (None, '', 'inherit')

# What kind of work a request was. One table; the first row that decides wins.
#
# For a helper or workflow agent, the "text" rows are tried on its workflowPhase first, then
# on its label (description), then on its agentType. Words match whole words, any case, in
# the text split on anything that is not a letter or a digit. If no text row decides, the
# "tools" rows look at every tool the agent called. Still undecided: "other" if it had a
# phase or label, "not labelled" if not.
# A chat turn (from one message in until the next) has no phase or label, so only the
# "tools" rows apply to it, then "not labelled".
KIND_RULES = (
    # looks at     decides when it finds any of these                                  kind
    ('text', ('fix', 'fixes', 'fixed', 'fixer', 'fixing', 'bug', 'bugs', 'bugfix', 'debug',
              'debugging', 'repair', 'patch', 'hotfix', 'regression'),                    'fix'),
    ('text', ('test', 'tests', 'testing', 'tester', 'e2e', 'unittest', 'unittests', 'pytest'),
                                                                                         'tests'),
    ('text', ('review', 'reviews', 'reviewer', 'reviewing', 'verify', 'verifier', 'verification',
              'check', 'checks', 'checker', 'checking', 'audit', 'auditor', 'critique', 'critic',
              'judge', 'refute', 'qa', 'validate', 'validation', 'proofread'),             'review'),
    ('text', ('doc', 'docs', 'documentation', 'readme', 'draft', 'drafts', 'writeup', 'notes',
              'summary', 'summarize', 'memo', 'changelog'),                                'docs'),
    ('text', ('research', 'researcher', 'read', 'reader', 'reading', 'scout', 'find', 'explore',
              'explorer', 'search', 'survey', 'investigate', 'investigation', 'lookup', 'study',
              'sources', 'fetch'),                                                         'research'),
    ('text', ('build', 'builder', 'building', 'implement', 'implementation', 'implementer',
              'code', 'coder', 'coding', 'scaffold', 'refactor', 'feature', 'port', 'migrate',
              'wire', 'wiring'),                                                           'build'),
    ('tools any', ('Edit', 'Write', 'MultiEdit', 'NotebookEdit'),                          'build'),
    ('tools only', ('Read', 'Grep', 'Glob', 'LS', 'WebFetch', 'WebSearch', 'ToolSearch',
                    'NotebookRead'),                                                       'research'),
    ('labelled', (),                                                                       'other'),
    ('always', (),                                                                         'not labelled'),
)

_WORD = re.compile(r'[a-z0-9]+')
_TS = re.compile(r'(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:[.,](\d+))?\s*'
                 r'(Z|z|[+-]\d{2}(?::?\d{2})?)?$')


# ---------------------------------------------------------------- small pure helpers

def weighted(tokens):
    """{input, output, cache_write, cache_read}: each kind's weighted tokens, a 1-hour write at WRITE_1H and a cache
    read on a model of CACHE_READ_BY_MODEL (cache_read_low) at CACHE_READ_LOW. From a dict or a list in TOKENS order
    (a 4-list: no 1-hour writes; a list without the sixth: every cache read at 0.05)."""
    if not isinstance(tokens, dict):
        tokens = dict(zip(TOKENS, tokens))
    out = dict((k, WEIGHTS[k] * (tokens.get(k) or 0)) for k in TOKEN_KINDS)
    out['cache_write'] += (WRITE_1H - WEIGHTS['cache_write']) * (tokens.get('cache_write_1h') or 0)
    out['cache_read'] += (CACHE_READ_LOW - WEIGHTS['cache_read']) * (tokens.get('cache_read_low') or 0)
    return out


def weight(tokens):
    """Weighted tokens for a dict or a list in TOKENS order (a 4-list in TOKEN_KINDS order has no 1-hour writes)."""
    return sum(weighted(tokens).values())


_LOW_MODELS = tuple(re.compile(re.escape(m) + r'(?![0-9])') for m in sorted(CACHE_READ_BY_MODEL))


def cache_read_weight(model):
    """A cache read's weight on this model, against its own input token. The model id is matched the way family()
    matches, any case and anywhere in the id, so 'claude-fable-5-2[1m]' is Fable 5.2; a following digit is a
    different model ('claude-fable-5-20' is not 'claude-fable-5-2'). Unknown or missing: WEIGHTS' 0.05."""
    m = model.lower() if isinstance(model, str) else ''
    for name, pattern in zip(sorted(CACHE_READ_BY_MODEL), _LOW_MODELS):
        if pattern.search(m):
            return CACHE_READ_BY_MODEL[name]
    return WEIGHTS['cache_read']


def family(model):
    m = (model or '').lower() if isinstance(model, str) else ''
    for name in FAMILIES[:-1]:
        if name.lower() in m:
            return name
    return 'Other'


_DAYS = {}


def parse_ts(value):
    """Epoch seconds from an ISO timestamp (no zone means UTC), or None."""
    if not isinstance(value, str):
        return None
    # Fast path for the one shape Claude Code writes, 2026-11-10T09:00:05.123Z. Same result as below.
    if len(value) == 24 and value[23] == 'Z' and value[10] == 'T' and value[19] == '.' \
            and value[13] == ':' and value[16] == ':':
        day = _DAYS.get(value[:10])
        if day is None:
            day = _DAYS[value[:10]] = _day_start(value[:10])
        h, mi, s, ms = value[11:13], value[14:16], value[17:19], value[20:23]
        if day is not False and h.isdigit() and mi.isdigit() and s.isdigit() and ms.isdigit() \
                and int(h) < 24 and int(mi) < 60 and int(s) < 60:
            return day + int(h) * 3600 + int(mi) * 60 + int(s) + int(ms) / 1000
    m = _TS.match(value.strip())
    if not m:
        return None
    y, mo, d, h, mi, s, frac, zone = m.groups()
    try:
        t = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s), tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None
    if frac:
        t += int(frac) / 10 ** len(frac)
    if zone and zone not in ('Z', 'z'):
        sign = -1 if zone[0] == '-' else 1
        digits = zone[1:].replace(':', '')
        t -= sign * (int(digits[:2]) * 3600 + int(digits[2:4] or 0) * 60)
    return t


def _day_start(text):
    """Epoch seconds of UTC midnight for 'YYYY-MM-DD', or False."""
    if len(text) != 10 or text[4] != '-' or text[7] != '-' or not (text[:4] + text[5:7] + text[8:]).isdigit():
        return False
    try:
        return datetime(int(text[:4]), int(text[5:7]), int(text[8:]), tzinfo=timezone.utc).timestamp()
    except ValueError:
        return False


def kind_of(phase=None, label=None, agent_type=None, tools=()):
    """(kind, decided_by) by KIND_RULES. tools is any iterable of tool names."""
    texts = [('phase', phase), ('label', label), ('type', agent_type)]
    for source, text in texts:
        if not isinstance(text, str) or not text.strip():
            continue
        words = set(_WORD.findall(text.lower()))
        for looks_at, found, kind in KIND_RULES:
            if looks_at == 'text' and words.intersection(found):
                return kind, source
    names = set(tools)
    for looks_at, found, kind in KIND_RULES:
        if looks_at == 'tools any' and names.intersection(found):
            return kind, 'tools'
        if looks_at == 'tools only' and names and names.issubset(found):
            return kind, 'tools'
    labelled = any(isinstance(t, str) and t.strip() for t in (phase, label))
    for looks_at, _, kind in KIND_RULES:
        if (looks_at == 'labelled' and labelled) or looks_at == 'always':
            return kind, 'none'


def _h(text):
    return hashlib.blake2b(text.encode('utf-8', 'replace'), digest_size=8).hexdigest()


def _n(value):
    """A token count: a non-negative int, else 0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value < 0:
        return 0
    return int(value)


# The lines that matter hold one of these. Claude Code writes compact JSON; a file whose first
# line is spaced ("type": "x") is searched with spaced marks instead.
AGENT_MARKS = (b'"type":"assistant"',)
CHAT_MARKS = (b'"type":"assistant"', b'"type":"user"', b'-title"')
CHUNK = 1 << 24


def _spaced(marks):
    return tuple(m.replace(b'":"', b'": "') for m in marks)


def _spans(buf, end, marks):
    """(start, end, mark) for every line in buf[:end] that holds any of marks, in file order.
    mark is the index of the first mark in `marks` that the line holds."""
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
    return [(s, e, n) for s, (e, n) in sorted(found.items())]


def _marked_lines(f, marks):
    """Yield (buf, start, end, mark, complete) for each line of the binary file f that holds
    any of marks; the line is buf[start:end].

    Searching whole chunks with bytes.find is many times faster than looking at every line;
    most of a transcript's bytes are tool output and request copies that are never parsed.
    complete is False only for a last line with no newline (it may still be being written).
    """
    carry = b''
    first = True
    while True:
        chunk = f.read(CHUNK)
        if not chunk:
            break
        buf = carry + chunk if carry else chunk
        cut = buf.rfind(b'\n') + 1
        if cut == 0:
            carry = buf
            continue
        if first:
            head = buf[:buf.find(b'\n')]
            if b'"type":"' not in head and b'"type": "' in head:
                marks = _spaced(marks)
            first = False
        carry = buf[cut:]
        for s, e, n in _spans(buf, cut, marks):
            yield buf, s, e, n, True
    if carry:
        if first and b'"type":"' not in carry and b'"type": "' in carry:
            marks = _spaced(marks)
        for s, e, n in _spans(carry, len(carry), marks):
            yield carry, s, e, n, False


def _loads(raw):
    try:
        d = json.loads(raw)
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


# ---------------------------------------------------------------- reading one file

def read_transcript(path, chat=False):
    """Per-file numbers for one transcript. Raises OSError if the file vanishes.

    Returns {'reqs': [[key, ts, model, input, output, cache_write, cache_read, turn, side,
    [[tool_id, tool_name], ...], cache_write_1h], ...], 'custom', 'ai', 'folder', 'bad', 'torn', 'nokey'}.
    Keys and tool ids are short hashes. Nothing here is message text.
    """
    reqs = {}
    custom = ai = folder = None
    bad = nokey = 0
    torn = False
    turn = 0
    with open(path, 'rb') as f:
        for buf, s, e, mark, complete in _marked_lines(f, CHAT_MARKS if chat else AGENT_MARKS):
            if mark == 1 and buf.find(b'"tool_result"', s, e) != -1:
                continue        # a tool's output coming back to the chat: never a new turn, never parsed
            raw = buf[s:e]
            d = _loads(raw)
            if d is None:
                if not complete:
                    torn = True     # the last line, still being written
                elif raw.strip():
                    bad += 1
                continue
            kind = d.get('type')
            if kind == 'assistant':
                m = d.get('message')
                if not isinstance(m, dict) or m.get('model') == '<synthetic>':
                    continue
                if folder is None and isinstance(d.get('cwd'), str) and d['cwd'].strip('/'):
                    folder = os.path.basename(d['cwd'].rstrip('/'))
                mid, rid = m.get('id'), d.get('requestId')
                if not mid and not rid:
                    nokey += 1
                    continue
                key = (mid or '', rid or '')
                ts = parse_ts(d.get('timestamp'))
                u = m.get('usage') if isinstance(m.get('usage'), dict) else {}
                w1h = _n((u.get('cache_creation') if isinstance(u.get('cache_creation'), dict) else {})
                         .get('ephemeral_1h_input_tokens'))
                toks = (_n(u.get('input_tokens')), _n(u.get('output_tokens')),
                        max(_n(u.get('cache_creation_input_tokens')), w1h),     # a write is at least its 1-hour part
                        _n(u.get('cache_read_input_tokens')), w1h)
                tools = {}
                content = m.get('content')
                for block in content if isinstance(content, list) else ():
                    if isinstance(block, dict) and block.get('type') == 'tool_use':
                        name = block.get('name') if isinstance(block.get('name'), str) else '?'
                        # an id-less call (never seen) counts once per tool name in its request
                        tid = block.get('id') if isinstance(block.get('id'), str) else '%s|%s:%s' % (key + (name,))
                        tools[tid] = name
                model = m.get('model') if isinstance(m.get('model'), str) else ''
                row = reqs.get(key)
                if row is None:
                    reqs[key] = [ts, model, toks[0], toks[1], toks[2], toks[3], turn,
                                 1 if d.get('isSidechain') and chat else 0, tools, toks[4]]
                else:
                    if ts is not None and (row[0] is None or ts < row[0]):
                        row[0] = ts
                    if not row[1]:
                        row[1] = model
                    for i in range(4):
                        if toks[i] > row[2 + i]:
                            row[2 + i] = toks[i]
                    if toks[4] > row[9]:
                        row[9] = toks[4]
                    row[8].update(tools)
            elif not chat:
                continue
            elif kind == 'custom-title':
                if isinstance(d.get('customTitle'), str) and d['customTitle'].strip():
                    custom = d['customTitle'].strip()     # the last one wins: a rename
            elif kind == 'ai-title':
                if isinstance(d.get('aiTitle'), str) and d['aiTitle'].strip():
                    ai = d['aiTitle'].strip()
            elif kind == 'user':
                # A new message into the chat (yours, or a notice) starts a new turn. Tool
                # results, injected context and compaction summaries do not.
                content = (d.get('message') or {}).get('content') if isinstance(d.get('message'), dict) else None
                is_result = isinstance(content, list) and any(
                    isinstance(b, dict) and b.get('type') == 'tool_result' for b in content)
                if not is_result and not d.get('isMeta') and not d.get('isCompactSummary') \
                        and not d.get('isSidechain'):
                    turn += 1
    rows = [[_h('%s|%s' % k), r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7],
             sorted([_h(t), n] for t, n in r[8].items()), r[9]] for k, r in reqs.items()]
    return {'reqs': rows, 'custom': custom, 'ai': ai, 'folder': folder, 'bad': bad, 'torn': torn,
            'nokey': nokey}


def _read_json(path):
    try:
        with open(path, 'rb') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------- finding the files

def _scan(root):
    """Every transcript under root, as dicts with path, kind, project, session, wf, size, mtime_ns."""
    out = []
    try:
        projects = sorted(e.path for e in os.scandir(root) if e.is_dir(follow_symlinks=False))
    except OSError:
        return out
    for project in projects:
        try:
            entries = sorted(os.scandir(project), key=lambda e: e.name)
        except OSError:
            continue
        for e in entries:
            try:
                if e.name.endswith('.jsonl') and e.is_file(follow_symlinks=False):
                    out.append({'path': e.path, 'kind': 'chat', 'project': project,
                                'session': e.name[:-len('.jsonl')], 'wf': None})
                elif e.is_dir(follow_symlinks=False):
                    sub = os.path.join(e.path, 'subagents')
                    for dirpath, dirnames, filenames in os.walk(sub):
                        dirnames.sort()
                        rel = os.path.relpath(dirpath, sub).split(os.sep)
                        wf = rel[rel.index('workflows') + 1] if 'workflows' in rel[:-1] else None
                        for fn in sorted(filenames):
                            if fn.startswith('agent-') and fn.endswith('.jsonl'):
                                out.append({'path': os.path.join(dirpath, fn),
                                            'kind': 'workflows' if wf else 'helpers', 'project': project,
                                            'session': e.name, 'wf': wf})
            except OSError:
                continue
    kept = []
    for f in out:
        try:
            st = os.stat(f['path'])
        except OSError:
            continue
        f['size'], f['mtime_ns'] = st.st_size, st.st_mtime_ns
        kept.append(f)
    return kept


# ---------------------------------------------------------------- the cache

def _load_cache(path):
    if not path:
        return {'version': CACHE_VERSION, 'files': {}}
    d = _read_json(path)
    if d.get('version') != CACHE_VERSION or not isinstance(d.get('files'), dict):
        return {'version': CACHE_VERSION, 'files': {}}
    return d


def _save_cache(path, cache):
    """Write the cache atomically (temp file, then rename). A cache that cannot be written is skipped."""
    tmp = '%s.%d.tmp' % (path, os.getpid())
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), mode=0o700, exist_ok=True)
        text = json.dumps(cache, separators=(',', ':'))    # dumps uses the C encoder; dump does not
        with open(tmp, 'w') as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _sound(data):
    """True when a cached transcript entry has the shape read_transcript gives."""
    if not isinstance(data, dict) or not isinstance(data.get('reqs'), list):
        return False
    if not all(data.get(k) is None or isinstance(data.get(k), str) for k in ('custom', 'ai', 'folder')) \
            or not all(isinstance(data.get(k), int) for k in ('bad', 'nokey')):
        return False
    for r in data['reqs']:
        if not (isinstance(r, list) and len(r) == 11 and isinstance(r[0], str) and (r[1] is None or _num(r[1]))
                and isinstance(r[2], str) and all(isinstance(x, int) for x in r[3:9]) and isinstance(r[10], int)
                and isinstance(r[9], list) and all(isinstance(t, list) and len(t) == 2 for t in r[9])):
            return False
    return True


def _cache_hit(cache, path, size, mtime_ns, check=_sound):
    hit = cache['files'].get(path)
    return isinstance(hit, dict) and hit.get('size') == size and hit.get('mtime_ns') == mtime_ns \
        and check(hit.get('data'))


def _cached(cache, path, size, mtime_ns, reader, stats, check=_sound):
    """Per-file data from the cache when (size, mtime) still match, else read it again."""
    if _cache_hit(cache, path, size, mtime_ns, check):
        stats['from_cache'] += 1
        return cache['files'][path]['data']
    try:
        data = reader(path)
    except OSError:
        stats['vanished'] += 1
        cache['files'].pop(path, None)
        return None
    cache['files'][path] = {'size': size, 'mtime_ns': mtime_ns, 'data': data}
    cache['dirty'] = True
    stats['read'] += 1
    return data


def _read_workflow_record(path):
    d = _read_json(path)
    if not d and not os.path.exists(path):
        raise OSError('gone')
    name = d.get('workflowName')
    return {'name': name.strip() if isinstance(name, str) and name.strip() else None}


# ---------------------------------------------------------------- the summary

def _tok():
    return [0] * len(TOKENS)


def _add(acc, toks):
    for i in range(len(TOKENS)):
        acc[i] += toks[i]


def _tokens(acc):
    return dict(zip(TOKENS, acc))


class _Group(object):
    __slots__ = ('tok', 'requests')

    def __init__(self):
        self.tok, self.requests = _tok(), 0

    def add(self, toks, n=1):
        _add(self.tok, toks)
        self.requests += n

    @property
    def weight(self):
        return weight(self.tok)


def _share(w, total):
    return w / total if total else 0.0


def summarize(root, since_ts, until_ts, cache_path=None, tz=None, series_since=None):
    """Where the window [since_ts, until_ts) went. See the module notes for the rules.

    root is Claude Code's projects folder. cache_path, when given, is a JSON file of per-file
    numbers that makes later calls fast. tz sets the day boundaries for the per-day totals
    (a tzinfo); None means this Mac's local time.

    series_since, when given, adds 'series': every local hour and every local day from the one
    series_since falls in to the one until_ts falls in, each with the weight of the requests in
    it from series_since on, split by who did them, by project, by model and by kind. An hour
    with use also says how its tokens divide (tokens), how many requests and tool calls it held,
    and which helpers and workflow agents (agents) of which workflow runs (runs) made a request
    in it: hashed ids, so that two hours can be counted together without counting one twice. It
    may be before since_ts, so the first day can be a whole day; the rest of the result still
    counts only [since_ts, until_ts).
    """
    t0 = time.time()
    stats = {'scanned': 0, 'in_scope': 0, 'read': 0, 'from_cache': 0, 'vanished': 0,
             'bad_lines': 0, 'torn_lines': 0, 'unkeyed_lines': 0, 'untimed_requests': 0}
    cache = _load_cache(cache_path)
    files = _scan(root)
    stats['scanned'] = len(files)
    by_path = {f['path']: f for f in files}
    chat_of = {(f['project'], f['session']): f for f in files if f['kind'] == 'chat'}
    lo = since_ts if series_since is None else min(since_ts, series_since)

    # Files that can hold a request in the window (or the series): changed at or after its start
    # (less 2 seconds, for disks that keep whole-second or two-second change times). Their chats
    # come too, for the title and the chat's model.
    scope = [f for f in files if f['mtime_ns'] >= int((lo - 2) * 1e9)]
    need = {f['path'] for f in scope}
    for f in scope:
        c = chat_of.get((f['project'], f['session']))
        if c is not None:
            need.add(c['path'])
    stats['in_scope'] = len(need)

    data = {}
    for path in sorted(need):
        f = by_path[path]
        d = _cached(cache, path, f['size'], f['mtime_ns'],
                    lambda p, chat=(f['kind'] == 'chat'): read_transcript(p, chat=chat), stats)
        if d is None:
            continue
        data[path] = d
        stats['bad_lines'] += d.get('bad', 0)
        stats['torn_lines'] += 1 if d.get('torn') else 0
        stats['unkeyed_lines'] += d.get('nokey', 0)

    # Agent metadata is small and can change on its own, so it is read fresh every time.
    meta = {}
    for path in data:
        f = by_path[path]
        if f['kind'] != 'chat':
            m = _read_json(path[:-len('.jsonl')] + '.meta.json')
            depth = m.get('spawnDepth')
            meta[path] = {
                'type': m.get('agentType') if isinstance(m.get('agentType'), str) else None,
                'label': m.get('description').strip() if isinstance(m.get('description'), str)
                and m.get('description').strip() else None,
                'phase': m.get('workflowPhase').strip() if isinstance(m.get('workflowPhase'), str)
                and m.get('workflowPhase').strip() else None,
                'model': m.get('model') if isinstance(m.get('model'), str) else None,
                'has_meta': bool(m),
                'depth': depth if isinstance(depth, int) and not isinstance(depth, bool) and depth > 0 else 1,
            }

    # One record per request across all files; the copy nearest the chat owns it.
    reqs = {}
    for path in sorted(data):
        f = by_path[path]
        rank = (0 if f['kind'] == 'chat' else meta[path]['depth'], path)
        for key, ts, model, i, o, cw, cr, turn, side, tools, cw1h in data[path]['reqs']:
            r = reqs.get(key)
            if r is None:
                reqs[key] = {'ts': ts, 'model': model, 'tok': [i, o, cw, cr, cw1h], 'rank': rank, 'path': path,
                             'turn': turn, 'side': side, 'tools': dict((t, n) for t, n in tools)}
                continue
            if ts is not None and (r['ts'] is None or ts < r['ts']):
                r['ts'] = ts
            if not r['model']:
                r['model'] = model
            for j, v in enumerate((i, o, cw, cr, cw1h)):
                if v > r['tok'][j]:
                    r['tok'][j] = v
            r['tools'].update((t, n) for t, n in tools)
            if rank < r['rank']:
                r.update(rank=rank, path=path, turn=turn, side=side)

    # Kind: per agent (all its requests), per chat turn, per stray sidechain request.
    unit_tools = {}
    for key, r in reqs.items():
        f = by_path[r['path']]
        unit = (r['path'],) if f['kind'] != 'chat' else \
            ((r['path'], 'turn', r['turn']) if not r['side'] else (r['path'], 'req', key))
        r['unit'] = unit
        unit_tools.setdefault(unit, set()).update(r['tools'].values())
    unit_kind = {}
    for unit, names in unit_tools.items():
        m = meta.get(unit[0]) if len(unit) == 1 else None
        if m:
            unit_kind[unit] = kind_of(m['phase'], m['label'], m['type'], names)
        else:
            unit_kind[unit] = kind_of(tools=names)

    # The chat's own model over time, to tell what an agent with no model set inherited.
    chat_models = {}
    for key, r in reqs.items():
        f = by_path[r['path']]
        if f['kind'] == 'chat' and not r['side'] and r['ts'] is not None and r['model']:
            chat_models.setdefault((f['project'], f['session']), []).append((r['ts'], family(r['model'])))
    chat_times = {}
    for ck, v in chat_models.items():
        v.sort()
        chat_times[ck] = [t for t, _ in v]
    agent_start = {}
    for r in reqs.values():
        if by_path[r['path']]['kind'] != 'chat' and r['ts'] is not None:
            p = r['path']
            agent_start[p] = r['ts'] if p not in agent_start else min(agent_start[p], r['ts'])

    def parent_family(f, at):
        """The chat's model family at its last request at or before `at` (else its first)."""
        ck = (f['project'], f['session'])
        line = chat_models.get(ck)
        if not line:
            return None
        i = bisect.bisect_right(chat_times[ck], at)
        return line[i - 1][1] if i else line[0][1]

    agent_parent = {p: parent_family(by_path[p], t) for p, t in agent_start.items()}

    # Add up the window: first into cells of requests that share every dimension (a few
    # thousand at most), then roll the cells up into each cut.
    day_of = {}

    def day_for(ts):
        # Local dates change only on 15-minute boundaries in every real time zone, so one
        # lookup per 15 minutes is enough; a bucket whose two ends differ is done per request.
        b = int(ts // 900)
        d = day_of.get(b)
        if d is None:
            ends = [(datetime.fromtimestamp(x, tz) if tz is not None else datetime.fromtimestamp(x))
                    .strftime('%Y-%m-%d') for x in (b * 900, b * 900 + 899)]
            d = day_of[b] = ends[0] if ends[0] == ends[1] else ''
        if d:
            return d
        return (datetime.fromtimestamp(ts, tz) if tz is not None else datetime.fromtimestamp(ts)).strftime('%Y-%m-%d')

    hour_of = {}

    def hour_for(ts):
        # The same for hours: every real time zone's offset is a whole number of quarter hours.
        b = int(ts // 900)
        h = hour_of.get(b)
        if h is None:
            ends = [hour_start(x, tz) for x in (b * 900, b * 900 + 899.999)]
            h = hour_of[b] = ends[0] if ends[0] == ends[1] else False
        return h if h is not False else hour_start(ts, tz)

    cells = {}
    low = {}                    # model -> True when its cache reads count as cache_read_low
    for key, r in reqs.items():
        ts = r['ts']
        if ts is None:
            stats['untimed_requests'] += 1
            continue
        if not (lo <= ts < until_ts):
            continue
        cut = ts >= since_ts                                        # in the window
        hour = hour_for(ts) if series_since is not None and ts >= series_since else None    # in the series
        k, by = unit_kind[r['unit']]
        ck = (r['path'], r['side'], r['model'], k, by, day_for(ts), hour, cut)
        c = cells.get(ck)
        if c is None:
            c = cells[ck] = [_tok(), 0, 0]
        t = r['tok']
        acc = c[0]
        acc[0] += t[0]
        acc[1] += t[1]
        acc[2] += t[2]
        acc[3] += t[3]
        acc[4] += t[4]
        m_low = low.get(r['model'])
        if m_low is None:
            m_low = low[r['model']] = cache_read_weight(r['model']) != WEIGHTS['cache_read']
        if m_low:
            acc[5] += t[3]
        c[1] += 1
        c[2] += len(r['tools'])

    total = _Group()
    who = dict((w, _Group()) for w in WHO)
    who_tools = dict((w, 0) for w in WHO)
    fam = dict((x, _Group()) for x in FAMILIES)
    fam_models = dict((x, {}) for x in FAMILIES)
    kinds = dict((k, _Group()) for k in KINDS)
    kind_by = dict((k, {}) for k in KINDS)
    days = {}
    chats = {}
    wfs = {}
    agents = {'helpers': set(), 'workflows': set()}
    inh = _Group()
    inh_fam = {}
    inh_labels = {}
    inh_agents = set()
    unknown_parent = _Group()
    helper_cells = {}       # one group per kind of helper, its model and its chat's, for models.py

    def group(d, key):
        g = d.get(key)
        if g is None:
            g = d[key] = _Group()
        return g

    s_hours = {}            # the series: local hour start -> the same four parts, and what ran in it
    s_days = {}             # the series: local day -> {'who', 'chat', 'fam', 'kind': {its key: tokens}}
    s_agents = {}           # the series: chat -> its agents (for a title, the way the window's chats find one)

    for (path, side, model, k, by, day, hour, cut), (toks, n, ntools) in cells.items():
        f = by_path[path]
        w_kind = f['kind'] if f['kind'] != 'chat' else ('helpers' if side else 'chat')
        if hour is not None:
            sh = s_hours.get(hour)
            if sh is None:
                sh = s_hours[hour] = {'who': {}, 'chat': {}, 'fam': {}, 'kind': {}, 'agents': set(), 'runs': set(),
                                      'requests': 0, 'tools': 0}
            sd = s_days.get(day)
            if sd is None:
                sd = s_days[day] = {'who': {}, 'chat': {}, 'fam': {}, 'kind': {}}
            ck = (f['project'], f['session'])
            for part, at in (('who', w_kind), ('chat', ck), ('fam', family(model)), ('kind', k)):
                _add(sh[part].setdefault(at, _tok()), toks)
                _add(sd[part].setdefault(at, _tok()), toks)
            sh['requests'] += n
            sh['tools'] += ntools
            if f['kind'] != 'chat':
                s_agents.setdefault(ck, set()).add(path)
                sh['agents'].add(path)
                if f['kind'] == 'workflows':
                    sh['runs'].add('\n'.join((f['project'], f['session'], f['wf'])))
        if not cut:
            continue                    # before the window: in the series only
        wt = weight(toks)
        total.add(toks, n)
        who[w_kind].add(toks, n)
        who_tools[w_kind] += ntools
        fm = family(model)
        fam[fm].add(toks, n)
        fam_models[fm][model or '?'] = fam_models[fm].get(model or '?', 0) + n
        kinds[k].add(toks, n)
        kind_by[k][by] = kind_by[k].get(by, 0.0) + wt
        group(days, day).add(toks, n)

        ck = (f['project'], f['session'])
        c = chats.get(ck)
        if c is None:
            c = chats[ck] = {'all': _Group(), 'who': dict((x, _Group()) for x in WHO), 'fam': {},
                             'agents': set(), 'wfs': set()}
        c['all'].add(toks, n)
        c['who'][w_kind].add(toks, n)
        group(c['fam'], fm).add(toks, n)
        if f['kind'] == 'chat':
            continue
        agents[f['kind']].add(path)
        c['agents'].add(path)
        m = meta[path]
        if f['kind'] == 'workflows':
            wk = (f['project'], f['session'], f['wf'])
            c['wfs'].add(wk)
            x = wfs.get(wk)
            if x is None:
                x = wfs[wk] = {'all': _Group(), 'agents': set(), 'labels': {}, 'phases': {}, 'fam': {}}
            x['all'].add(toks, n)
            x['agents'].add(path)
            if m['label']:
                x['labels'][m['label']] = x['labels'].get(m['label'], 0.0) + wt
            if m['phase']:
                x['phases'][m['phase']] = x['phases'].get(m['phase'], 0.0) + wt
            group(x['fam'], fm).add(toks, n)

        parent = agent_parent.get(path)
        hk = (f['kind'], m['type'] if m['has_meta'] else None, m['model'] or None, fm, parent, k,
              m['label'] or ('(%s)' % m['type'] if m['type'] else '(no label)'))
        group(helper_cells, hk).add(toks, n)
        if not m['has_meta'] or (m['model'] in UNSET_MODELS and parent is None):
            unknown_parent.add(toks, n)      # cannot tell: no meta file, or no chat model to compare
        elif m['model'] in UNSET_MODELS and parent == fm:
            inh.add(toks, n)
            group(inh_fam, fm).add(toks, n)
            label = m['label'] or ('(%s)' % m['type'] if m['type'] else '(no label)')
            e = inh_labels.get(label)
            if e is None:
                e = inh_labels[label] = [_Group(), set()]
            e[0].add(toks, n)
            e[1].add(path)
            inh_agents.add(path)

    tw = total.weight

    def row(label, g, **detail):
        d = {'weight': g.weight, 'requests': g.requests, 'tokens': _tokens(g.tok)}
        d.update(detail)
        return {'label': label, 'share': _share(g.weight, tw), 'detail': d}

    def fam_list(groups, of):
        return [{'family': x, 'share': _share(groups[x].weight, of)}
                for x in sorted(groups, key=lambda x: (-groups[x].weight, FAMILIES.index(x)))]

    def title_of(ck, agent_paths):
        cf = chat_of.get(ck)
        d = data.get(cf['path']) if cf else None
        title = d and (d.get('custom') or d.get('ai') or d.get('folder'))
        if not title:
            for p in sorted(agent_paths):
                if data.get(p, {}).get('folder'):
                    title = data[p]['folder']
                    break
        return title or 'chat %s' % ck[1][:8]

    titles = dict((ck, title_of(ck, c['agents'])) for ck, c in chats.items())
    for sd in s_days.values():
        for ck in sd['chat']:
            if ck not in titles:
                titles[ck] = title_of(ck, s_agents.get(ck, ()))

    work = []
    for ck, c in sorted(chats.items(), key=lambda kv: (-kv[1]['all'].weight, kv[0])):
        cw = c['all'].weight
        work.append(row(titles[ck], c['all'], session=ck[1],
                        own=_share(c['who']['chat'].weight, cw),
                        helpers=_share(c['who']['helpers'].weight, cw),
                        workflows=_share(c['who']['workflows'].weight, cw),
                        models=fam_list(c['fam'], cw),
                        agents=len(c['agents']), workflow_runs=len(c['wfs'])))

    who_rows = [
        row(WHO_LABEL['chat'], who['chat'], chats=sum(1 for c in chats.values() if c['who']['chat'].requests),
            tool_calls=who_tools['chat']),
        row(WHO_LABEL['helpers'], who['helpers'], agents=len(agents['helpers']), tool_calls=who_tools['helpers']),
        row(WHO_LABEL['workflows'], who['workflows'], workflows=len(wfs), agents=len(agents['workflows']),
            tool_calls=who_tools['workflows']),
    ]
    model_rows = [row(x, fam[x], models=dict(sorted(fam_models[x].items()))) for x in FAMILIES]
    kind_rows = [row(k, kinds[k], decided_by=dict(sorted(kind_by[k].items()))) for k in KINDS]

    top = sorted(inh_labels.items(), key=lambda kv: (-kv[1][0].weight, kv[0]))[:5]
    finding = {
        'share': _share(inh.weight, tw), 'weight': inh.weight, 'requests': inh.requests,
        'tokens': _tokens(inh.tok), 'agents': len(inh_agents),
        'by_family': [{'family': x, 'share': _share(inh_fam[x].weight, tw)}
                      for x in sorted(inh_fam, key=lambda x: FAMILIES.index(x))],
        'top_labels': [{'label': lab, 'share': _share(g.weight, tw), 'agents': len(ps)} for lab, (g, ps) in top],
        'unknown_share': _share(unknown_parent.weight, tw),     # agents we cannot judge (no meta, no chat model)
        'rule': ('Helpers and workflow agents whose meta has no model (or "inherit") and whose requests ran '
                 "on the same model family as their chat when they started."),
    }

    wf_rows = []
    for wk, x in sorted(wfs.items(), key=lambda kv: (-kv[1]['all'].weight, kv[0]))[:10]:
        rec = None
        rp = os.path.join(wk[0], wk[1], 'workflows', wk[2] + '.json')
        try:
            st = os.stat(rp)
            rec = _cached(cache, rp, st.st_size, st.st_mtime_ns, _read_workflow_record,
                          {'from_cache': 0, 'read': 0, 'vanished': 0},
                          check=lambda d: isinstance(d, dict) and (d.get('name') is None or isinstance(d['name'], str)))
        except OSError:
            pass
        labels = sorted(x['labels'], key=lambda k: (-x['labels'][k], k))
        phases = sorted(x['phases'], key=lambda k: (-x['phases'][k], k))
        wf_rows.append({'id': wk[2], 'name': (rec or {}).get('name'), 'chat': titles.get((wk[0], wk[1])),
                        'share': _share(x['all'].weight, tw), 'weight': x['all'].weight,
                        'requests': x['all'].requests, 'agents': len(x['agents']), 'labels': labels[:4],
                        'phases': phases[:6], 'models': fam_list(x['fam'], x['all'].weight)})

    day_rows = [{'day': k, 'share': _share(g.weight, tw), 'weight': g.weight, 'requests': g.requests,
                 'tokens': _tokens(g.tok)} for k, g in sorted(days.items())]

    series = None
    if series_since is not None:
        series = {'since': series_since, 'until': until_ts, 'hours': [], 'days': []}

        def split(parts, names):
            return dict((x, weight(parts[x]) if x in parts else 0.0) for x in names)

        def whole(parts):
            acc = _tok()
            for t_ in parts.values():
                _add(acc, t_)
            return weight(acc)

        def by_project(chats_):
            projects = {}               # chats with one title are one project, as the page names them
            for ck, t_ in chats_.items():
                _add(projects.setdefault(titles[ck], _tok()), t_)
            return dict((lab, weight(t_)) for lab, t_ in sorted(projects.items()))

        for h in _hours(series_since, until_ts, tz, s_hours):
            sh = s_hours.get(h)
            if sh is None:              # an hour with no request
                series['hours'].append({'t': h, 'weight': 0.0, 'who': split({}, WHO)})
                continue
            acc = _tok()
            for t_ in sh['who'].values():
                _add(acc, t_)
            series['hours'].append({
                't': h, 'weight': weight(acc), 'who': split(sh['who'], WHO), 'projects': by_project(sh['chat']),
                'models': split(sh['fam'], FAMILIES), 'kinds': split(sh['kind'], KINDS), 'tokens': _tokens(acc),
                'requests': sh['requests'], 'tools': sh['tools'],
                'agents': sorted(_h(p) for p in sh['agents']), 'runs': sorted(_h(r) for r in sh['runs'])})
        for day in _days(series_since, until_ts, tz, s_days):
            sd = s_days.get(day) or {'who': {}, 'chat': {}, 'fam': {}, 'kind': {}}
            series['days'].append({'day': day, 'weight': whole(sd['who']), 'who': split(sd['who'], WHO),
                                   'projects': by_project(sd['chat']),
                                   'models': split(sd['fam'], FAMILIES), 'kinds': split(sd['kind'], KINDS)})

    if cache_path:
        # Forget files that are gone, so the cache does not grow forever.
        under = os.path.join(root, '')
        stale = [p for p in cache['files'] if p.startswith(under)
                 and (p not in by_path if p.endswith('.jsonl') else not os.path.exists(p))]
        for p in stale:
            del cache['files'][p]
        if cache.pop('dirty', False) or stale:
            _save_cache(cache_path, cache)

    stats['seconds'] = round(time.time() - t0, 3)
    out = {
        'window': {'since': since_ts, 'until': until_ts},
        'weighting': {'formula': WEIGHT_FORMULA, 'factors': dict(WEIGHTS, cache_write_1h=WRITE_1H), 'note': WEIGHT_NOTE,
                      'cache_read_by_model': dict(CACHE_READ_BY_MODEL)},
        'totals': {'requests': total.requests, 'weight': tw, 'tokens': _tokens(total.tok),
                   'chats': len(chats), 'helper_agents': len(agents['helpers']), 'workflows': len(wfs),
                   'workflow_agents': len(agents['workflows']), 'tool_calls': sum(who_tools.values())},
        'work': work,
        'who': who_rows,
        'model': model_rows,
        'kind': kind_rows,
        'inherited': finding,
        'days': day_rows,
        'workflows': wf_rows,
        'tool_calls': dict(who_tools),
        # Every helper and workflow agent request, grouped by who started it, its agent type, the model its
        # meta names (None when none, 'inherit' kept), the family it ran on, the chat's family when it
        # started, its kind and label. models.would_have_moved() reads this to be exact.
        'helper_rows': [{'who': w_, 'type': t_, 'set_model': sm_, 'family': fm_, 'chat_family': cf_, 'kind': k_,
                         'label': lab_, 'share': _share(g.weight, tw), 'requests': g.requests}
                        for (w_, t_, sm_, fm_, cf_, k_, lab_), g in sorted(helper_cells.items(),
                                                                          key=lambda kv: (-kv[1].weight, kv[0][6]))],
        'files': stats,
    }
    if series is not None:
        out['series'] = series
    return out


def hour_start(t, tz=None):
    """Epoch seconds of the start of the local hour that `t` is in (tz a tzinfo; None: this Mac's local time, as it is
    now). Worked out with that moment's own offset, so in an hour that comes twice (the clocks going back) each is
    its own hour."""
    d = datetime.fromtimestamp(t, tz) if tz is not None else datetime.fromtimestamp(t, timezone.utc).astimezone()
    return d.replace(minute=0, second=0, microsecond=0).timestamp()


def local_day(t, tz=None):
    """The local date (a datetime.date) that `t` is in, as the per-day totals count it."""
    return (datetime.fromtimestamp(t, tz) if tz is not None else datetime.fromtimestamp(t)).date()


def _hours(since, until, tz, seen):
    """Every local hour start from the hour `since` is in up to `until`, and any hour in `seen`, in order."""
    out = set(seen)
    h = hour_start(since, tz)
    while h < until:
        out.add(h)
        nxt = hour_start(h + 3600, tz)
        h = nxt if nxt > h else h + 3600
    return sorted(out)


def _days(since, until, tz, seen):
    """Every local day ('YYYY-MM-DD') from the day `since` is in to the day before `until`, and any in `seen`."""
    out = set(seen)
    if since < until:
        d, last = local_day(since, tz), local_day(until - 0.001, tz)
        while d <= last:
            out.add(d.strftime('%Y-%m-%d'))
            d += timedelta(days=1)
    return sorted(out)


# ---------------------------------------------------------------- what the page shows

PAGE_ROWS = 8                       # rows per cut; past that, the last row is "N more"
PAGE_WHO = {'chat': 'You, in chat', 'helpers': 'Helpers', 'workflows': 'Workflows'}
PAGE_WHO_KEYS = ('workflows', 'helpers', 'chat')     # a project's split by who: the chart's order, baseline up
PAGE_KIND = {'build': 'Build', 'fix': 'Fix', 'research': 'Research', 'review': 'Review', 'docs': 'Docs',
             'tests': 'Tests', 'other': 'Other', 'not labelled': 'Not labelled'}


def whole_percents(weights):
    """Whole percents of the weights that add to exactly 100 (largest remainder first, ties to the
    earlier one). All zeros when nothing weighs anything. A heavier weight never gets less."""
    total = float(sum(weights))
    if total <= 0:
        return [0] * len(weights)
    raw = [w * 100.0 / total for w in weights]
    out = [int(x) for x in raw]
    for i in sorted(range(len(raw)), key=lambda i: (out[i] - raw[i], i))[:100 - sum(out)]:
        out[i] += 1
    return out


def _count(n, one, many=None):
    return '%s %s' % ('{:,}'.format(n), one if n == 1 else (many or one + 's'))


def _mix(parts):
    """'86% workflows, 11% helpers, 3% chat.' from [(name, fraction)]; 'All chat.' when one part is everything."""
    named = [(name, p) for name, p in zip([n for n, _ in parts], whole_percents([f for _, f in parts])) if p]
    named.sort(key=lambda x: -x[1])
    if not named:
        return ''
    if len(named) == 1:
        return 'All %s.' % named[0][0]
    return ', '.join('%d%% %s' % (p, name) for name, p in named) + '.'


def _models(fams):
    """'Mostly Opus.', 'All Opus.' or 'Opus and Sonnet.' from [{family, share}] (biggest first)."""
    fams = [f for f in fams if f['share'] > 0]
    if not fams:
        return ''
    if fams[0]['share'] >= 0.995:
        return 'All %s.' % fams[0]['family']
    if fams[0]['share'] >= 0.6 or len(fams) == 1:
        return 'Mostly %s.' % fams[0]['family']
    return '%s and %s.' % (fams[0]['family'], fams[1]['family'])


def _rows(items, more, who=None):
    """Rows of a cut: [(label, weight, note(percent) -> str)], heaviest first, up to PAGE_ROWS with the
    rest folded into one 'N more' row (note from more(n)). Whole percents that add to 100; rows that
    come to 0% are left out. With `who` (each label's {workflows, helpers, chat} weights), each row
    also gets its own split by who, as whole percents that add to 100 ('N more': the rest's together)."""
    items = sorted((x for x in items if x[1] > 0), key=lambda x: -x[1])
    parts = [dict(who.get(x[0]) or {}) for x in items] if who is not None else None
    if len(items) > PAGE_ROWS:
        rest = items[PAGE_ROWS - 1:]
        n = len(rest)
        if parts is not None:
            parts = parts[:PAGE_ROWS - 1] + [dict((k, sum(p.get(k, 0.0) for p in parts[PAGE_ROWS - 1:])) for k in PAGE_WHO_KEYS)]
        items = items[:PAGE_ROWS - 1] + [('%d more' % n, sum(x[1] for x in rest), lambda p, n=n: more(n))]
    shares = whole_percents([x[1] for x in items])
    out = []
    for i, ((label, _, note), p) in enumerate(zip(items, shares)):
        if p > 0:
            row = {'label': label, 'share': p, 'note': note(p)}
            if parts is not None:
                row['who'] = dict(zip(PAGE_WHO_KEYS, whole_percents([parts[i].get(k, 0.0) for k in PAGE_WHO_KEYS])))
            out.append(row)
    return out


def for_page(s, days):
    """The summary in the shape History's Work draws: four cuts (work, who, model, kind), each a list of
    {label, share (whole percent; each cut adds to 100), note (one short sentence made only from
    numbers and fixed words)}, and `moved`, the share "Sonnet for helpers" would have moved
    ({share, to}, or None): the same number the Models page shows."""
    if not s['totals']['weight']:
        return {'days': days, 'cuts': {}, 'moved': None}
    work, work_who = [], {}
    for r in s['work']:
        d = r['detail']
        mix = _mix([('workflows', d['workflows']), ('helpers', d['helpers']), ('chat', d['own'])])
        work.append((r['label'], d['weight'], lambda p, mix=mix, m=_models(d['models']): (mix + ' ' + m).strip()))
        work_who[r['label']] = {'workflows': d['weight'] * d['workflows'], 'helpers': d['weight'] * d['helpers'],
                                'chat': d['weight'] * d['own']}
    who = dict((r['label'], r) for r in s['who'])
    biggest = s['workflows'][0]['weight'] if s['workflows'] else 0
    total = s['totals']['weight']
    wf = who[WHO_LABEL['workflows']]['detail']
    wf_note = '%s, %s.' % (_count(wf.get('workflows', 0), 'workflow'), _count(wf.get('agents', 0), 'agent'))
    if biggest and wf.get('workflows', 0) > 1:
        wf_note += ' The largest took %d%%.' % round(100.0 * biggest / total)
    who_items = [
        (PAGE_WHO['workflows'], wf['weight'], lambda p: wf_note),
        (PAGE_WHO['helpers'], who[WHO_LABEL['helpers']]['detail']['weight'],
         lambda p, n=who[WHO_LABEL['helpers']]['detail'].get('agents', 0): '%s started from chats.' % _count(n, 'helper')),
        (PAGE_WHO['chat'], who[WHO_LABEL['chat']]['detail']['weight'],
         lambda p, n=who[WHO_LABEL['chat']]['detail'].get('chats', 0): 'Your own turns in %s.' % _count(n, 'chat')),
    ]
    model_items = [(r['label'], r['detail']['weight'], lambda p, n=r['detail']['requests']: '%s.' % _count(n, 'request'))
                   for r in s['model']]
    kind_items = [(PAGE_KIND.get(r['label'], r['label']), r['detail']['weight'],
                   lambda p, n=r['detail']['requests']: '%s.' % _count(n, 'request')) for r in s['kind']]
    cuts = {'work': _rows(work, lambda n: '%s.' % _count(n, 'smaller chat'), who=work_who),
            'who': _rows(who_items, lambda n: ''),
            'model': _rows(model_items, lambda n: ''),
            'kind': _rows(kind_items, lambda n: '')}
    share = moved_share(s)
    return {'days': days, 'cuts': cuts,
            'moved': {'share': share, 'to': 'Sonnet', 'from': moved_from(s)} if share >= 1 else None}


def moved_share(s):
    """The one number both pages show for helpers with no model set: the whole percent of the window that
    "Sonnet for helpers" would have moved (models.would_have_moved, without the Explore file)."""
    from . import models
    w = models.would_have_moved(s, lookups=False)
    return int(round(100 * (w['share'] if w['exact'] else w['at_most'])))


def moved_from(s):
    """The models the moved part ran on (the chat's: Opus, Fable), biggest first; a model with under a tenth of the
    moved part is left out. [] when the summary doesn't say."""
    from . import models
    fams = {}
    for r in s.get('helper_rows') or []:
        if models._moves(r, False)[0] == 'Sonnet':
            fams[r.get('family')] = fams.get(r.get('family'), 0.0) + (r.get('share') or 0.0)
    total = sum(fams.values())
    return [f for f in sorted(fams, key=lambda f: (-fams[f], FAMILIES.index(f) if f in FAMILIES else 99))
            if f and total and fams[f] >= 0.1 * total]


def print_page(page, out):
    """The page's numbers as text: what `switchboard usage` prints. Laid out as History shows them: the work of the
    last days under its heading, then who did it, by model and by kind."""
    w = out.write
    if not page['cuts']:
        w('Nothing in the last %g days.\n' % page['days'])
        return
    w('Work, last %g days (shares of weighted tokens)\n' % page['days'])
    for key, name in (('work', None), ('who', 'Who'), ('model', 'Model'), ('kind', 'Kind')):
        rows = page['cuts'].get(key) or []
        if not rows:
            continue
        if name:
            w('\n%s\n' % name)
        width = min(32, max(len(r['label']) for r in rows))
        for r in rows:
            label = r['label'] if len(r['label']) <= width else r['label'][:width - 1] + '…'
            w('  %3d%%  %-*s  %s\n' % (r['share'], width, label, r['note']))
    m = page.get('moved')
    if m:
        w('\n%d%% would move to %s (helpers with no model set).\n' % (m['share'], m['to']))


# ---------------------------------------------------------------- the command

def _pct(x):
    return '%5.1f%%' % (100 * x)


def _day_bounds(day):
    """Local midnight to the next local midnight for 'YYYY-MM-DD'."""
    d = datetime.strptime(day, '%Y-%m-%d')
    nxt = d + timedelta(days=1)
    return (time.mktime((d.year, d.month, d.day, 0, 0, 0, 0, 0, -1)),
            time.mktime((nxt.year, nxt.month, nxt.day, 0, 0, 0, 0, 0, -1)))


def _print(s, out):
    w = out.write
    since = time.strftime('%a %b %d %H:%M', time.localtime(s['window']['since']))
    until = time.strftime('%a %b %d %H:%M', time.localtime(s['window']['until']))
    t = s['totals']
    w('Work, %s to %s\n' % (since, until))
    w('%d requests, %d chats, %d helpers, %d workflows (%d agents), %d tool calls\n' % (
        t['requests'], t['chats'], t['helper_agents'], t['workflows'], t['workflow_agents'], t['tool_calls']))
    w('tokens: input %d, output %d, cache write %d, cache read %d; weighted %.0f\n' % (
        t['tokens']['input'], t['tokens']['output'], t['tokens']['cache_write'], t['tokens']['cache_read'],
        t['weight']))
    w('%s\n' % s['weighting']['note'])
    for name, cut in (('Who', s['who']), ('Model', s['model']), ('Kind', s['kind'])):
        w('\n%s\n' % name)
        for r in cut:
            if r['detail']['requests']:
                w('  %s  %s\n' % (_pct(r['share']), r['label']))
    w('\nWork (top 12 of %d chats)\n' % len(s['work']))
    for r in s['work'][:12]:
        d = r['detail']
        models = ', '.join('%s %d%%' % (m['family'], round(100 * m['share'])) for m in d['models'][:2])
        w('  %s  %s  (chat %d%%, helpers %d%%, workflows %d%%; %s)\n' % (
            _pct(r['share']), r['label'][:60], round(100 * d['own']), round(100 * d['helpers']),
            round(100 * d['workflows']), models))
    f = s['inherited']
    w('\n%s of the window ran on the chat\'s own model only because the helper had no model set' % _pct(f['share']).strip())
    w(' (%d agents)\n' % f['agents'])
    for x in f['by_family']:
        w('  %s  %s\n' % (_pct(x['share']), x['family']))
    for x in f['top_labels']:
        w('  %s  %s (%d agents)\n' % (_pct(x['share']), x['label'][:60], x['agents']))
    w('\nDays\n')
    for d in s['days']:
        w('  %s  %s  %d requests\n' % (d['day'], _pct(d['share']), d['requests']))
    w('\nLargest workflows\n')
    for x in s['workflows'][:5]:
        w('  %s  %s  %d agents  %s\n' % (_pct(x['share']), x['name'] or x['id'], x['agents'],
                                        '; '.join(label[:30] for label in x['labels'][:3])))
    tc = s['tool_calls']
    w('\nTool calls: chat %d, helpers %d, workflows %d\n' % (tc['chat'], tc['helpers'], tc['workflows']))
    fs = s['files']
    w('Files: %d in scope, %d read, %d from cache, %d vanished; %d bad and %d torn lines; %.2f s\n' % (
        fs['in_scope'], fs['read'], fs['from_cache'], fs['vanished'], fs['bad_lines'], fs['torn_lines'],
        fs['seconds']))


def main(argv=None, out=None):
    out = out or sys.stdout
    claude_dir = os.environ.get('CLAUDE_CONFIG_DIR') or os.path.join(os.path.expanduser('~'), '.claude')
    data_dir = os.environ.get('SWITCHBOARD_HOME') or os.path.join(os.path.expanduser('~'), '.switchboard')
    p = argparse.ArgumentParser(prog='python3 -m switchboard.usage',
                                description='Where your Claude Code time went. Prints numbers, titles and labels.')
    p.add_argument('--days', type=float, default=7, help='how many days back from now (default 7)')
    p.add_argument('--day', help='one local day, YYYY-MM-DD, instead of --days')
    p.add_argument('--json', action='store_true', help='print the whole result as JSON')
    p.add_argument('--root', default=os.path.join(claude_dir, 'projects'), help=argparse.SUPPRESS)
    p.add_argument('--cache', default=os.path.join(data_dir, 'usage-cache.json'), help=argparse.SUPPRESS)
    p.add_argument('--no-cache', action='store_true', help='read every file again and keep no cache')
    a = p.parse_args(argv)
    if a.day:
        since, until = _day_bounds(a.day)
    else:
        until = time.time()
        since = until - a.days * 86400
    s = summarize(a.root, since, until, cache_path=None if a.no_cache else a.cache)
    if a.json:
        json.dump(s, out, indent=1, sort_keys=True)
        out.write('\n')
    else:
        _print(s, out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
