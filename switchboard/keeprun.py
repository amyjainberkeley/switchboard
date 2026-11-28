"""Keep running: what stopped work in Claude Code on this Mac, what Switchboard does about each cause, and the one step
left for you, if any. The page's "Keep running" section draws what view() gives (GET /api/keep).

Everything here is read from files on this Mac and from three read-only system commands; nothing leaves the Mac and no
model is called. What it keeps and shows: times, counts, causes, chat titles, workflow names and labels, and model
names. Never what a message said, and not Claude Code's own error line either (only the cause read from it).

Where each part comes from:

  stops     Claude Code's chat records (core.projects()): every turn that ended on an error. Claude Code writes the
            error as an assistant line with model "<synthetic>" and isApiErrorMessage true; its `error` kind,
            `apiError`, `apiErrorStatus` and the limit sentence say the cause (checked in 2.1.329 to 2.1.332). One
            stop written several times (a retry pressed again, a fork that copied the chat) counts once: lines with the
            same message id, and lines of one cause in one chat with no reply between them, are one stop.
  plugin    <data>/running/events.jsonl, written by the switchboard-running plugin when it is on: what was held,
            continued or given up. Where both sources have the same stop, the plugin's record wins.
  runs      each workflow run's folder (<chat>/subagents/workflows/wf_*): journal.jsonl says which agents started and
            which returned; each agent's record says why one died and what model it ran on.
  waiting   main chats whose last record asks you something (a question, a plan, a permission) with no answer for 10
            minutes, while their program runs.
  cut off   main chats that were in the middle of a turn when their program ended (a window reload, a quit, a restart
            of the Mac) and have no record since.
  mac       `pmset -g log` (sleep spans, the lid, battery or power; run at most once every 10 minutes and kept),
            `pmset -g batt`, `sysctl vm.swapusage` and `ps` (how many chat programs run, and which chats they hold).
  history   Switchboard's History, its readings log, and the files the loop and the plugin keep under running/.
  claude    the version of `claude` a terminal finds against the newest one installed, and whether Claude Code's user
            settings hold an API key (never the key).
  panels    the Claude Code extension's own log: "did not finish starting" right after a switch is VS Code checking each
            chat's panel again, shown under the switch that caused it, never as a stop.

The chat records are read incrementally: a cache under the data folder (keeprun-cache.json) keeps each file's few
facts by (path, size, change time) and where its read ended; a file that grew is read on from there, and each pass
stops reading after PASS_SECONDS. A changed file it did not reach keeps its earlier reading; a file never read means
no totals yet (the headline says how far the reading is). The extension's logs and each agent's .meta.json are kept
the same way (keeprun-panels.json, keeprun-cache.json), inside the same time budget.

view() never raises: a part that cannot be read is left out, with one plain line in `problems`.
"""
import bisect
import json
import os
import re
import subprocess
import time
from datetime import datetime

from . import core, history, usage

VERSION = 1
READ_VERSION = 5            # the cache's own: 5 keeps titles, labels and run names cut (a run's whole name hashed)
CACHE_FILE = 'keeprun-cache.json'
MAC_FILE = 'keeprun-mac.json'
DAYS = 14
DAY = 86400
PASS_SECONDS = 20.0         # one pass reads chat records for this long at most; the rest at the next pass
QUIET = 600                 # a chat with no record for this long is not working
MID_QUIET = 1800            # a turn whose program still runs but has not moved for this long has not continued
CUT_DAYS = 3                # a chat cut off longer ago than this is history, not a step
CLUSTER = 180               # chats cut off within 3 minutes of each other ended together
PMSET_EVERY = 600           # `pmset -g log` at most once every 10 minutes
PMSET_TIMEOUT = 30
SLEEP_MIN = 120             # a sleep shorter than 2 minutes is not counted
WAKE_NEAR = 180             # a network stop this soon after a wake came from the wake
SWITCH_NEAR = 300           # a sign-in stop this soon after a switch came from the switch
PANEL_NEAR = 180            # "did not finish starting" this soon after a switch belongs to it
PANELS_SLOW_RE = re.compile(rb'did not finish starting')
EVENTS_TAIL = 4 << 20       # the plugin's events: its newest 4 MB
SWAP_GB = 8.0               # this many GB of swap in use shows the memory step (a fixed figure, not a share)
NEVER_WAIT = 3600           # a stop with no reply is counted as "never went on" only after an hour
OPEN_QUIET = 2 * DAY        # an open chat with no record for this long is quiet
FABLE_MIN = (2, 1, 321)     # the first Claude Code that offers Fable 5.2
LADDER = ('fable', 'opus', 'sonnet', 'haiku')
MODEL_NAME = {'fable': 'Fable', 'opus': 'Opus', 'sonnet': 'Sonnet', 'haiku': 'Haiku'}
TITLE_MAX = 60
TEXT_MAX = 80

# Tools that keep a turn going on their own for a long time: a chat waiting on one is still working.
LONG_TOOLS = ('Agent', 'Task', 'Workflow', 'Monitor', 'TaskOutput', 'Sleep', 'ScheduleWakeup')

# The causes, in the words the plugin writes in running/events.jsonl, and the rows the page adds from other sources.
CAUSES = ('limit_model', 'limit_5hour', 'limit_weekly', 'limit_credits', 'limit_other', 'signed_out', 'offline',
          'overloaded', 'too_long', 'flagged', 'blocked_403', 'restart', 'empty', 'error_other')
NAMES = {
    'limit_5hour': '5-hour limit on the account in use',
    'limit_weekly': 'Weekly limit on the account in use',
    'limit_credits': 'Usage credits or a spend limit',
    'limit_other': 'Usage or rate limit',
    'signed_out': 'Signed out',
    'offline': 'Offline',
    'overloaded': 'Servers overloaded',
    'too_long': 'Chat too long for the model',
    'flagged': 'Flagged by the model’s safeguards',
    'blocked_403': 'Request rejected (403)',
    'restart': 'Chats that ended mid-turn',
    'empty': 'Turn ended with no reply',
    'error_other': 'Other errors',
    'lid': 'Lid shut on battery',
    'every_out': 'Every account out',
    'auto_off': 'Automatic switching off',
    'agents': 'Agents that died in a workflow',
}


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float('inf')


def _text(v):
    return v.strip() if isinstance(v, str) and v.strip() else None


def family(model):
    """'fable', 'opus', 'sonnet' or 'haiku' for a model id that contains the word; None for any other."""
    m = model.lower() if isinstance(model, str) else ''
    for f in LADDER:
        if f in m:
            return f
    return None


def short(text, limit=TEXT_MAX):
    """One line of Claude Code's own error text, keys masked, cut at `limit` characters."""
    return history.clean(text or '', limit) if text else ''


def iso(t):
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec='seconds') if _num(t) else None


def clock(t, now=None):
    """'2:05 AM' today, 'Thu 2:05 AM' this week, 'Nov 13, 2:05 AM' before that."""
    d = datetime.fromtimestamp(t)
    now_d = datetime.fromtimestamp(now if _num(now) else time.time())
    h = d.strftime('%I:%M %p').lstrip('0')
    if d.date() == now_d.date():
        return h
    if 0 <= (now_d.date() - d.date()).days < 7:
        return '%s %s' % (d.strftime('%a'), h)
    return '%s %d, %s' % (d.strftime('%b'), d.day, h)


def hours(seconds):
    """'41 hours', '1.5 hours', '25 minutes'."""
    m = seconds / 60.0
    if m < 90:
        return '%d minute%s' % (int(m + 0.5), '' if int(m + 0.5) == 1 else 's')
    h = m / 60.0
    return ('%d hours' % int(h + 0.5)) if h >= 10 else ('%s hours' % ('%.1f' % h).rstrip('0').rstrip('.'))


def plural(n, word, many=None):
    return '%d %s' % (n, word if n == 1 else (many or word + 's'))


# ---------------------------------------------------------------- what a stop record says

LIMIT_RE = re.compile(r'hit your ([A-Za-z0-9-]+) limit', re.I)


def cause_of(d, text):
    """The cause, in the plugin's words, of one stop record `d` (a parsed line) whose first text block is `text`."""
    t = text or ''
    low = t.lower()
    err, api, status = d.get('error'), d.get('apiError'), d.get('apiErrorStatus')
    info = ((d.get('apiErrorParams') or {}).get('rate_limit_info') or {}) if isinstance(d.get('apiErrorParams'),
                                                                                      dict) else {}
    m = LIMIT_RE.search(t)
    if m or api == 'usage_limit_reached' or err == 'rate_limit':
        word = (m.group(1) if m else '').lower()
        kind = info.get('rateLimitType') if isinstance(info, dict) else None
        if family(word):
            return 'limit_model'
        if word in ('5-hour', 'session') or kind == 'five_hour':
            return 'limit_5hour'
        if word == 'weekly' or kind == 'seven_day':
            return 'limit_weekly'
        if 'credit' in low or 'spend limit' in low or 'spending limit' in low:
            return 'limit_credits'
        if kind and 'seven_day' in str(kind):
            return 'limit_model' if 'overage' in str(kind) else 'limit_weekly'
        # No known word or kind: which limit it was is not read, so it is not named (a per-minute 429 lands here).
        return 'overloaded' if status == 529 and not m else 'limit_other'
    if status == 403 or 'attention required' in low:
        return 'blocked_403'
    if err == 'authentication_failed' or 'not logged in' in low or 'please run /login' in low \
            or 'oauth' in low and 'expired' in low:
        return 'signed_out'
    if status == 529 or d.get('apiErrorType') == 'overloaded_error' or 'overloaded' in low:
        return 'overloaded'
    if api in ('connection_failed', 'computer_slept') or "can't reach" in low or 'enotfound' in low \
            or 'econnreset' in low or 'went to sleep' in low or 'connection error' in low:
        return 'offline'
    if 'prompt is too long' in low or 'too long' in low and 'prompt' in low:
        return 'too_long'
    if 'safeguards' in low or (d.get('message') or {}).get('stop_reason') == 'refusal':
        return 'flagged'
    if low.strip() in ('no response requested.', ''):
        return 'empty'
    return 'error_other'


# ---------------------------------------------------------------- reading one transcript

MARKS = (b'"type":"assistant"', b'"type":"user"', b'"type":"custom-title"', b'"type":"ai-title"')
_PROMPT_SKIP = (b'"isMeta":true', b'"isCompactSummary":true', b'"isSidechain":true')
SHUT_RE = re.compile(rb'"interruptedByShutdown":\s*true')


def _first_text(content):
    if isinstance(content, str):
        return content
    for b in content if isinstance(content, list) else ():
        if isinstance(b, dict) and b.get('type') == 'text' and isinstance(b.get('text'), str):
            return b['text']
    return ''


class _Upto(object):
    """A file read from where it stands up to byte `end`, no further (the last line still being written waits)."""

    def __init__(self, f, end):
        self.f, self.end = f, end

    def read(self, n=-1):
        left = self.end - self.f.tell()
        if left <= 0:
            return b''
        return self.f.read(left if n is None or n < 0 else min(n, left))


def _complete_end(f, size):
    """The byte just after the last newline before `size` (0 when there is none)."""
    pos = size
    while pos > 0:
        start = max(0, pos - (1 << 16))
        f.seek(start)
        block = f.read(pos - start)
        i = block.rfind(b'\n')
        if i != -1:
            return start + i + 1
        pos = start
    return 0


def _head_mark(f, upto):
    f.seek(0)
    return usage._h(f.read(min(upto, 4096)).hex()) if upto else ''


def read_file(path, chat=True, prev=None):
    """A transcript's few facts (cached by path, size and change time). Raises OSError if the file vanishes.

    {'v', 'title', 'custom', 'ai', 'stops': [[t, cause, asked, text, records, next_prompt, next_reply, id, resets,
     side]], 'last': [t, state, tool], 'first', 'reply' (the last reply's time), 'fam' (its model family), 'intr' (an
     "interrupted" line came last), 'seen' (the stop ids), 'off' (where the read ended), 'head'}

    text is always '': Claude Code's own error line is not kept (nothing shows it).

    prev: this file's earlier reading. When the file only grew (its first bytes are the same and it is not shorter
    than where that read ended), the read goes on from there instead of from byte 0.

    state: 'done' (the turn ended), 'mid' (in a turn), 'ask' (a question to you), 'plan' (a plan to approve), 'tool'
    (a tool not yet run or answered), 'busy' (a long tool, such as a helper), 'stop' (an error ended it), 'shutdown'
    (Claude Code's program shut down in the turn).
    """
    with open(path, 'rb') as f:
        size = os.fstat(f.fileno()).st_size
        end = _complete_end(f, size)
        go_on = (_sound(prev) and _num(prev.get('off')) and 0 < prev['off'] <= end
                 and prev.get('head') == _head_mark(f, min(prev['off'], 4096)))
        if go_on:
            stops = [list(r) for r in prev['stops']]
            seen_ids = set(prev.get('seen') or [])
            title_custom, title_ai = prev.get('custom'), prev.get('ai')
            first, reply, fam = prev.get('first'), prev.get('reply'), prev.get('fam')
            last = list(prev['last'])
            intr = bool(prev.get('intr'))
            start = prev['off']
        else:
            stops, seen_ids = [], set()
            title_custom = title_ai = None
            first = reply = fam = None
            last = [None, 'done', None]
            intr = False
            start = 0
        pending = [r for r in stops if r[6] is None]
        last_tool = None
        head = _head_mark(f, min(end, 4096))
        f.seek(start)
        for buf, s, e, mark, complete in usage._marked_lines(_Upto(f, end), MARKS):
            if not complete:
                continue
            if mark == 1 and buf.find(b'"tool_result"', s, e) != -1:
                # A tool's output: never parsed (it can be megabytes) and never copied; only its time and the state
                # it leaves. The record's own "timestamp" is the first unescaped one in the line.
                if buf.find(b'"isSidechain":true', s, min(e, s + 400)) != -1 and chat:
                    continue
                shut = SHUT_RE.search(buf, s, e) is not None
                j = buf.find(b'"timestamp":"', s, e)
                tts = usage.parse_ts(buf[j + 13:min(e, j + 50)].split(b'"', 1)[0].decode('ascii', 'replace')) \
                    if j != -1 else None
                last_tool = (tts, 'shutdown' if shut else 'mid')
                continue
            d = usage._loads(buf[s:e])
            if d is None:
                continue
            kind = d.get('type')
            ts = usage.parse_ts(d.get('timestamp'))
            if ts is not None and (first is None or ts < first):
                first = ts
            if kind == 'custom-title':
                title_custom = _text(d.get('customTitle')) or title_custom
                continue
            if kind == 'ai-title':
                title_ai = _text(d.get('aiTitle')) or title_ai
                continue
            side = bool(d.get('isSidechain')) and chat
            m = d.get('message') if isinstance(d.get('message'), dict) else {}
            if kind == 'assistant':
                synthetic = m.get('model') == '<synthetic>'
                # A stop only on an error: isApiErrorMessage true, or an error kind. A synthetic line with
                # isApiErrorMessage false (such as "No response requested.") is not an error.
                if synthetic and (d.get('isApiErrorMessage') is True or d.get('error') or d.get('apiError')):
                    if side:
                        continue
                    text = _first_text(m.get('content'))
                    cause = cause_of(d, text)
                    mid = m.get('id') or d.get('uuid') or ''
                    hid = usage._h(str(mid))
                    if hid in seen_ids:
                        continue
                    seen_ids.add(hid)
                    info = ((d.get('apiErrorParams') or {}).get('rate_limit_info') or {}) \
                        if isinstance(d.get('apiErrorParams'), dict) else {}
                    resets = info.get('resetsAt') if isinstance(info, dict) and _num(info.get('resetsAt')) else None
                    prev_row = stops[-1] if stops and stops[-1][6] is None else None
                    if prev_row is not None and prev_row[1] == cause and prev_row[5] is None:
                        prev_row[4] += 1                # the same stop again, no reply and no typed line between
                    else:
                        # the model of a model-limit stop is the one its limit sentence names, never a guess
                        lim = LIMIT_RE.search(text or '')
                        asked = (family(lim.group(1)) if lim else None) if cause == 'limit_model' else \
                            (family(d.get('requestedModel')) or fam)
                        row = [ts, cause, asked, '', 1, None, None, hid, resets, 0]
                        stops.append(row)
                        pending.append(row)
                    last = [ts, 'stop', None]
                    last_tool = None
                    continue
                if synthetic:
                    continue
                if side:
                    continue
                if ts is not None:
                    reply = ts if reply is None or ts > reply else reply
                fam = family(m.get('model')) or fam
                for row in pending:
                    if row[6] is None and ts is not None:
                        row[6] = ts
                pending = []
                names = [b.get('name') for b in m.get('content') or [] if isinstance(b, dict)
                         and b.get('type') == 'tool_use' and isinstance(b.get('name'), str)]
                stop_reason = m.get('stop_reason')
                if names:
                    if 'AskUserQuestion' in names:
                        state = 'ask'
                    elif 'ExitPlanMode' in names:
                        state = 'plan'
                    elif any(n in LONG_TOOLS for n in names):
                        state = 'busy'
                    else:
                        state = 'tool'
                    last = [ts, state, names[0]]
                elif stop_reason in ('end_turn', 'stop_sequence', 'max_tokens', 'refusal'):
                    last = [ts, 'done', None]
                elif last[1] not in ('ask', 'plan', 'tool', 'busy') or last[0] != ts:
                    last = [ts, 'mid', None]
                last_tool = None
                intr = False
            elif kind == 'user':
                raw = buf[s:e]
                if side or any(raw.find(x, 0, 600) != -1 for x in _PROMPT_SKIP[:2]):
                    continue
                # A line a person typed (or the interrupt Claude Code writes when she stops a turn): a stop before it
                # did not go on by itself. A line Claude Code made (a task notification, a command's output: its text
                # starts with "<") is not one a person typed.
                said = _first_text(m.get('content')).lstrip()
                if not said.startswith('<'):
                    for row in pending:
                        if row[5] is None:
                            row[5] = ts if ts is not None else (row[0] if _num(row[0]) else 0.0)
                if said.startswith('[Request interrupted by user'):
                    intr = True
                    last = [ts, 'done', None]
                    last_tool = None
                    continue
                last = [ts, 'mid', None]
                last_tool = None
    if last_tool is not None:
        last = [last_tool[0] if last_tool[0] is not None else last[0], last_tool[1], None]
    return {'v': READ_VERSION, 'title': _cut(title_custom or title_ai), 'custom': _cut(title_custom),
            'ai': _cut(title_ai),
            'stops': stops, 'last': last, 'first': first, 'reply': reply, 'fam': fam, 'intr': intr,
            'seen': sorted(seen_ids), 'off': end, 'head': head}


def _sound(data):
    return isinstance(data, dict) and data.get('v') == READ_VERSION and isinstance(data.get('stops'), list) \
        and isinstance(data.get('last'), list) and len(data['last']) == 3


def _journal(path):
    """A workflow run's journal: {agent id: [key, label, phase, 'started' | 'result' | 'failed']}, in order."""
    agents = {}
    with open(path, 'rb') as f:
        for raw in f:
            d = usage._loads(raw)
            if not d:
                continue
            aid = d.get('agentId')
            if not isinstance(aid, str):
                continue
            if d.get('type') == 'started':
                agents[aid] = [_cut(_text(d.get('key'))), _cut(_text(d.get('label'))), _text(d.get('phase')), 'started']
            elif d.get('type') in ('result', 'failed') and aid in agents:
                agents[aid][3] = d['type']
    return {'v': READ_VERSION, 'agents': agents}


def _run_record(path):
    d = usage._read_json(path)
    if not d and not os.path.exists(path):
        raise OSError('gone')
    name = _text(d.get('workflowName'))
    # the name as shown is cut; the whole name is kept only as a hash, to tell a later run of the same workflow
    return {'v': READ_VERSION, 'name': _cut(name), 'name_h': usage._h(name) if name else None,
            'status': _text(d.get('status')),
            'start': usage.parse_ts(d.get('timestamp')) if isinstance(d.get('timestamp'), str) else
            (d['startTime'] / 1000.0 if _num(d.get('startTime')) else None)}


def _ok(data):
    return isinstance(data, dict) and data.get('v') == READ_VERSION


# ---------------------------------------------------------------- the history pass

ENV = {}        # tests point these at made-up places: projects, vscode_logs, extensions, claude_settings, path, run


def _root():
    return ENV.get('projects') or core.projects()


def run_cmd(cmd, timeout=10):
    """A read-only command's output, or None when it cannot be run. No shell, low priority."""
    runner = ENV.get('run')
    if runner is not None:
        return runner(cmd, timeout)
    try:
        r = subprocess.run(['/usr/bin/nice', '-n', '10'] + list(cmd), capture_output=True, text=True,
                           timeout=timeout, errors='replace')
        return r.stdout if r.returncode == 0 or r.stdout else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def scan(now, budget=PASS_SECONDS, cache_path=None):
    """Read what changed in the chat records of the last DAYS days, for at most `budget` seconds.

    A file that changed is read on from where its last read ended (read_file's prev). A changed file the pass has no
    time left for keeps its earlier reading ('behind'); one never read is 'unread'. Each workflow agent's .meta.json
    is read once and cached by size and change time, inside the same time budget.

    Returns {'files': [file dicts with 'data' (and 'meta' for workflow agents)], 'runs': {run folder: {...}},
    'left': unread + behind, 'unread', 'behind', 'total'}."""
    t0 = time.monotonic()
    cache_path = cache_path or core.data_path(CACHE_FILE)
    cache = usage._load_cache(cache_path)
    if cache.get('keeprun') != READ_VERSION:
        cache = {'version': usage.CACHE_VERSION, 'keeprun': READ_VERSION, 'files': {}}
    files = usage._scan(_root())
    since = now - DAYS * DAY
    keep, unread, behind = [], 0, 0
    stats = {'from_cache': 0, 'read': 0, 'vanished': 0}
    chats = dict(((f['project'], f['session']), f) for f in files if f['kind'] == 'chat')
    wanted = [f for f in files if f['mtime_ns'] >= int((since - 2) * 1e9)]
    need = dict((f['path'], f) for f in wanted)
    for f in wanted:                                        # an agent's chat comes too, for its title
        c = chats.get((f['project'], f['session']))
        if c is not None:
            need.setdefault(c['path'], c)
    # Files already in the cache first, then the newest: a pass that runs out of time has read the freshest.
    order = sorted(need.values(), key=lambda f: (not usage._cache_hit(cache, f['path'], f['size'], f['mtime_ns'],
                                                                      _sound), -f['mtime_ns']))
    for f in order:
        hit = usage._cache_hit(cache, f['path'], f['size'], f['mtime_ns'], _sound)
        old = None if hit else (cache['files'].get(f['path']) or {}).get('data')
        old = old if _sound(old) else None
        if not hit and time.monotonic() - t0 > budget:
            if old is not None:
                keep.append(dict(f, data=old))          # its earlier reading stays in the answer
                behind += 1
            else:
                unread += 1
            continue
        data = usage._cached(cache, f['path'], f['size'], f['mtime_ns'],
                             lambda p, chat=(f['kind'] == 'chat'), old=old: read_file(p, chat, old), stats, _sound)
        if data is not None:
            keep.append(dict(f, data=data))
    runs, used = {}, set(need)
    for f in keep:
        if f['kind'] != 'workflows':
            continue
        p = f['path'][:-len('.jsonl')] + '.meta.json'
        f['meta'] = None
        try:
            st = os.stat(p)
        except OSError:
            continue
        used.add(p)
        if usage._cache_hit(cache, p, st.st_size, st.st_mtime_ns, _ok) or time.monotonic() - t0 <= budget:
            f['meta'] = usage._cached(cache, p, st.st_size, st.st_mtime_ns, _meta, stats, _ok)
    for f in keep:
        if f['kind'] != 'workflows':
            continue
        folder = os.path.dirname(f['path'])
        if folder in runs:
            continue
        r = {'project': f['project'], 'session': f['session'], 'wf': f['wf'], 'journal': None, 'record': None}
        for key, p, reader in (('journal', os.path.join(folder, 'journal.jsonl'), _journal),
                               ('record', os.path.join(f['project'], f['session'], 'workflows', f['wf'] + '.json'),
                                _run_record)):
            used.add(p)
            try:
                st = os.stat(p)
            except OSError:
                continue
            r[key] = usage._cached(cache, p, st.st_size, st.st_mtime_ns, reader, stats, _ok)
        runs[folder] = r
    gone = [p for p in cache['files'] if p not in used]
    for p in gone:                                          # files older than the window leave the cache
        cache['files'].pop(p, None)
        cache['dirty'] = True
    if cache.pop('dirty', False):
        usage._save_cache(cache_path, cache)
    return {'files': keep, 'runs': runs, 'left': unread + behind, 'unread': unread, 'behind': behind,
            'total': len(order), 'chats': chats, 'read': stats['read']}


def _meta(path):
    """A workflow agent's .meta.json (its own path): label, phase, type and model."""
    m = usage._read_json(path)
    if not m and not os.path.exists(path):
        raise OSError('gone')
    return {'v': READ_VERSION, 'label': _cut(_text(m.get('description'))), 'phase': _text(m.get('workflowPhase')),
            'type': _text(m.get('agentType')), 'model': _text(m.get('model'))}


def _cut(text):
    """A label, run name or title cut to TITLE_MAX characters, like chat titles."""
    return text if not isinstance(text, str) or len(text) <= TITLE_MAX else text[:TITLE_MAX - 1] + '…'


def _title(f, chats_data):
    d = chats_data.get((f['project'], f['session']))
    t = (d or {}).get('title') if d else None
    t = t or 'chat %s' % f['session'][:8]
    return t if len(t) <= TITLE_MAX else t[:TITLE_MAX - 1] + '…'


# ---------------------------------------------------------------- the other sources

def read_events(now):
    """The plugin's events (running/events.jsonl), newest 4 MB, the last DAYS days. [] when there are none."""
    path = core.data_path(os.path.join('running', 'events.jsonl'))
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - EVENTS_TAIL))
            raw = f.read()
    except OSError:
        return []
    lines = raw.split(b'\n')
    if size > EVENTS_TAIL:
        lines = lines[1:]
    out = []
    for line in lines:
        d = usage._loads(line) if line.strip() else None
        if d and d.get('v') == 1 and _num(d.get('at')) and d['at'] >= now - DAYS * DAY and isinstance(d.get('kind'),
                                                                                                      str):
            out.append(d)
    return out


def read_json_file(name):
    try:
        with open(core.data_path(name), encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def room_in_build():
    """True when switchboard.room can be imported now (the model-room lane is part of this build)."""
    try:
        from . import room  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 - not part of this build, or broken: as if it were not there
        return False


def running_status():
    """(module there, status) from running.status(); (False, None) when the module is not part of this build."""
    try:
        from . import running  # noqa: F401 - an install without it shows no switch
    except Exception:  # noqa: BLE001 - missing or broken: as if it were not part of this build
        return False, None
    try:
        st = running.status()
        return True, st if isinstance(st, dict) else None
    except Exception:  # noqa: BLE001 - its status says nothing: shown as off
        return True, None


def parse_pmset_log(text):
    """(spans, wakes) from `pmset -g log`: sleep spans [[start, end, cause, on_battery, charge, dark wakes]] and the
    times of every wake, dark or full, oldest first. A span runs from the first "Entering Sleep" to the next full
    wake; the dark wakes in between (a few seconds each, with no network yet) stay inside it."""
    spans, wakes, cur = [], [], None
    rx = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) ([+-]\d{4}) (\S+)\s+(.*)$')
    for line in (text or '').splitlines():
        m = rx.match(line)
        if not m or m.group(3) not in ('Sleep', 'Wake', 'DarkWake'):
            continue
        what, rest = m.group(3), m.group(4).strip()
        full = what == 'Wake' and (rest.startswith('Wake from') or 'to FullWake' in rest)
        if not (full or (what == 'Sleep' and rest.startswith('Entering Sleep'))
                or (what == 'DarkWake' and rest.startswith('DarkWake from'))):
            continue                                # "Wake Requests" and the like
        try:
            t = datetime.strptime(m.group(1) + ' ' + m.group(2), '%Y-%m-%d %H:%M:%S %z').timestamp()
        except ValueError:
            continue
        if what == 'Sleep':
            if cur is None:
                why = re.search(r"due to '([^']*)'", rest)
                charge = re.search(r'Charge:(\d+)%', rest)
                cur = [t, None, why.group(1) if why else '', 'Using Batt' in rest or 'Using BATT' in rest,
                       int(charge.group(1)) if charge else None, 0]
            continue
        wakes.append(t)
        if what == 'DarkWake':
            if cur is not None:
                cur[5] += 1
        elif cur is not None:
            cur[1] = t
            spans.append(cur)
            cur = None
    return spans, wakes


def mac_facts(now):
    """{'spans', 'wakes', 'on_battery', 'charge', 'swap', 'programs', 'sessions', 'known'}: what the Mac says."""
    out = {'spans': [], 'wakes': [], 'on_battery': None, 'charge': None, 'swap': None, 'programs': None,
           'sessions': None, 'problems': []}
    kept = read_json_file(MAC_FILE) or {}
    at = kept.get('at')
    had = isinstance(kept.get('spans'), list)
    if had:         # a corrupt or hand-edited file: only spans of the shape parse_pmset_log writes are kept
        kept['spans'] = [list(x) for x in kept['spans'] if _span_ok(x)]
        kept['wakes'] = sorted(w for w in kept.get('wakes') or [] if _num(w)) \
            if isinstance(kept.get('wakes'), list) else []
    if _num(at) and 0 <= now - at < PMSET_EVERY and had:
        out['spans'], out['wakes'] = kept['spans'], kept.get('wakes') or []
        failed = bool(kept.get('failed'))
    else:
        text = run_cmd(['/usr/bin/pmset', '-g', 'log'], PMSET_TIMEOUT)
        failed = text is None
        if failed:
            # A failed or slow run is remembered like a good one (the same 10 minutes), with the last spans kept.
            spans, wakes = (kept['spans'], kept.get('wakes') or []) if had else ([], [])
        else:
            spans, wakes = parse_pmset_log(text)
            wakes = wakes[-5000:]
        out['spans'], out['wakes'] = spans, wakes
        try:
            core.write_json(core.data_path(MAC_FILE), {'at': now, 'spans': spans, 'wakes': wakes, 'failed': failed})
        except (OSError, RuntimeError, TypeError, ValueError):
            pass
    if failed:
        out['problems'].append('The Mac’s sleep record could not be read just now; the last one read is shown.'
                               if out['spans'] else 'The Mac’s sleep record could not be read.')
    batt = run_cmd(['/usr/bin/pmset', '-g', 'batt'], 5)
    if batt:
        m = re.search(r"drawing from '([^']+)'", batt)
        if m:
            out['on_battery'] = m.group(1) != 'AC Power'
        p = re.search(r'(\d{1,3})%;', batt)
        out['charge'] = int(p.group(1)) if p else None
    swap = run_cmd(['/usr/sbin/sysctl', 'vm.swapusage'], 5)
    m = re.search(r'total = ([\d.]+)M\s+used = ([\d.]+)M', swap or '')
    if m:
        out['swap'] = {'total_mb': float(m.group(1)), 'used_mb': float(m.group(2))}
    ps = run_cmd(['/bin/ps', '-axo', 'command='], 5)
    if ps is not None:
        n, sessions = 0, set()
        for line in ps.splitlines():
            head = line.split(' ', 1)[0]
            if os.path.basename(head) != 'claude' and '@anthropic-ai/claude-code/' not in line:
                continue
            n += 1
            r = re.search(r'--resume[= ]([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', line)
            if r:
                sessions.add(r.group(1))
        out['programs'], out['sessions'] = n, sorted(sessions)
    return out


def _span_ok(x):
    return isinstance(x, (list, tuple)) and len(x) == 6 and _num(x[0]) and _num(x[1]) and isinstance(x[2], str)


def _version(text):
    m = re.search(r'(\d+)\.(\d+)\.(\d+)', text or '')
    return tuple(int(x) for x in m.groups()) if m else None


def claude_facts():
    """{'terminal': version or None, 'terminal_path': the `claude` looked at, 'terminal_npm': bool, 'newest': version
    or None, 'api_key': bool, 'api_kinds': which of 'key', 'token', 'helper' the user settings hold (never a value)}."""
    out = {'terminal': None, 'terminal_path': None, 'terminal_npm': False, 'newest': None, 'api_key': False,
           'api_kinds': []}
    dirs = (ENV.get('path') if ENV.get('path') is not None else
            os.environ.get('PATH', '').split(os.pathsep) + [os.path.join(core.home(), '.npm-global', 'bin'),
                                                            os.path.join(core.home(), '.local', 'bin'),
                                                            '/opt/homebrew/bin', '/usr/local/bin'])
    for d in dirs:
        p = os.path.join(d, 'claude') if d else None
        if not p or not os.path.isfile(p):
            continue
        real = os.path.realpath(p)
        # the version only from its package.json, or from the program's own file name (a native install keeps each
        # version as a file named for it); never from a folder name further up
        base = os.path.basename(real)
        v = _version(base) if re.match(r'^\d+\.\d+\.\d+$', base) else None
        cur = os.path.dirname(real)
        for _ in range(4):
            pkg = usage._read_json(os.path.join(cur, 'package.json'))
            if pkg.get('name') == '@anthropic-ai/claude-code':
                v = _version(str(pkg.get('version'))) or v
                out['terminal_npm'] = True
                break
            cur = os.path.dirname(cur)
        out['terminal'], out['terminal_path'] = v, p
        break
    ext = ENV.get('extensions') or os.path.join(core.home(), '.vscode', 'extensions')
    try:
        names = os.listdir(ext)
    except OSError:
        names = []
    found = [_version(n) for n in names if n.startswith('anthropic.claude-code-')]
    found = [v for v in found if v]
    if out['terminal']:
        found.append(out['terminal'])
    out['newest'] = max(found) if found else None
    settings = usage._read_json(ENV.get('claude_settings') or os.path.join(core.claude_dir(), 'settings.json'))
    env = settings.get('env') if isinstance(settings.get('env'), dict) else {}
    has = lambda k: isinstance(env.get(k), str) and bool(env.get(k).strip())  # noqa: E731
    out['api_kinds'] = [k for k, there in (('key', has('ANTHROPIC_API_KEY')), ('token', has('ANTHROPIC_AUTH_TOKEN')),
                                           ('helper', bool(_text(settings.get('apiKeyHelper'))))) if there]
    out['api_key'] = bool(out['api_kinds'])
    return out


PANEL_FILE = 'keeprun-panels.json'


def _panel_times(path, prev=None):
    """Times of the "did not finish starting" lines of one log file (local times as written).

    prev: this file's earlier reading. A log that only grew (same first bytes) is read on from where that read ended,
    never again from byte 0. A line still being written waits for the next pass."""
    with open(path, 'rb') as f:
        size = os.fstat(f.fileno()).st_size
        end = _complete_end(f, size)
        go_on = (_ok(prev) and isinstance(prev.get('times'), list) and _num(prev.get('off'))
                 and 0 < prev['off'] <= end and prev.get('head') == _head_mark(f, min(prev['off'], 4096)))
        out = [t for t in prev['times'] if _num(t)] if go_on else []
        start = prev['off'] if go_on else 0
        head = _head_mark(f, min(end, 4096))
        f.seek(start)
        raw = f.read(end - start)
    i = raw.find(b'did not finish starting')
    while i != -1:
        start = raw.rfind(b'\n', 0, i) + 1
        try:
            out.append(time.mktime(time.strptime(raw[start:start + 19].decode('ascii'), '%Y-%m-%d %H:%M:%S')))
        except (ValueError, UnicodeDecodeError, OverflowError):
            pass
        i = raw.find(b'did not finish starting', i + 1)
    return {'v': READ_VERSION, 'times': out, 'off': end, 'head': head}


def panel_lines(now, deadline=None, cache_path=None, stats=None):
    """(times, left): times of "did not finish starting" lines in the extension's logs of the last DAYS days, and how
    many log files were not read because the pass ran out of time. Each file is read once and kept by (path, size,
    change time) in keeprun-panels.json; a file not yet kept is read only while time.monotonic() < deadline."""
    base = ENV.get('vscode_logs') or os.path.join(core.home(), 'Library', 'Application Support', 'Code', 'logs')
    out, left = [], 0
    stats = stats if stats is not None else {'from_cache': 0, 'read': 0, 'vanished': 0}
    try:
        sessions = os.listdir(base)
    except OSError:
        return out, left
    cache_path = cache_path or core.data_path(PANEL_FILE)
    cache = usage._load_cache(cache_path)
    if cache.get('panels') != READ_VERSION:
        cache = {'version': usage.CACHE_VERSION, 'panels': READ_VERSION, 'files': {}}
    since = now - DAYS * DAY
    used = set()
    for s in sorted(sessions):
        top = os.path.join(base, s)
        for dirpath, dirnames, filenames in os.walk(top):
            if os.path.basename(dirpath) != 'Anthropic.claude-code':
                continue
            for fn in sorted(filenames):
                if not fn.endswith('.log'):
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if st.st_mtime < since:
                    continue
                used.add(p)
                if not usage._cache_hit(cache, p, st.st_size, st.st_mtime_ns, _ok) and deadline is not None \
                        and time.monotonic() > deadline:
                    left += 1
                    continue
                old = (cache['files'].get(p) or {}).get('data')
                got = usage._cached(cache, p, st.st_size, st.st_mtime_ns,
                                    lambda q, old=old: _panel_times(q, old), stats, _ok)
                out.extend(t for t in (got or {}).get('times') or [] if _num(t) and t >= since)
    for p in [p for p in cache['files'] if p not in used]:
        cache['files'].pop(p, None)
        cache['dirty'] = True
    if cache.pop('dirty', False):
        usage._save_cache(cache_path, cache)
    return sorted(out), left


# ---------------------------------------------------------------- putting it together

def _near_after(t, marks, within):
    """True when one of the sorted `marks` falls in [t - within, t]."""
    i = bisect.bisect_right(marks, t)
    return i > 0 and t - marks[i - 1] <= within


def chat_stops(scanned, now):
    """Every stop of the last DAYS days, merged across files, oldest first."""
    chats_data = dict((k, None) for k in scanned['chats'])
    for f in scanned['files']:
        if f['kind'] == 'chat':
            chats_data[(f['project'], f['session'])] = f['data']
    seen, out = set(), []
    since = now - DAYS * DAY
    for f in sorted(scanned['files'], key=lambda f: (f['kind'] != 'chat', f['path'])):
        if f['kind'] == 'workflows':
            continue                # a workflow agent's stop is counted once, as a dead agent (runs_view)
        for row in f['data'].get('stops') or []:
            t, cause, asked, text, records, nxt_p, nxt_r, hid, resets, _side = (list(row) + [None] * 10)[:10]
            if not _num(t) or t < since or hid in seen:
                continue
            seen.add(hid)
            # no reply yet: "never went on" only once an hour has passed; before that it is too recent to tell
            how, wait = ('never' if now - t >= NEVER_WAIT else 'recent'), None
            if _num(nxt_r):
                if _num(nxt_p) and nxt_p <= nxt_r:
                    how, wait = 'typed', nxt_p - t
                else:
                    how = 'itself'
            out.append({'t': t, 'cause': cause, 'asked': asked, 'text': text or '', 'records': records or 1,
                        'session': f['session'], 'kind': f['kind'], 'wf': f.get('wf'),
                        'title': _title(f, chats_data), 'how': how, 'wait': wait,
                        'lost': (nxt_r - t) if _num(nxt_r) and nxt_r >= t else None, 'resets': resets,
                        'by': 'records'})
    out.sort(key=lambda s: s['t'])
    return out


def merge_plugin(stops, events, titles, now=None):
    """The plugin's stops replace the chat records' for the same stop (same chat, within 2 minutes); its others are
    added. Returns (stops, {'held', 'resumed', 'gave_up', 'restarts', 'continued'})."""
    counts = {'held': 0, 'held_s': 0.0, 'resumed': 0, 'gave_up': 0, 'restarts': 0, 'continued': 0}
    by_at = {}
    for e in events:
        k = e.get('kind')
        if k == 'held':
            counts['held'] += 1
            counts['held_s'] += e['seconds'] if _num(e.get('seconds')) else 0.0
        elif k == 'restart':
            counts['restarts'] += 1
            counts['continued'] += 1 if e.get('continued') else 0
        elif k in ('resumed', 'gave_up') and _num(e.get('of')):
            by_at[(e.get('session'), round(e['of'], 3))] = e
    for e in events:
        if e.get('kind') != 'stop' or e.get('cause') not in CAUSES:
            continue
        sid = e.get('session') if isinstance(e.get('session'), str) else '?'
        follow = by_at.get((sid, round(e['at'], 3)))
        how, lost = ('recent' if _num(now) and now - e['at'] < NEVER_WAIT else 'never'), None
        if follow and follow.get('kind') == 'resumed':
            how, lost = 'plugin', follow.get('after_s') if _num(follow.get('after_s')) else None
            counts['resumed'] += 1
        elif follow:
            how = 'gave_up'
            counts['gave_up'] += 1
        # the same stop: same chat, same kind (a helper's record is never the main chat's), the closest within 2 min
        kind = 'helpers' if e.get('agent') else 'chat'
        near = [s for s in stops if s['session'] == sid and s['kind'] == kind and s['by'] == 'records'
                and abs(s['t'] - e['at']) <= 120]
        match = min(near, key=lambda s: abs(s['t'] - e['at'])) if near else None
        row = {'t': e['at'], 'cause': e['cause'], 'asked': e.get('asked'), 'text': '',
               'records': 1, 'session': sid, 'kind': 'helpers' if e.get('agent') else 'chat', 'wf': None,
               'title': titles.get(sid) or (match or {}).get('title') or 'chat %s' % sid[:8], 'how': how,
               'wait': None, 'lost': lost, 'resets': None, 'by': 'plugin',
               'why': follow.get('why') if follow and follow.get('kind') == 'gave_up' else None}
        if match is not None:
            if match.get('lost') is not None and row['lost'] is None and how != 'gave_up':
                row['lost'] = match['lost']
            if how in ('never', 'recent') and match['how'] not in ('never', 'recent'):
                row['how'], row['wait'] = match['how'], match['wait']
            stops[stops.index(match)] = row
        else:
            stops.append(row)
    stops.sort(key=lambda s: s['t'])
    return stops, counts


def runs_view(scanned, now):
    """Workflow runs of the last DAYS days, with what happened to their agents. Returns (runs, totals)."""
    files = dict((f['path'], f) for f in scanned['files'])
    chats_data = {}
    for f in scanned['files']:
        if f['kind'] == 'chat':
            chats_data[(f['project'], f['session'])] = f['data']
    runs, known, firsts = [], [], {}
    for f in scanned['files']:
        if f['kind'] == 'workflows' and _num(f['data'].get('first')):
            firsts.setdefault(os.path.dirname(f['path']), []).append(f['data']['first'])
    since = now - DAYS * DAY
    for folder, r in scanned['runs'].items():
        j = (r.get('journal') or {}).get('agents') or {}
        rec = r.get('record') or {}
        chat = {'project': r['project'], 'session': r['session']}
        rows = []
        for aid, (key, label, phase, ev) in j.items():
            p = os.path.join(folder, 'agent-%s.jsonl' % aid)
            f = files.get(p)
            d = f['data'] if f else {}
            meta = (f.get('meta') if f else None) or {}
            last = d.get('last') or [None, None, None]
            mtime = f['mtime_ns'] / 1e9 if f else None
            # Dead only when its record ENDS on an error: a stop followed by more work is no death, and an agent a
            # person stopped (or one that ended with no error written) is not called dead.
            dead, why, dead_t = False, None, None
            stops = d.get('stops') or []
            if ev != 'result' and stops and last[1] == 'stop':
                dead, why, dead_t = True, stops[-1][1], stops[-1][0]
            fam = d.get('fam') or family(meta.get('model'))
            row = {'id': aid, 'label': _cut(label or meta.get('label') or key or aid[:8]), 'phase': phase,
                   'returned': ev == 'result', 'dead': dead, 'why': why, 'dead_t': dead_t, 'fam': fam,
                   'first': d.get('first'), 'last': last[0] or d.get('reply') or mtime,
                   'chat': (r['project'], r['session'])}
            rows.append(row)
        start = rec.get('start') or min(firsts.get(folder) or [None])
        shown = rec.get('name') or r['wf']
        full = rec.get('name_h') or usage._h(r['wf'])          # the whole name, as a hash
        if _num(start):     # every run with agents is a candidate "later run", whatever its journal says
            known.append((r['session'], full, start))
        if not rows or not _num(start) or start < since:
            continue
        runs.append({'folder': folder, 'name': _cut(shown), 'full': full, 'status': rec.get('status'),
                     'chat': _title(dict(chat), chats_data), 'session': r['session'], 'at': start, 'agents': rows})
    out, tot = [], {'agents': 0, 'dead': 0, 'why': {}, 'never_rerun': 0}
    for run in sorted(runs, key=lambda x: -x['at']):
        rows = run['agents']
        dead = [a for a in rows if a['dead']]
        tot['agents'] += len(rows)
        tot['dead'] += len(dead)
        why = {}
        for a in dead:
            why[a['why']] = why.get(a['why'], 0) + 1
            tot['why'][a['why']] = tot['why'].get(a['why'], 0) + 1
        # Only this is said of a run again: whether a later run of the SAME workflow name in the SAME chat started
        # after it began (yes or no). Which agents it did again, and when, is not measured.
        later = bool(dead) and any(sid == run['session'] and name == run['full'] and at > run['at']
                                   for sid, name, at in known)
        never = 0 if later else len(dead)
        tot['never_rerun'] += never
        out.append({'name': run['name'], 'chat': run['chat'], 'session': run['session'], 'at': iso(run['at']),
                    't': run['at'], 'agents': len(rows), 'returned': sum(1 for a in rows if a['returned']),
                    'dead': len(dead), 'why': why, 'said_completed': bool(dead) and run['status'] == 'completed',
                    'later_run': later, 'never_rerun': never, 'dead_t': [a['dead_t'] for a in dead],
                    'labels': [a['label'] for a in dead][:6],
                    'models': sorted(set(a['fam'] for a in rows if a['fam']))})
    return out, tot


def chats_now(scanned, mac, now):
    """(waiting, cut_off, quiet_open): main chats that wait on you, that were cut off, and open ones gone quiet."""
    running = set(mac.get('sessions') or []) if mac.get('sessions') is not None else None
    active = {}
    for f in scanned['files']:
        if f['kind'] != 'chat':
            k = (f['project'], f['session'])
            active[k] = max(active.get(k, 0), f['mtime_ns'] / 1e9)
    waiting, cut, quiet = [], [], []
    for f in scanned['files']:
        if f['kind'] != 'chat':
            continue
        d = f['data']
        t, state, tool = (list(d.get('last') or []) + [None] * 3)[:3]
        mtime = f['mtime_ns'] / 1e9
        idle = now - mtime
        helpers_busy = now - active.get((f['project'], f['session']), 0) < QUIET
        title = d.get('title') or 'chat %s' % f['session'][:8]
        title = title if len(title) <= TITLE_MAX else title[:TITLE_MAX - 1] + '…'
        # A chat found as --resume <id> in ps runs. One not found is not known to have ended (a program started
        # without --resume, or an install ps is not matched against), unless no Claude Code program runs at all.
        alive = None if running is None else True if f['session'] in running else \
            False if mac.get('programs') == 0 else None
        row = {'title': title, 'session': f['session'], 'since': iso(t if _num(t) else mtime),
               't': t if _num(t) else mtime}
        if alive and idle >= OPEN_QUIET:
            quiet.append(dict(row, days=int(idle // DAY)))
        if idle < QUIET or helpers_busy or now - (t if _num(t) else mtime) > CUT_DAYS * DAY:
            continue
        if state == 'shutdown' or (state in ('ask', 'plan', 'tool', 'mid') and alive is False):
            cut.append(row)                     # its program ended in the turn
        elif state in ('ask', 'plan') and alive:
            # only a question or a plan is known to wait on you, and only while its program is known to run; a tool
            # call with no result may be running
            what = 'a question' if state == 'ask' else 'a plan to approve'
            # from the time of the question's own record, never the file's change time (a reload touches files)
            waiting.append(dict(row, what=what, minutes=int((now - (t if _num(t) else mtime)) // 60)))
        elif state == 'mid' and idle >= MID_QUIET:
            cut.append(row)                     # a turn that has not moved for half an hour has not continued
    waiting.sort(key=lambda w: -w['minutes'])
    cut.sort(key=lambda c: c['t'])
    quiet.sort(key=lambda q: q['t'])
    return waiting, cut, quiet


def cut_groups(cut):
    """The cut-off chats in groups that ended together (within CLUSTER seconds of each other)."""
    groups = []
    for c in cut:
        if groups and c['t'] - groups[-1][-1]['t'] <= CLUSTER:
            groups[-1].append(c)
        else:
            groups.append([c])
    return groups


def history_facts(now, lines=None):
    """{'switches', 'every_out' [[start, end]], 'auto_off' [[start, end]], 'auto_on' (now, from the last line)}."""
    if lines is None:
        try:
            lines = history.read(5000)
        except Exception:  # noqa: BLE001
            lines = []
    since = now - DAYS * DAY
    switches, out_spans, off_spans = [], [], []
    open_out = open_off = None
    auto_on = None
    for x in sorted((x for x in lines if isinstance(x, dict) and _num(x.get('t'))), key=lambda x: x['t']):
        t, k, text = x['t'], x.get('kind'), str(x.get('text') or '')
        if k == 'switch':
            switches.append({'t': t, 'from': _text(x.get('from')), 'to': _text(x.get('to')) or _text(x.get('account')),
                             'by': _text(x.get('by'))})
            if open_out is not None:
                out_spans.append([open_out, t])
                open_out = None
        elif k == 'limit' and text.startswith('Every account is out'):
            open_out = t if open_out is None else open_out
        elif k == 'limit' and 'refilled' in text and open_out is not None:
            out_spans.append([open_out, t])
            open_out = None
        elif k == 'auto' and isinstance(x.get('on'), bool):
            auto_on = x['on']
            if not x['on'] and open_off is None:
                open_off = t
            elif x['on'] and open_off is not None:
                off_spans.append([open_off, t])
                open_off = None
    if open_out is not None:
        out_spans.append([open_out, now])
    if open_off is not None:
        off_spans.append([open_off, now])
    keep = lambda spans: [s for s in spans if s[1] >= since and s[1] - s[0] >= 60]  # noqa: E731
    return {'switches': [s for s in switches if s['t'] >= since], 'every_out': keep(out_spans),
            'auto_off': keep(off_spans), 'auto_on': auto_on}


def room_gap(now, readings, switches, fam='fable'):
    """Minutes in the last DAYS days that the account in use had no room for `fam` while another account had room for
    it, from the readings log (a reading counts for 3 hours) and History's switches. None when not known."""
    name = MODEL_NAME[fam]
    if not readings or not switches:
        return None
    since = now - DAYS * DAY
    rows = dict((a, sorted(r, key=lambda x: x['t'])) for a, r in readings.items())
    ons = sorted((s['t'], s['to']) for s in switches if s.get('to'))

    def at(account, t):
        best = None
        for r in rows.get(account) or ():
            if r['t'] > t:
                break
            best = r
        return best if best is not None and t - best['t'] <= 3 * 3600 else None

    def blocked(r):
        if r is None:
            return None
        vals = [r.get('five'), r.get('week'), (r.get('models') or {}).get(name)]
        return any(_num(v) and v >= 100 for v in vals)

    minutes = 0
    step = 300
    t = max(since, ons[0][0])
    while t < now:
        on = [a for s, a in ons if s <= t]
        cur = on[-1] if on else None
        if cur and blocked(at(cur, t)) and any(blocked(at(a, t)) is False for a in rows if a.lower() != cur.lower()):
            minutes += step / 60.0
        t += step
    return int(minutes + 0.5)


# ---------------------------------------------------------------- the payload

def _handled_rows(stops, runs_tot, runs, mac, hist, on, has_switch, plan, auto_now, awake_on, now, room_in=False,
                  stay_on=None):
    """The causes as rows: key, name, count, went_on_min, last, doing, handled, step, examples."""
    by = {}
    for s in stops:
        by.setdefault(s['cause'], []).append(s)
    rows = []
    loop_room = plan is not None
    for key in CAUSES:
        got = by.get(key) or []
        if not got:
            continue
        name = NAMES.get(key, key)
        fams = [s['asked'] for s in got if s.get('asked')]
        note = None
        if key == 'limit_model':
            # a model is named only when every stop of the row names that same one; else "A model", never a guess
            one = fams[0] if fams and len(fams) == len(got) and len(set(fams)) == 1 else None
            name = '%s used up on the account in use' % MODEL_NAME.get(one, 'A model')
        if key == 'signed_out':
            marks = sorted(x['t'] for x in hist['switches'])
            near = sum(1 for s in got if _near_after(s['t'], marks, SWITCH_NEAR))
            if near:
                note = '%d of %d within 5 minutes after a switch' % (near, len(got))
        near_wake = False
        if key == 'offline':
            wakes = sorted(mac.get('wakes') or [])
            covered = [s for s in got if wakes and wakes[0] <= s['t'] <= wakes[-1] + 3600]
            near = sum(1 for s in covered if _near_after(s['t'], wakes, WAKE_NEAR))
            if near:
                note = '%d of %d within 3 minutes after a wake' % (near, len(got))
            near_wake = bool(covered) and near * 2 >= len(covered)
        doing, step = _doing(key, on, has_switch, loop_room, auto_now, room_in, stay_on)
        if key == 'offline' and step is None and not on and near_wake:
            step = 'lid'                        # most stops came on waking: the step is the lid
        plugged = sum(1 for s in got if s['how'] == 'plugin')
        rows.append({'key': key, 'name': name, 'note': note, 'count': len(got), 'went_on_min': went_on_min(got),
                     'went_on_n': went_on_n(got),
                     'last': iso(got[-1]['t']), 'doing': doing,
                     # "handled" only where it is measured: Keep running is on and continued some of these
                     'handled': 'yes' if on and plugged else None, 'step': step,
                     'continued': sum(1 for s in got if s['how'] in ('itself', 'plugin')),
                     'typed': sum(1 for s in got if s['how'] == 'typed'),
                     'never': sum(1 for s in got if s['how'] in ('never', 'gave_up')),
                     'recent': sum(1 for s in got if s['how'] == 'recent'),
                     'examples': [{'at': iso(s['t']), 'title': s['title'], 'helper': s['kind'] != 'chat'}
                                  for s in got[-3:][::-1]]})
    lid = [s for s in mac.get('spans') or [] if s[2] == 'Clamshell Sleep' and s[3] and _num(s[1])
           and s[1] - s[0] >= SLEEP_MIN and s[1] >= now - DAYS * DAY]
    if lid:
        doing = ('Keep the Mac awake is on in Switchboard’s settings.' if awake_on else
                 'Keep the Mac awake is off in Switchboard’s settings.')
        rows.append({'key': 'lid', 'name': NAMES['lid'], 'note': None, 'count': len(lid), 'went_on_min': None,
                     'went_on_n': None, 'last': iso(lid[-1][0]),
                     'doing': doing, 'handled': None, 'step': 'lid', 'recent': None,
                     'continued': None, 'typed': None, 'never': None,
                     'examples': [{'at': iso(s[0]), 'title': 'Slept with the lid shut', 'helper': False}
                                  for s in lid[-3:][::-1]]})
    if hist['every_out']:
        sp = hist['every_out']
        rows.append({'key': 'every_out', 'name': NAMES['every_out'], 'note': None, 'count': len(sp),
                     'went_on_min': None, 'went_on_n': None, 'last': iso(sp[-1][0]), 'recent': None,
                     'doing': EVERY_OUT_ROOM if room_in else
                     'Switchboard says every account is out when the asked model is used up.',
                     'handled': None, 'step': None, 'continued': None, 'typed': None,
                     'never': None, 'examples': [{'at': iso(a), 'title': 'Said every account is out', 'helper': False}
                                                 for a, b in sp[-3:][::-1]]})
    if hist['auto_off']:
        sp = hist['auto_off']
        rows.append({'key': 'auto_off', 'name': NAMES['auto_off'], 'note': None, 'count': len(sp),
                     'went_on_min': None, 'went_on_n': None, 'last': iso(sp[-1][0]), 'recent': None,
                     'doing': 'It is on now.' if auto_now else 'It is off now.',
                     'handled': None, 'step': None if auto_now else 'auto',
                     'continued': None, 'typed': None, 'never': None,
                     'examples': [{'at': iso(a), 'title': 'Turned off', 'helper': False}
                                  for a, b in sp[-3:][::-1]]})
    if runs_tot['dead']:
        # when each run's agents died (the time of the error their records end on), never when the run started
        died = sorted(((max(t for t in r['dead_t'] if _num(t)), r) for r in runs
                       if r['dead'] and any(_num(t) for t in r.get('dead_t') or [])), key=lambda x: -x[0])
        rows.append({'key': 'agents', 'name': NAMES['agents'], 'note': None, 'count': runs_tot['dead'],
                     'went_on_min': None, 'went_on_n': None, 'last': iso(died[0][0]) if died else None, 'recent': None,
                     'doing': 'Agents are held and continued like chats.' if on else '',
                     'handled': None, 'step': 'runs' if runs_tot['never_rerun'] else None,
                     'continued': None, 'typed': None, 'never': None,
                     'examples': [{'at': iso(t), 'title': '%s (%s)' % (r['name'], r['chat']), 'helper': True}
                                  for t, r in died[:3]]})
    order = dict((k, i) for i, k in enumerate(CAUSES + ('lid', 'every_out', 'auto_off', 'agents')))
    rows.sort(key=lambda r: (-r['count'], order.get(r['key'], 99)))     # by how many times, never by time
    return rows


def went_on_n(got):
    """How many stops went_on_min is the middle of (those that went on by themselves); None when none did."""
    return sum(1 for s in got if s.get('how') == 'itself' and _num(s.get('lost'))) or None


def went_on_min(got):
    """How many minutes after the stop the next reply came, for the stops that went on by themselves (no typed line
    between, read from the chat's own records): the middle value in whole minutes; None when none did. Stops that
    went on after a typed line, or that Keep running continued, are not in it (those are other measures)."""
    waits = sorted(s['lost'] for s in got if s.get('how') == 'itself' and _num(s.get('lost')))
    if not waits:
        return None
    return int(waits[(len(waits) - 1) // 2] / 60 + 0.5)


EVERY_OUT_ROOM = ('Switchboard says every account is out only when no account has room on its 5-hour and weekly '
                  'windows.')


def stay_signed_in():
    """The stay-signed-in setting as read: True, False, or None when it cannot be read (a missing key means on)."""
    s = core.settings()
    pol = s.get('auto_policy') if isinstance(s, dict) and isinstance(s.get('auto_policy'), dict) else {}
    v = pol.get('stay_signed_in', True)
    return v if isinstance(v, bool) else None


def _doing(key, on, has_switch, loop_room, auto_now, room_in=False, stay_on=None):
    """(what Switchboard does about a cause now, the step key or None). Only what Switchboard or its settings do is
    said; nothing about what Claude Code does is claimed.

    room_in: switchboard.room can be imported (the model-room lane is in this build); has_switch: switchboard.running
    can (lane mod). Without the running module there is no switch, no "Turn on Keep running" step and no sentence
    about Keep running."""
    plug = bool(on)
    off_step = 'on' if has_switch and not on else None
    if key == 'limit_model':
        if plug:
            return (ON_WORDS, None)
        if room_in:                     # the room part moves by model room only while automatic switching is on
            return ('Automatic switching is on.', off_step) if auto_now else ('Automatic switching is off.', 'auto')
        return ('A request stopped by the limit ends the turn.', off_step)
    if key in ('limit_5hour', 'limit_weekly', 'limit_credits', 'limit_other'):
        if not auto_now and key in ('limit_5hour', 'limit_weekly'):
            return ('Automatic switching is off.', 'auto')
        if plug and key != 'limit_other':
            return ('Switchboard moves Claude Code before the line, and a stopped chat continues by itself.', None)
        if key in ('limit_credits', 'limit_other'):
            return ('A request stopped by the limit ends the turn.', off_step)
        return ('Automatic switching is on.', off_step)
    if key == 'signed_out':                 # the put-back is said only while the setting that does it is read as on
        if stay_on is not True:
            return ('', off_step)
        return ('Switchboard puts back a sign-in that went missing, or moves Claude Code to a signed-in account.' +
                (' The chat then continues by itself.' if plug else ''), off_step)
    if key in ('offline', 'overloaded'):
        if plug:
            return ('The request waits inside the turn, then goes on.', None)
        return ('', off_step)
    if key in ('restart', 'empty', 'error_other'):
        if plug:
            return ('A chat that was working continues by itself.', None)
        return ('', off_step if key != 'restart' else 'cut')
    if key == 'flagged':
        return ('Claude’s safeguards flagged the request.', None)
    return ('', None)


def _window(stops, runs, now, days):
    """Exact counts of one window: early stops in chats and their helpers (never a workflow agent's: those are counted
    once, as dead agents, by the time of the error their record ends on), and what came after each stop."""
    since = now - days * DAY
    got = [s for s in stops if s['t'] >= since and s['kind'] != 'workflows']
    dead = sum(1 for r in runs for t in r.get('dead_t') or [] if _num(t) and t >= since)
    return {'stops': len(got), 'chats': sum(1 for s in got if s['kind'] == 'chat'),
            'helpers': sum(1 for s in got if s['kind'] != 'chat'), 'agents_dead': dead,
            'itself': sum(1 for s in got if s['how'] == 'itself'),
            'plugin': sum(1 for s in got if s['how'] == 'plugin'),
            'continued': sum(1 for s in got if s['how'] in ('itself', 'plugin')),
            'typed': sum(1 for s in got if s['how'] == 'typed'),
            'never': sum(1 for s in got if s['how'] in ('never', 'gave_up')),
            'recent': sum(1 for s in got if s['how'] == 'recent')}


def sentence(w, behind=0):
    """The week in exact counts: how many stopped early, how many workflow agents died, and what came after.
    behind: changed chat files not read again yet (their earlier reading is counted): said, and never "none"."""
    more = ' (%s being read again)' % plural(behind, 'file is', 'files are') if behind else ''
    if not w['stops'] and not w['agents_dead']:
        if behind:
            return 'Last 7 days: no early stop found in your chats so far%s.' % more
        return 'No chat, helper or workflow agent stopped early in the last 7 days.'
    parts = []
    if w['stops']:
        where = 'chats and their helpers' if w['chats'] and w['helpers'] else 'chats' if w['chats'] else 'helpers'
        parts.append('%s in %s' % (plural(w['stops'], 'early stop'), where))
    if w['agents_dead']:
        parts.append(plural(w['agents_dead'], 'workflow agent') + ' died')
    text = 'Last 7 days: ' + (', and ' if w['stops'] and w['helpers'] and w['chats'] else ' and ').join(parts) + '.'
    if w['stops']:
        after = ['%s went on by %s' % (str(w['itself']) if w['itself'] else 'none',
                                       'itself' if w['itself'] == 1 else 'themselves')]
        if w.get('plugin'):
            after.append('%d continued by Keep running' % w['plugin'])
        if w['typed']:
            after.append('%d after you typed' % w['typed'])
        if w['never']:
            after.append('%d never went on' % w['never'])
        if w.get('recent'):
            after.append('%d less than an hour ago' % w['recent'])
        text += ' Of the stops, ' + ', '.join(after) + '.'
    if more:
        text = text[:-1] + more + '.'
    return text


def reading_sentence(done, total):
    """While part of the chat history has never been read: no totals, only how far the reading is."""
    return 'Reading your chats: %s of %s files so far.' % ('{:,}'.format(done), '{:,}'.format(total))


ON_WORDS = ('When a model’s usage limit is reached on the account in use, Keep running sends the request to the next '
            'model with room, and it continues a chat that stopped.')


def _in_spans(t, spans):
    return any(_num(a) and _num(b) and a <= t <= b for a, b in spans or [])


def _gb(mb):
    return ('%.1f' % (mb / 1024.0)).rstrip('0').rstrip('.')


def _home_path(p):
    h = core.home()
    return '~' + p[len(h):] if p and h and (p == h or p.startswith(h.rstrip('/') + '/')) else p


def steps_for(on, has_switch, stops, runs, runs_tot, waiting, cut, quiet, mac, claude, awake_on, auto_now, now,
              hist=None):
    """The steps, the most valuable first; each only when its numbers are true."""
    out = []
    since = now - DAYS * DAY
    hist = hist or {}
    handled_by_plugin = [s for s in stops if s['cause'] in CAUSES and s['cause'] not in ('flagged', 'too_long',
                                                                                         'blocked_403')]
    never = sum(1 for s in handled_by_plugin if s['how'] in ('never', 'typed'))
    if has_switch and not on and never:         # never "0 early stops ... went on only after you typed"
        out.append({'key': 'on', 'title': 'Turn on Keep running',
                    'why': '%s in the last 14 days went on only after you typed, or never. %s'
                           % (plural(never, 'early stop'), ON_WORDS),
                    'action': {'kind': 'press', 'post': '/api/running', 'body': {'on': True}, 'label': 'Turn on'},
                    'items': []})
    # Only stops whose time falls inside a span History says automatic switching was off.
    limits = [s for s in stops if s['cause'] in ('limit_5hour', 'limit_weekly')
              and _in_spans(s['t'], hist.get('auto_off'))]
    if not auto_now and limits:
        out.append({'key': 'auto', 'title': 'Turn on automatic switching',
                    'why': '%s at a 5-hour or weekly limit in the last 14 days came while it was off.'
                           % plural(len(limits), 'early stop'),
                    'action': {'kind': 'press', 'post': '/api/auto', 'body': {'enabled': True}, 'label': 'Turn on'},
                    'items': []})
    for group in cut_groups(cut):
        n = len(group)
        when = clock(group[0]['t'], now)
        # what ended them (VS Code, Terminal, a restart, power) is not read from anything: not named
        if n >= 2:
            title = '%d chats were working at %s and have not continued.' % (n, when)
        else:
            title = 'A chat was working at %s and has not continued.' % when
        out.append({'key': 'cut', 'title': title, 'why': 'Open each and type continue.', 'action': None,
                    'items': [{'title': c['title'], 'at': c['since']} for c in group]})
    lost_runs = [r for r in runs if r['never_rerun']]
    if lost_runs:
        items = []
        for r in lost_runs:
            why = ', '.join('%d %s' % (n, WHY_WORDS.get(k, k))
                            for k, n in sorted(r['why'].items(), key=lambda x: -x[1]))
            items.append({'title': '%s in %s' % (r['name'], r['chat']), 'at': r['at'],
                          'note': '%d of %d agents died (%s)%s' % (r['dead'], r['agents'], why,
                                                                   '; the run still said completed'
                                                                   if r['said_completed'] else '')})
        out.append({'key': 'runs', 'title': '%s had agents that died, and no later run of the same workflow with '
                                            'agents started in that chat.' % plural(len(lost_runs), 'workflow run'),
                    'why': 'Ask each chat to resume its run.', 'action': None, 'items': items[:8]})
    if waiting:
        out.append({'key': 'waiting', 'title': '%s waiting for you.' % plural(len(waiting), 'chat is', 'chats are'),
                    'why': 'The longest first.', 'action': None,
                    'items': [{'title': w['title'], 'at': w['since'], 'note': 'Waiting %s for %s' % (
                        hours(w['minutes'] * 60), w['what'])} for w in waiting]})
    lid = [s for s in mac.get('spans') or [] if s[2] == 'Clamshell Sleep' and s[3] and _num(s[1])
           and s[1] - s[0] >= SLEEP_MIN and s[1] >= since]
    if lid:
        out.append({'key': 'lid', 'title': 'Plug in before you close the lid.',
                    'why': 'The Mac slept %s on battery with the lid shut (%s asleep in all).'
                           % (plural(len(lid), 'time'), hours(sum(s[1] - s[0] for s in lid))),
                    'action': None if awake_on else {'kind': 'press', 'post': '/api/awake', 'body': {'on': True},
                                                     'label': 'Keep the Mac awake'},
                    'items': []})
    swap = mac.get('swap')
    # A fixed figure of swap in use, never a share: macOS reports the swap it has allocated as the total.
    if swap and _num(swap.get('used_mb')) and swap['used_mb'] >= SWAP_GB * 1024 and quiet:
        out.append({'key': 'memory', 'title': 'Close the chats you are done with.',
                    'why': '%s GB of swap in use (%s GB allocated). %s been quiet for 2 days or more.'
                           % (_gb(swap['used_mb']), _gb(swap.get('total_mb') or 0),
                              plural(len(quiet), 'open chat has', 'open chats have')),
                    'action': None,
                    'items': [{'title': q['title'], 'at': q['since'], 'note': 'Quiet %d days' % q['days']}
                              for q in quiet]})
    term, newest = claude.get('terminal'), claude.get('newest')
    if term and newest and term < newest and term < FABLE_MIN:
        # Which `claude` a terminal finds cannot be known from here (its PATH is not this program's), so no line to
        # copy: only the program looked at and the two versions.
        where = _home_path(claude.get('terminal_path')) or 'claude'
        out.append({'key': 'terminal', 'title': 'The claude program at %s is older than the VS Code extension.'
                                                % where,
                    'why': 'It is Claude Code %s. The extension is %s.' % (
                        '.'.join(map(str, term)), '.'.join(map(str, newest))),
                    'action': None, 'items': []})
    kinds = claude.get('api_kinds') or ([] if not claude.get('api_key') else ['key'])
    if kinds:
        names = {'key': 'an API key (ANTHROPIC_API_KEY in env)', 'token': 'an auth token (ANTHROPIC_AUTH_TOKEN in env)',
                 'helper': 'a key helper (apiKeyHelper)'}
        found = [names[k] for k in ('key', 'token', 'helper') if k in kinds]
        title = 'Claude Code’s settings hold %s.' % (' and '.join(found) if len(found) < 3 else
                                                       ', '.join(found[:2]) + ' and ' + found[2])
        out.append({'key': 'api_key', 'title': title,
                    'why': 'The value is not read into this page.',
                    'action': None, 'items': []})
    if runs_tot['agents'] and runs_tot['dead']:
        why = ', '.join('%d %s' % (n, WHY_WORDS.get(k, k)) for k, n in
                        sorted(runs_tot['why'].items(), key=lambda x: -x[1]))
        out.append({'key': 'workflows', 'title': 'Workflows, in numbers.',
                    'why': '%d%% of workflow agents died (%d of %d: %s).' % (
                        int(100.0 * runs_tot['dead'] / runs_tot['agents'] + 0.5), runs_tot['dead'],
                        runs_tot['agents'], why),
                    'action': None, 'items': []})
    return out


WHY_WORDS = {'signed_out': 'signed out', 'offline': 'offline', 'flagged': 'flagged', 'stopped': 'stopped',
             'unknown': 'no reason recorded', 'limit_model': 'model limit', 'limit_5hour': '5-hour limit',
             'limit_weekly': 'weekly limit', 'limit_credits': 'credits', 'limit_other': 'usage or rate limit',
             'overloaded': 'overloaded',
             'too_long': 'too long', 'blocked_403': '403', 'restart': 'restart', 'empty': 'no reply',
             'error_other': 'other error'}


def switches_view(hist, panels):
    """Switches of the last DAYS days, each with the panels VS Code checked again right after it."""
    out = []
    times = sorted(panels)
    for s in hist['switches']:
        n = sum(1 for p in times if 0 <= p - s['t'] <= PANEL_NEAR)
        out.append({'at': iso(s['t']), 'to': s['to'], 'by': s['by'], 'slow': n})
    return out


def build(now=None, budget=PASS_SECONDS, lines=None, auto_on=None, awake_on=None):
    """GET /api/keep's answer, made now. auto_on: automatic switching is on (None: History's last word on it);
    awake_on: Keep the Mac awake is on (None: Switchboard's settings). Never raises: a part that can't be read is left
    out with a `problems` line."""
    now = time.time() if now is None else now
    t0 = time.monotonic()
    problems = []

    failed = set()              # the parts that could not be read

    def part(what, fn, default, key=None):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - that part is left out
            try:
                core.log('keep running: %s could not be read: %r' % (what, e))
            except Exception:  # noqa: BLE001
                pass
            if what + ' could not be read.' not in problems:
                problems.append('%s could not be read.' % what)
            failed.add(key or what)
            return default

    no_tot = {'agents': 0, 'dead': 0, 'why': {}, 'never_rerun': 0}
    no_counts = {'held': 0, 'resumed': 0, 'gave_up': 0, 'restarts': 0, 'continued': 0}
    scanned = part('Your chat history', lambda: scan(now, budget), {'files': [], 'runs': {}, 'left': 0, 'chats': {},
                                                                     'unread': 0, 'behind': 0, 'total': 0},
                   'chats')
    if scanned.get('left'):
        problems.append('Part of your chat history is still being read (%s to go).' % plural(scanned['left'], 'file'))
    has_switch, status = running_status()
    room_in = room_in_build()
    on = bool(status and status.get('on')) if has_switch else None
    plan = part('Keep running’s plan', lambda: _plan(now), None)
    mac = part('The Mac’s own records', lambda: mac_facts(now), {'spans': [], 'wakes': [], 'sessions': None})
    problems.extend(p for p in mac.get('problems') or [] if isinstance(p, str))
    hist = part('History', lambda: history_facts(now, lines), {'switches': [], 'every_out': [], 'auto_off': [],
                                                                'auto_on': None})
    claude = part('Claude Code’s version', claude_facts, {})
    panels, panels_left = part('VS Code’s log', lambda: panel_lines(now, t0 + budget), ([], 0))
    stops = part('Stops in chats', lambda: chat_stops(scanned, now), [], 'chats')
    titles = part('Chat titles', lambda: dict((f['session'], (f['data'].get('title') or '')[:TITLE_MAX])
                                              for f in scanned['files']
                                              if f['kind'] == 'chat' and f['data'].get('title')), {})
    events = part('Keep running’s record', lambda: read_events(now), [], 'chats')
    stops, counts = part('Keep running’s record', lambda: merge_plugin(list(stops), events, titles, now),
                         (stops, dict(no_counts)), 'chats')
    runs, runs_tot = part('Workflow runs', lambda: runs_view(scanned, now), ([], dict(no_tot)), 'chats')
    waiting, cut, quiet = part('Open chats', lambda: chats_now(scanned, mac, now), ([], [], []))
    auto_now = bool(auto_on) if auto_on is not None else hist.get('auto_on') is not False
    if awake_on is None:
        settings = part('Switchboard’s settings', core.settings, {})
        awake_on = not isinstance(settings, dict) or settings.get('awake') is not False
    # While some chat files have never been read, or a part read from the chats failed, no total is given: the
    # counts would be short, and "nothing stopped" would not be measured.
    reading = bool(scanned.get('unread'))
    broken = 'chats' in failed
    if reading or broken:
        # No counts from the chats at all (stops, agents, runs, open chats); what does not come from the chats
        # (sleep, History, Claude Code itself) is shown.
        stops, runs, waiting, cut, quiet = [], [], [], [], []
        runs_tot = dict(no_tot)
    if not has_switch:
        plan = None             # no Keep running in this build: its plan's words are not this build's to say
    words = plan.get('words') if plan and isinstance(plan.get('words'), str) else None
    stay_on = part('Switchboard’s settings', stay_signed_in, None)
    causes = part('What stopped your work', lambda: _handled_rows(stops, runs_tot, runs, mac, hist, on, has_switch,
                                                                   plan, auto_now, awake_on, now, room_in, stay_on),
                  [])
    steps = part('The steps', lambda: steps_for(on, has_switch, stops, runs, runs_tot, waiting, cut, quiet, mac,
                                                claude, awake_on, auto_now, now, hist), [])

    def the_week():
        if broken:
            return {'d7': None, 'd14': None, 'reading': None, 'sentence': 'Your chat history could not be read.'}
        if reading:
            total = scanned.get('total') or 0
            return {'d7': None, 'd14': None, 'reading': {'done': total - scanned['unread'], 'of': total},
                    'sentence': reading_sentence(total - scanned['unread'], total)}
        d7, d14 = _window(stops, runs, now, 7), _window(stops, runs, now, DAYS)
        return {'d7': d7, 'd14': d14, 'reading': None, 'sentence': sentence(d7, scanned.get('behind') or 0)}
    week = part('The week', the_week, {'d7': None, 'd14': None, 'reading': None,
                                       'sentence': 'Your chat history could not be read.'})

    # Chats cut off are the step 'cut' (counted exactly there, last 3 days), never a row of the 14-day list.
    if status and status.get('problems'):
        problems.extend(p for p in status['problems'] if isinstance(p, str))
    return {
        'v': VERSION, 'at': now,
        'now': {'on': on, 'switch': has_switch, 'words': words, 'plan_at': iso(plan['at']) if plan else None,
                'held': counts.get('held', 0), 'resumed': counts.get('resumed', 0),
                'gave_up': counts.get('gave_up', 0)},
        'week': week,
        'causes': causes,
        'waiting': waiting,
        'cut_off': cut,
        'runs': [r for r in runs if r['dead']][:20],
        # a count of re-checked panels only when every log was read (else it could be short)
        'switches': [] if panels_left else [s for s in part('Switches', lambda: switches_view(hist, panels), [])
                                            if s['slow']][-10:],
        'gap_minutes': None,        # not measured exactly: not given
        'mac': {'on_battery': mac.get('on_battery'), 'charge': mac.get('charge'), 'swap': mac.get('swap'),
                'programs': mac.get('programs'), 'quiet': quiet},
        'claude': {'terminal': '.'.join(map(str, claude['terminal'])) if claude.get('terminal') else None,
                   'terminal_path': _home_path(claude.get('terminal_path')),
                   'newest': '.'.join(map(str, claude['newest'])) if claude.get('newest') else None,
                   'api_key': bool(claude.get('api_key')), 'api_kinds': list(claude.get('api_kinds') or [])},
        'steps': steps,
        'problems': problems,
    }


def _plan(now):
    plan = read_json_file(os.path.join('running', 'room.json'))
    if plan is not None and (plan.get('v') != 1 or not _num(plan.get('at')) or not 0 <= now - plan['at'] <= 1200):
        plan = None
    return plan


def _readings(now):
    from . import insights
    return insights.read_readings(now - DAYS * DAY, now)


# ---------------------------------------------------------------- made-up numbers for the demo page

def sample(now=None):
    """A made-up answer with every kind of row, for the demo page and the stills. No real chat, account or person.
    Like build(), it follows what the build has: without switchboard.running, no switch, no "Turn on" step, and each
    row says what Switchboard does without it."""
    d = _sample(now)
    has_switch, _ = running_status()
    room_in = room_in_build()
    if not has_switch:                     # and no plan line: room.json is written only while Keep running is on
        d['now'] = dict(d['now'], switch=False, on=None, words=None, plan_at=None)
        d['steps'] = [x for x in d['steps'] if x['key'] != 'on']
    for c in d['causes']:                          # the same words build() gives (_doing, _handled_rows)
        if c['key'] == 'limit_model' or (c['step'] == 'on' and not has_switch):
            # the stay-signed-in setting as a fresh install has it (on)
            c['doing'], c['step'] = _doing(c['key'], None, has_switch, False, True, room_in, True)
            c['handled'] = None                     # 'yes' only when Keep running continued a stop of the row
        elif c['key'] == 'every_out' and room_in:
            c['doing'] = EVERY_OUT_ROOM
    return d


def _sample(now=None):
    now = time.time() if now is None else now
    at = lambda h: iso(now - h * 3600)  # noqa: E731
    row = lambda key, name, count, went, last, doing, handled, step, ex, note=None: {  # noqa: E731
        'key': key, 'name': name, 'note': note, 'count': count, 'went_on_min': went,
        'went_on_n': 1 if went is not None else None, 'last': at(last),
        'doing': doing, 'handled': handled, 'step': step, 'continued': 1, 'typed': max(0, count - 2), 'never': 1,
        'recent': 0,
        'examples': [{'at': at(last + i * 5), 'title': t, 'helper': False} for i, t in enumerate(ex)]}
    return {
        'v': VERSION, 'at': now, 'sample': True,
        'now': {'on': False, 'switch': True, 'plan_at': at(0.1), 'held': 0, 'resumed': 0,
                'gave_up': 0,
                'words': 'Fable is used up on maya.reyes@mail.example until Mon 1:00 PM. Chats run on Opus 5.5 until '
                         'Switchboard moves to maya@fernhill.example.'},
        'week': {'d7': {'stops': 31, 'chats': 19, 'helpers': 12, 'agents_dead': 6, 'itself': 4, 'plugin': 0,
                        'continued': 4, 'typed': 18, 'never': 9, 'recent': 0},
                 'd14': {'stops': 79, 'chats': 42, 'helpers': 37, 'agents_dead': 32, 'itself': 7, 'plugin': 0,
                         'continued': 7, 'typed': 30, 'never': 42, 'recent': 0},
                 'reading': None,
                 'sentence': 'Last 7 days: 31 early stops in chats and their helpers, and 6 workflow agents died. Of '
                             'the stops, 4 went on by themselves, 18 after you typed, 9 never went on.'},
        'causes': [
            row('limit_model', 'Fable used up on the account in use', 13, 95, 1, 'A request stopped by the limit ends '
                'the turn.', None, 'on', ['Release notes', 'Page redesign', 'Billing export']),
            row('signed_out', 'Signed out', 22, 41, 60, 'Switchboard puts back a sign-in that went missing, or moves '
                'Claude Code to a signed-in account.', None, 'on', ['Search index', 'Weekly report'],
                '22 of 22 within 5 minutes after a switch'),
            row('offline', 'Offline', 27, 12, 130, '', None, 'on', ['Data import'],
                '5 of 27 within 3 minutes after a wake'),
            row('lid', 'Lid shut on battery', 16, None, 100, 'Keep the Mac awake is on in Switchboard’s settings.',
                None, 'lid', ['Slept with the lid shut']),
            row('every_out', 'Every account out', 1, None, 20, 'Switchboard says every account is out when the asked '
                'model is used up.', None, None, ['Said every account is out']),
            row('agents', 'Agents that died in a workflow', 6, None, 26, '', None, 'runs',
                ['build-and-test (Page redesign)']),
            row('too_long', 'Chat too long for the model', 1, 7, 190, '', None, None, ['Billing export']),
            row('flagged', 'Flagged by the model’s safeguards', 2, None, 200, 'Claude’s safeguards flagged the '
                'request.', None, None, ['Lab notes']),
        ],
        'waiting': [{'title': 'Weekly report', 'session': '0' * 8, 'since': at(1.5), 't': now - 5400,
                     'what': 'a question', 'minutes': 90}],
        'cut_off': [{'title': t, 'session': str(i) * 8, 'since': at(0.9), 't': now - 3240}
                    for i, t in enumerate(('Release notes', 'Page redesign', 'Data import'))],
        'runs': [{'name': 'build-and-test', 'chat': 'Page redesign', 'session': '1' * 8, 'at': at(26), 't': now - 93600,
                  'agents': 3, 'returned': 0, 'dead': 3, 'why': {'signed_out': 3}, 'said_completed': True,
                  'later_run': False, 'never_rerun': 3, 'dead_t': [now - 93000] * 3,
                  'labels': ['builder', 'tests', 'review'], 'models': ['opus', 'sonnet']}],
        'switches': [{'at': at(9), 'to': 'maya@fernhill.example', 'by': 'auto', 'slow': 27}],
        'gap_minutes': None,
        'mac': {'on_battery': True, 'charge': 64, 'swap': {'total_mb': 19456.0, 'used_mb': 18032.0}, 'programs': 27,
                'quiet': [{'title': 'Old experiment', 'session': '2' * 8, 'since': at(80), 't': now - 288000,
                           'days': 3}]},
        'claude': {'terminal': '2.1.309', 'terminal_path': '~/.npm-global/bin/claude', 'newest': '2.1.332',
                   'api_key': True, 'api_kinds': ['key']},
        'steps': [
            {'key': 'on', 'title': 'Turn on Keep running',
             'why': '39 early stops in the last 14 days went on only after you typed, or never. ' + ON_WORDS,
             'action': {'kind': 'press', 'post': '/api/running', 'body': {'on': True}, 'label': 'Turn on'},
             'items': []},
            {'key': 'cut', 'title': '3 chats were working at %s and have not continued.' % clock(now - 3240, now),
             'why': 'Open each and type continue.', 'action': None,
             'items': [{'title': t, 'at': at(0.9)} for t in ('Release notes', 'Page redesign', 'Data import')]},
            {'key': 'runs', 'title': '1 workflow run had agents that died, and no later run of the same workflow '
                                     'with agents started in that chat.',
             'why': 'Ask each chat to resume its run.', 'action': None,
             'items': [{'title': 'build-and-test in Page redesign', 'at': at(26),
                        'note': '3 of 3 agents died (3 signed out); the run still said completed'}]},
            {'key': 'waiting', 'title': '1 chat is waiting for you.', 'why': 'The longest first.', 'action': None,
             'items': [{'title': 'Weekly report', 'at': at(1.5), 'note': 'Waiting 90 minutes for a question'}]},
            {'key': 'lid', 'title': 'Plug in before you close the lid.',
             'why': 'The Mac slept 16 times on battery with the lid shut (74 hours asleep in all).', 'action': None,
             'items': []},
            {'key': 'memory', 'title': 'Close the chats you are done with.',
             'why': '17.6 GB of swap in use (19 GB allocated). 1 open chat has been quiet for 2 days or more.',
             'action': None,
             'items': [{'title': 'Old experiment', 'at': at(80), 'note': 'Quiet 3 days'}]},
            {'key': 'terminal', 'title': 'The claude program at ~/.npm-global/bin/claude is older than the VS Code '
                                         'extension.',
             'why': 'It is Claude Code 2.1.309. The extension is 2.1.332.', 'action': None,
             'items': []},
            {'key': 'api_key', 'title': 'Claude Code’s settings hold an API key (ANTHROPIC_API_KEY in env).',
             'why': 'The value is not read into this page.', 'action': None, 'items': []},
            {'key': 'workflows', 'title': 'Workflows, in numbers.',
             'why': '3% of workflow agents died (32 of 1,008: 16 signed out, 15 offline, 1 flagged).', 'action': None,
             'items': []},
        ],
        'problems': [],
    }
