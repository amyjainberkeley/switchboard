"""One task the person just typed in one app, run by the other app's own non-interactive mode, its answer brought back.

A helper inside the app the person is typing in runs command(): this module's main(), with --to, --kind, --here and
maybe --area, and the task on standard input. It prints the other app's answer, framed as data, or one plain line
saying why nothing was handed, and exits 0 (2 for words it does not take, 1 when it could not run at all).

This is the one place Switchboard starts an app without a person at a terminal, so it starts nothing unless all of
these hold at that moment, in this order:
1. One hop: SWITCHBOARD_HAND is not set (every app this starts gets it, so a handed task is never handed on).
2. The rule, asked again: the plan is usable, its hand switch (hand.on) is on, and resolve.resolve() with where
   "helper" answers do "hand" to that same app.
3. The app is not cooling down: health() reads hands.jsonl and says an app is cooling down after at least COOL_MIN
   hand-overs in COOL_WINDOW_S with half or more failing by the app's own fault (fault_of). A cooling app is refused
   and the work stays where it was typed; nothing ever falls to another choice here.
4. A person is there: stamped(folder) holds a stamp younger than STAMP_FRESH_S, written by stamp() when the person
   sends a prompt in that folder (the adapters call stamp(); nothing here writes one).
5. Not the home folder or above it, and one hand-over at a time for a folder (a lock file; taken: it says so).
A plan in its trial mode (dry) records what would be done and starts nothing.

To Codex: `codex exec` as providers/codex.py runs a job (codex_facts.JOB_ARGS, an environment of
contract.JOB_ENV_NAMES only, a list of words and never a shell line), on Codex's own sign-in as it is set up there,
with the route's model and effort when the program's version takes them (catalog.start_args), read-only unless the
plan's hand.write is true (then workspace-write, never more). To Claude Code: `claude -p` with read-only tools only
(Read, Glob, Grep; no MCP server, no skill or command, nothing that asks a question), whatever hand.write says, the
route's model, no session kept.
Three guards keep a key or another sign-in from being billed in place of the plan: the environment carries no name of
contract.BILLING_ENV; --restricted leaves the person's and the folder's settings files out (their env blocks, key
helpers and hooks); and --settings blanks every Claude name of contract.BILLING_ENV for the run ("In non-interactive
mode (-p), the key is always used when present"). The run's init line says
which key source it used (apiKeySource); anything but "none", or no such line, counts the hand-over as failed and its
answer is not shown.

The answer is data: it is printed between FRAME and END, secrets taken out as providers/redact.py does, terminal
controls taken out, cut to ANSWER_MAX bytes with one line saying so. Kept: one line per hand-over in hands.jsonl (time,
to, how long, outcome, fault) and one decision file (CONTRACT section 3). Never a word of the task, of the answer or of
a path.
"""
import hashlib
import inspect
import json
import os
import re
import select
import signal
import subprocess
import sys
import time
from contextlib import contextmanager

from ..providers import catalog, codex_facts, contract, redact
from ..providers import run as _run
from ..providers import words as _pwords
from . import hand_words as W
from . import hookline
from . import resolve as _resolve

HAND_ENV = 'SWITCHBOARD_HAND'           # set to "1" for every app a hand-over starts
STAMPS = os.path.join('router', 'stamps')
STAMP_FRESH_S = 1800                    # a stamp is fresh for 30 minutes
STAMP_SKEW_S = 5                        # a stamp this far in the future still counts (two clocks read apart)
HANDS = os.path.join('router', 'hands.jsonl')
HANDS_MAX = 256 * 1024                  # the hand-over record is cut back to its newest half past this
DECIDED = os.path.join('router', 'decisions')
LOCKS = os.path.join('router', 'hand-locks')
ANSWERS = os.path.join('router', 'hand-out')     # Codex writes its last message here; read once, then removed
COOL_WINDOW_S = 600
COOL_MIN = 5
HAND_MAX_S = 570                        # the longest a hand-over runs, whatever the plan says: under the 600 s the
#                                         helper's shell tool gives its command, so this ends the started app first
TASK_MAX = 32 * 1024                    # bytes of a task read from standard input
ANSWER_MAX = 64 * 1024                  # bytes of an answer printed
STDERR_KEEP = 64 * 1024
LINE_MAX = 8 << 20                      # a line of Claude Code's output longer than this is read past
HELPER_TYPE = 'switchboard-hand'        # the Claude Code helper that runs this (router/agents/switchboard-hand.md)

# Claude Code's non-interactive run, every word read from its own --help (2.1.309): -p, --input-format, --output-format
# ("stream-json" needs --verbose), --no-session-persistence, --restricted ("ignores user, project and local settings
# files (managed settings and --settings still apply)", and "confines the file tools to the working directories"), so
# no env block, key helper or hook of the person's or the folder's settings is loaded; --tools ("the list of available
# tools from the built-in set"), --strict-mcp-config (with no --mcp-config: no MCP server), --disable-slash-commands
# ("Disable all skills and commands"), --permission-prompts none ("anything that would prompt is denied
# automatically"), --settings, which blanks every Claude name of contract.BILLING_ENV for the run.
CLAUDE_SINCE = '2.1.309'
CLAUDE_TOOLS = 'Read,Glob,Grep'
CLAUDE_BLANK_KEY = json.dumps({'env': {n: '' for n in contract.BILLING_ENV if n.startswith(('ANTHROPIC_', 'CLAUDE_'))}},
                              separators=(',', ':'))
CLAUDE_ARGS = ('-p', '--input-format', 'text', '--output-format', 'stream-json', '--verbose',
               '--no-session-persistence', '--restricted', '--tools', CLAUDE_TOOLS, '--strict-mcp-config',
               '--disable-slash-commands', '--permission-prompts', 'none', '--settings', CLAUDE_BLANK_KEY)
PLAN_SIGN_IN = 'none'                   # the init line's apiKeySource when the run is on the plan's own sign-in

# Codex runs on its own sign-in, a plan or a key, as the person set it up there (CODEX_HOME is passed on, so its own
# login file is read). The command names no login method: Codex's own docs say that when the sign-in in use does not
# match a forced one, "Codex logs the user out and exits", so a forced method could sign a person out. The environment
# still carries no name of contract.BILLING_ENV. A Codex older than CODEX_SINCE is not started.
CODEX_SINCE = '0.155.1'

# Work typed in another app handed to Claude Code (--to claude). Built and tested, but never tried on a real sign-in,
# so this version refuses it with one line before anything is read or started. Its tests set this true.
TO_CLAUDE = False

# Whose fault a failure is. The app's: it is missing, too old, signed out, at its limit, its service failed, it ran
# out of time, a signal ended it, or it ran on a key. The task's: a normal run that answered with an error, or with
# nothing. Only the app's failures count toward cooling down.
OUTCOMES_APP = ('missing', 'too_old', 'signed_out', 'limit', 'service', 'timed_out', 'signal', 'key_used',
                'no_key_source')
OUTCOMES_TASK = ('error', 'no_answer')
OUTCOMES = ('ok', 'stopped') + OUTCOMES_APP + OUTCOMES_TASK
# Read in what a failed run printed, in this order. An HTTP status in the text is read as such: 401 and 402 are a
# sign-in, 429 a limit, 5xx the service; any other error is the task's.
_SIGNED_OUT_RE = re.compile(r'(?i)not logged in|not signed in|log ?in again|sign ?in again|run /login|please log ?in|'
                            r'unauthori[sz]ed|invalid api key|authentication (failed|error)|\b40[12]\b')
_LIMIT_RE = re.compile(r'(?i)usage limit|rate limit|hit your [a-z0-9 .-]{0,24}limit|limit reached|\b429\b|quota')
_SERVICE_RE = re.compile(r'(?i)\b(500|502|503|504|529)\b|overloaded|service unavailable|internal server error')

_WORD_RE = re.compile(r'^[a-z][a-z0-9_-]{0,23}\Z')
_KEY_SOURCE_RE = re.compile(r'^[A-Za-z0-9_./ -]{1,40}\Z')
USAGE = ('usage: python3 -I -S -c "<the package on the path>; from switchboard.router import hand; '
         'raise SystemExit(hand.main())" -- --to AGENT --kind KIND [--area AREA] --here AGENT  '
         '(the task on standard input)')


class _Usage(ValueError):
    pass


# ---------------------------------------------------------------- small helpers

def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and abs(x) != float('inf')


def _data_dir(env=None):
    """The adapters' rule: $SWITCHBOARD_HOME, else ~/.switchboard."""
    return hookline.data_dir(env)


def _norm(folder) -> str:
    return os.path.realpath(os.path.abspath(folder))


def folder_key(folder) -> str:
    """The first 16 hex of the sha256 of the folder's real path: a stamp's and a lock's file name. The adapters that
    call stamp() hash the same real path."""
    return hashlib.sha256(_norm(folder).encode('utf-8')).hexdigest()[:16]


def _private_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def _write_whole(path, data: bytes):
    """data at path, whole or not at all (a temp file, fsync, rename), mode 0600."""
    _private_dir(os.path.dirname(path))
    tmp = os.path.join(os.path.dirname(path), '.%s.%d.tmp' % (os.path.basename(path), os.getpid()))
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


def _name(pid) -> str:
    return W.APP_NAMES.get(pid, W.APP_OTHER)


def _clock(t, now) -> str:
    """A time as a person reads it: '3:10 AM', with the day's short name when it is more than 20 hours away."""
    tm = time.localtime(t)
    text = '%d:%02d %s' % (tm.tm_hour % 12 or 12, tm.tm_min, 'AM' if tm.tm_hour < 12 else 'PM')
    return ('%s %s' % (time.strftime('%a', tm), text)) if abs(t - now) > 20 * 3600 else text


def _span(seconds) -> str:
    if seconds < 60:
        return W.SPAN_SECONDS.format(n=max(1, int(round(seconds))))
    n = int(round(seconds / 60.0))
    return W.SPAN_MINUTE if n == 1 else W.SPAN_MINUTES.format(n=n)


def _say(out, text):
    out.write(text + '\n')
    out.flush()


# ---------------------------------------------------------------- a person is there: the folder's stamp

def stamp_path(folder, data=None) -> str:
    return os.path.join(data or _data_dir(), STAMPS, folder_key(folder) + '.json')


def stamp(folder, now=None, data=None, env=None):
    """Note that the person just sent a prompt in folder: <data>/router/stamps/<folder_key>.json, {"t": now}, written
    whole, mode 0600. The adapters call this; the stamp holds a time and nothing else. Returns its path.

    Writes nothing and returns None when HAND_ENV is set in env (os.environ when None), even empty: the prompt then
    came from a hand-over, not a person, and an app a hand-over started runs the same prompt hooks. Every adapter that
    writes a stamp itself follows the same rule."""
    if HAND_ENV in (os.environ if env is None else env):
        return None
    now = time.time() if now is None else now
    path = stamp_path(folder, data)
    _write_whole(path, json.dumps({'t': now}).encode('utf-8'))
    return path


def stamped(folder, now=None, data=None) -> bool:
    """A stamp for folder younger than STAMP_FRESH_S is there. Never raises."""
    now = time.time() if now is None else now
    try:
        with open(stamp_path(folder, data), 'rb') as f:
            got = json.loads(f.read(4096).decode('utf-8'))
        t = got.get('t') if isinstance(got, dict) else None
        return _num(t) and -STAMP_SKEW_S <= now - t < STAMP_FRESH_S
    except (OSError, ValueError, RecursionError):
        return False


# ---------------------------------------------------------------- the record of hand-overs, and cooling down

def fault_of(outcome):
    """'agent' for a failure that is the app's own, 'task' for one that is the task's, None for ok or stopped."""
    return 'agent' if outcome in OUTCOMES_APP else 'task' if outcome in OUTCOMES_TASK else None


@contextmanager
def _flock(path):
    import fcntl
    _private_dir(os.path.dirname(path))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def note_hand(to, took, outcome, now, data=None):
    """One line in hands.jsonl: {"t", "to", "took", "outcome", "fault"}. Nothing of the task. The file is cut back to
    its newest half when it passes HANDS_MAX."""
    data = data or _data_dir()
    path = os.path.join(data, HANDS)
    line = json.dumps({'t': now, 'to': to, 'took': round(float(took or 0.0), 3), 'outcome': outcome,
                       'fault': fault_of(outcome)}, separators=(',', ':')).encode('utf-8') + b'\n'
    with _flock(path + '.lock'):
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, 'ab') as f:
            f.write(line)
            size = f.tell()
        if size > HANDS_MAX:
            with open(path, 'rb') as f:
                tail = f.read()[-HANDS_MAX // 2:]
            cut = tail.find(b'\n')
            _write_whole(path, tail[cut + 1:] if cut >= 0 else b'')


def _hands(data):
    try:
        with open(os.path.join(data, HANDS), 'rb') as f:
            raw = f.read()[-HANDS_MAX:]
    except OSError:
        return []
    out = []
    for line in raw.split(b'\n'):
        try:
            rec = json.loads(line.decode('utf-8'))
        except (ValueError, RecursionError):
            continue
        if isinstance(rec, dict) and _num(rec.get('t')) and isinstance(rec.get('to'), str):
            out.append(rec)
    return out


def health(agent, now=None, data=None) -> dict:
    """{"cooling", "n", "bad", "until"} for hand-overs to agent: cooling is true when, in the COOL_WINDOW_S before
    now, there were at least COOL_MIN of them and half or more failed by the app's own fault; until is when that
    stops being true as the older ones leave the window (None when not cooling). Never raises."""
    now = time.time() if now is None else now
    try:
        seen = sorted((r['t'], r.get('fault') == 'agent') for r in _hands(data or _data_dir())
                      if r['to'] == agent and now - COOL_WINDOW_S < r['t'] <= now)
    except Exception:  # noqa: BLE001 - a record that cannot be read cools nothing
        seen = []

    def holds(items):
        return len(items) >= COOL_MIN and 2 * sum(1 for _, bad in items if bad) >= len(items)

    if not holds(seen):
        return {'cooling': False, 'n': len(seen), 'bad': sum(1 for _, b in seen if b), 'until': None}
    until = None
    for t, _ in seen:
        end = t + COOL_WINDOW_S
        if not holds([s for s in seen if s[0] + COOL_WINDOW_S > end]):
            until = end
            break
    return {'cooling': True, 'n': len(seen), 'bad': sum(1 for _, b in seen if b), 'until': until}


def _decide(data, now, rec):
    """One decision file, <data>/router/decisions/<epoch ms>-<8 hex>.json, written whole (CONTRACT section 3)."""
    name = '%013d-%s.json' % (int(now * 1000), os.urandom(4).hex())
    _write_whole(os.path.join(data, DECIDED, name), json.dumps(rec, separators=(',', ':')).encode('utf-8'))


def _record(here, to, kind, area, answer, plan, did, reason, model, effort, now):
    return {'t': now, 'agent': here, 'where': 'helper', 'type': HELPER_TYPE, 'kind': kind, 'area': area,
            'did': did, 'from_model': None, 'model': model, 'effort': effort, 'to': to, 'reason': reason,
            'why': answer.get('why') if isinstance(answer.get('why'), str) else None,
            'passed': [list(p) for p in answer.get('passed') or ()],
            'plan': plan.get('made_at') if _num(plan.get('made_at')) else None}


# ---------------------------------------------------------------- the two command lines

def _start_words(pid, model, effort, version):
    """(words, model passed, effort passed): the route's model and effort as catalog.start_args gives them for this
    program's version, each only when the version takes it and the list knows it; the other is left out."""
    words, used = [], {'model': None, 'effort': None}
    for given, value in (('model', model), ('effort', effort)):
        if not isinstance(value, str):
            continue
        try:
            got = catalog.start_args(pid, value if given == 'model' else None, value if given == 'effort' else None,
                                     version)
        except Exception:  # noqa: BLE001 - a word the list does not know is not passed
            continue
        if got:
            words += got
            used[given] = value
    return words, used['model'], used['effort']


def codex_argv(path, version, folder, out_path, model=None, effort=None, write=False):
    """(argv, model passed, effort passed): `codex exec` as providers/codex.py runs a job, with -s workspace-write in
    place of read-only when write is true, then the start words before the last word ('-': the task on standard
    input). No login method is named: Codex runs on its own sign-in. ValueError for a start word a job never carries."""
    from ..providers.codex import CodexProvider
    words = [a.format(folder=folder, out=out_path) for a in codex_facts.JOB_ARGS]
    if write:
        words[words.index('read-only')] = 'workspace-write'
    extra, model, effort = _start_words('codex', model, effort, version)
    extra = list(CodexProvider._task_words(extra))
    return [path] + words[:-1] + extra + words[-1:], model, effort


def claude_argv(path, version, model=None, effort=None):
    """(argv, model passed, effort passed): Claude Code's non-interactive run with read-only tools (CLAUDE_ARGS) and
    the start words. ValueError for a start word that is not a plain model or effort word."""
    from ..providers import claude_facts
    extra, model, effort = _start_words('claude', model, effort, version)
    for flag, value in zip(extra[::2], extra[1::2]):
        if flag not in claude_facts.TASK_FLAGS or not claude_facts.TASK_VALUE_RE.match(value):
            raise ValueError('a start word a hand-over never carries')
    return [path] + list(CLAUDE_ARGS) + extra, model, effort


def codex_env(env) -> dict:
    """A job's environment (contract.JOB_ENV_NAMES only, no BILLING_ENV name) and HAND_ENV."""
    out = _run.child_env(env, job=True)
    out[HAND_ENV] = '1'
    return out


def claude_env(env) -> dict:
    """A job's environment, the person's own CLAUDE_CONFIG_DIR when set (it names their sign-in's folder; it holds no
    secret), and HAND_ENV. No BILLING_ENV name."""
    out = _run.child_env(env, job=True)
    if (env or {}).get('CLAUDE_CONFIG_DIR'):
        out['CLAUDE_CONFIG_DIR'] = env['CLAUDE_CONFIG_DIR']
    for name in contract.BILLING_ENV:
        out.pop(name, None)
    out[HAND_ENV] = '1'
    return out


def prompt_for(here, folder, task, write) -> str:
    head = (W.PROMPT_WRITE if write else W.PROMPT_READ).format(frm=_name(here), folder=folder)
    return head + '\n\n' + task


# ---------------------------------------------------------------- running Claude Code, reading its lines as they come

def _stop(signum, frame):
    raise KeyboardInterrupt


@contextmanager
def _stoppable():
    """SIGTERM and SIGHUP (the helper's own time limit, a closed window) stop a run as Ctrl-C does, so the started
    program, in its own session, never outlives this one."""
    old = {}
    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            old[sig] = signal.signal(sig, _stop)
        except (ValueError, OSError):
            pass
    try:
        yield
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)


def stream(argv, stdin, timeout, env, cwd, on_line):
    """Run argv (a list, never a shell) to its end or timeout, in its own session, stdin written and closed; each
    whole line of its output goes to on_line(bytes) as it comes and is not kept (a line past LINE_MAX is read past);
    the last STDERR_KEEP bytes of its error output are kept. -> contract.Ran with out '' . On the time limit, or any
    exception while it waits, its process group is ended (SIGTERM, then SIGKILL) first."""
    start = time.monotonic()
    try:
        proc = subprocess.Popen([str(a) for a in argv], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=dict(env), cwd=cwd, start_new_session=True,
                                close_fds=True)
    except OSError:
        return contract.Ran(code=None, took_s=time.monotonic() - start, missing=True)
    out_fd, err_fd = proc.stdout.fileno(), proc.stderr.fileno()
    data = memoryview(stdin.encode('utf-8'))
    writer = proc.stdin.fileno()
    os.set_blocking(writer, False)
    buf, err, skipping = bytearray(), bytearray(), False
    readers, how = [out_fd, err_fd], 'done'
    try:
        while readers or writer is not None:
            left = start + timeout - time.monotonic()
            if left <= 0:
                how = 'time'
                break
            r, w, _ = select.select(readers, [writer] if writer is not None else [], [], min(left, 0.25))
            if w:
                try:
                    data = data[os.write(writer, data[:1 << 16]):]
                except BlockingIOError:
                    pass
                except OSError:
                    data = data[:0]
                if not data:
                    proc.stdin.close()
                    writer = None
            for fd in r:
                try:
                    chunk = os.read(fd, 1 << 16)
                except OSError:
                    chunk = b''
                if not chunk:
                    readers.remove(fd)
                    continue
                if fd == err_fd:
                    err = (err + chunk)[-STDERR_KEEP:]
                    continue
                buf += chunk
                while True:
                    i = buf.find(b'\n')
                    if i < 0:
                        break
                    line = bytes(buf[:i])
                    del buf[:i + 1]
                    if not skipping:
                        on_line(line)
                    skipping = False
                if len(buf) > LINE_MAX:
                    buf.clear()
                    skipping = True
        if buf and not skipping and how == 'done':
            on_line(bytes(buf))
        if how == 'done':
            try:
                proc.wait(max(start + timeout - time.monotonic(), 0.0))
            except subprocess.TimeoutExpired:
                how = 'time'
        if how != 'done':
            _run._end(proc)
    except BaseException:
        _run._end(proc)
        _run._close_streams(proc)
        raise
    _run._close_streams(proc)
    took = time.monotonic() - start
    if how == 'time':
        return contract.Ran(code=None, err=err.decode('utf-8', 'replace'), took_s=took, timed_out=True)
    return contract.Ran(code=proc.returncode, err=err.decode('utf-8', 'replace'), took_s=took)


class _ClaudeLines:
    """What is kept of Claude Code's stream-json lines: the init line's apiKeySource, whether a rate-limit line said
    rejected, and the result line's text and error flag. Nothing else is held."""

    def __init__(self, keep_chars):
        self.source, self.rejected, self.result, self.keep = None, False, None, keep_chars

    def __call__(self, line):
        try:
            obj = json.loads(line.decode('utf-8', 'replace'))
        except (ValueError, RecursionError):
            return
        if not isinstance(obj, dict):
            return
        kind = obj.get('type')
        if kind == 'system' and obj.get('subtype') == 'init':
            src = obj.get('apiKeySource')
            self.source = src if isinstance(src, str) else '?'
        elif kind == 'rate_limit_event':
            inner = [v for v in obj.values() if isinstance(v, dict)]
            if any(d.get('status') == 'rejected' for d in [obj] + inner):
                self.rejected = True
        elif kind == 'result':
            text = obj.get('result')
            self.result = {'text': text[:self.keep] if isinstance(text, str) else '',
                           'error': obj.get('is_error') is True}


def _from_words(text):
    """The app's own failure read in what it printed ('signed_out', 'limit', 'service'), or None."""
    for outcome, rx in (('signed_out', _SIGNED_OUT_RE), ('limit', _LIMIT_RE), ('service', _SERVICE_RE)):
        if rx.search(text or ''):
            return outcome
    return None


# ---------------------------------------------------------------- the lines a person sees

def _why_not(answer, to, now) -> str:
    reason = answer.get('reason')
    if reason == 'no_plan':
        return W.NO_PLAN
    if reason == 'old_plan':
        return W.OLD_PLAN
    if reason == 'off':
        return W.OFF
    name = _name(to)
    codes = [c for a, c in answer.get('passed') or () if a == to]
    if 'hand_off' in codes:
        return W.HAND_OFF
    if 'out' in codes:
        back = answer.get('back_at')
        return W.NO_ROOM_UNTIL.format(to=name, when=_clock(back, now)) if _num(back) and back > now else \
            W.NO_ROOM.format(to=name)
    for code in codes:
        if code in W.PASSED:
            return W.PASSED[code].format(to=name)
    return W.NOT_ROUTED.format(to=name)


def _clean(text, env):
    """The other app's words made safe to print: secrets taken out (providers/redact.py, the person's own secret
    values among them), terminal controls taken out. -> (text, cut)."""
    cap = ANSWER_MAX
    cut = len(text) > 2 * cap
    text = text[:2 * cap]
    try:
        text, _ = redact.scrub(text, known=redact.env_secrets(env), deadline=time.monotonic() + redact.BUDGET_S)
    except Exception:  # noqa: BLE001 - an answer that cannot be checked for secrets is not shown
        return redact.TOO_LONG, False
    text = _pwords.safe(text)
    raw = text.encode('utf-8')
    if len(raw) > cap:
        text, cut = raw[:cap].decode('utf-8', 'ignore'), True
    return text, cut


def _last_words(text, env) -> str:
    lines = [ln for ln in (text or '').splitlines() if ln.strip()]
    if not lines:
        return ''
    try:
        words, _ = redact.scrub(lines[-1][-4000:], known=redact.env_secrets(env))
    except Exception:  # noqa: BLE001 - nothing of it is said when it cannot be checked
        return ''
    return W.LAST_WORDS.format(words=_pwords.one_line(words, 200))


def _failure_line(outcome, to, max_s, words, source):
    name = _name(to)
    return {'missing': W.MISSING.format(to=name), 'timed_out': W.TIMED_OUT.format(to=name, span=_span(max_s)),
            'signal': W.SIGNAL.format(to=name), 'signed_out': W.SIGNED_OUT.format(to=name),
            'limit': W.AT_LIMIT.format(to=name), 'service': W.SERVICE.format(to=name),
            'error': W.TASK_ERROR.format(to=name) + words, 'no_answer': W.NO_ANSWER.format(to=name),
            'key_used': W.KEY_USED.format(source=source), 'no_key_source': W.NO_KEY_SOURCE,
            'stopped': W.STOPPED}.get(outcome, W.MISSING.format(to=name))


# ---------------------------------------------------------------- main

def _parse(argv):
    args = list(argv)
    while args and args[0] in ('--', '-c'):
        args.pop(0)
    got = {}
    while args:
        flag = args.pop(0)
        if flag not in ('--to', '--kind', '--area', '--here') or not args or flag in got:
            raise _Usage(flag)
        got[flag] = args.pop(0)
    from .. import providers
    to, here, kind, area = got.get('--to'), got.get('--here'), got.get('--kind'), got.get('--area')
    if not (providers.known(to) and providers.known(here)) or to == here:
        raise _Usage('agents')
    if not (isinstance(kind, str) and _WORD_RE.match(kind)) or (area is not None and not _WORD_RE.match(area)):
        raise _Usage('kind')
    return {'to': to, 'here': here, 'kind': kind, 'area': area}


def _ask_rule(plan, kind, here, now, area):
    """(answer, area taken): resolve.resolve() with where "helper", and the area when this build's rule takes one.
    The area taken is None when the rule did not take it, so the record never names an area nothing routed by."""
    try:
        takes_area = 'area' in inspect.signature(_resolve.resolve).parameters
    except (TypeError, ValueError):
        takes_area = False
    if takes_area and area is not None:
        return _resolve.resolve(plan, kind, here, now, 'helper', area=area), area
    return _resolve.resolve(plan, kind, here, now, 'helper'), None


PLAN_AHEAD_S = 60          # a plan made further ahead than this is from the future: old_plan (CONTRACT section 1)
PLAN_GOOD_MAX_S = 3600     # a plan good for longer than this is no plan


def _out_of_bounds(plan, now):
    """The line for a plan outside the contract's two bounds (made in the future, or good for too long), else None.
    These are checked here as well as by the rule, so neither can let a hand-over start."""
    if not isinstance(plan, dict):
        return None
    made, good = plan.get('made_at'), plan.get('good_for_s')
    number = (int, float)
    if isinstance(made, number) and not isinstance(made, bool) and not made <= now + PLAN_AHEAD_S:   # NaN too
        return W.OLD_PLAN
    if isinstance(good, number) and not isinstance(good, bool) and not good <= PLAN_GOOD_MAX_S:     # inf, NaN too
        return W.NO_PLAN
    return None


def _read_task(stdin):
    raw = stdin.read(TASK_MAX + 1)
    if isinstance(raw, str):
        raw = raw.encode('utf-8')
    return raw


def main(argv=None, stdin=None, out=None, reach=None, now=None, folder=None) -> int:
    """The helper's command (see the module's opening lines). argv: the words after the program; stdin: the task
    (bytes); reach: providers.contract.Reach (run.default_reach() when None); folder: where the person is typing
    (the current folder when None)."""
    out = out if out is not None else sys.stdout
    try:
        args = _parse(sys.argv[1:] if argv is None else argv)
    except _Usage:
        _say(out, USAGE)
        return 2
    if args['to'] == 'claude' and not TO_CLAUDE:
        _say(out, W.TO_CLAUDE_NOT_IN)
        return 0
    try:
        return _main(args, stdin if stdin is not None else sys.stdin.buffer, out, reach, now, folder)
    except Exception as e:  # noqa: BLE001 - one line, never a traceback with a path in it
        _say(out, W.NO_RUN.format(why=type(e).__name__))
        return 1


def _main(args, stdin, out, reach, now, folder) -> int:
    to, here, kind, area = args['to'], args['here'], args['kind'], args['area']
    if reach is None:
        reach = _run.default_reach()
    now = reach.wall() if now is None else now
    if HAND_ENV in (reach.env or {}):          # set at all, even empty: this run came from a hand-over
        _say(out, W.ONE_HOP)
        return 0
    raw = _read_task(stdin)
    if len(raw) > TASK_MAX:
        _say(out, W.TASK_TOO_LONG.format(kb=TASK_MAX // 1024))
        return 0
    task = raw.decode('utf-8', 'replace').strip()
    if not task:
        _say(out, W.NO_TASK)
        return 0
    data = reach.data_dir
    plan = hookline.read_plan(os.path.join(data, 'router', 'plan.json'))
    out_of_bounds = _out_of_bounds(plan, now)
    if out_of_bounds:
        _say(out, out_of_bounds)
        return 0
    answer, area = _ask_rule(plan, kind, here, now, area)
    args = dict(args, area=area)
    hand_on = isinstance(plan, dict) and isinstance(plan.get('hand'), dict) and plan['hand'].get('on') is True
    if answer.get('do') != 'hand' or answer.get('to') != to or not hand_on:
        _say(out, _why_not(answer, to, now))
        return 0
    cool = health(to, now, data)
    if cool['cooling']:
        _say(out, W.COOLING.format(to=_name(to), bad=cool['bad'], n=cool['n'],
                                   when=_clock(cool['until'] or now + COOL_WINDOW_S, now)))
        return 0
    folder = _norm(folder or os.getcwd())
    if not stamped(folder, now, data):
        _say(out, W.NOBODY_HERE)
        return 0
    home = _norm(reach.home)
    if folder == os.sep or folder == home or home.startswith(os.path.join(folder, '')):
        _say(out, W.HOME_FOLDER)
        return 0
    model, effort = answer.get('model'), answer.get('effort')
    if plan.get('dry') is True:
        _decide(data, now, _record(here, to, kind, area, answer, plan, 'would', answer.get('reason'), model, effort,
                                   now))
        _say(out, W.DRY.format(to=_name(to), on=W.ON.format(model=model) if isinstance(model, str) else '',
                               at=W.AT.format(effort=effort) if isinstance(effort, str) else ''))
        return 0
    with _folder_lock(data, folder) as held:
        if not held:
            _say(out, W.BUSY)
            return 0
        return _hand(args, plan, answer, task, folder, reach, now, out)


@contextmanager
def _folder_lock(data, folder):
    """One hand-over at a time for a folder, across processes, without waiting: yields True when this one holds it."""
    import fcntl
    path = os.path.join(_private_dir(os.path.join(data, LOCKS)), folder_key(folder) + '.lock')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held = True
        except OSError:
            held = False
        yield held
    finally:
        os.close(fd)


def _max_s(plan) -> float:
    got = plan['hand'].get('max_s')
    return float(min(max(got, 1), HAND_MAX_S)) if _num(got) else float(HAND_MAX_S)


def _hand(args, plan, answer, task, folder, reach, now, out) -> int:
    to, here, kind, area = args['to'], args['here'], args['kind'], args['area']
    write = plan['hand'].get('write') is True and to != 'claude'
    max_s = _max_s(plan)
    started = reach.mono()
    got = _Got()
    try:
        with _stoppable():
            if to == 'codex':
                _to_codex(got, reach, folder, answer, prompt_for(here, folder, task, write), write, max_s)
            elif to == 'claude':
                _to_claude(got, reach, folder, answer, prompt_for(here, folder, task, False), max_s)
            # An app with no direction here stays 'missing': nothing is started for it.
    except KeyboardInterrupt:
        got.outcome = 'stopped'
    took = reach.mono() - started
    data = reach.data_dir
    try:
        note_hand(to, took, got.outcome, now, data)
    except OSError:
        pass
    ok = got.outcome == 'ok'
    try:
        _decide(data, now, _record(here, to, kind, area, answer, plan, 'hand' if ok else 'none',
                                   answer.get('reason') if ok else 'hand_' + got.outcome, got.model, got.effort,
                                   now))
    except OSError:
        pass
    if not ok:
        framed = got.outcome == 'error' and got.words and not got.line    # the line holds the app's own words
        if framed:
            _say(out, W.FRAME.format(to=_name(to)))
        _say(out, got.line or _failure_line(got.outcome, to, max_s, got.words, got.source))
        if framed:
            _say(out, W.END.format(to=_name(to)))
        return 0
    name = _name(to)
    clean, cut = _clean(got.text, reach.env)
    _say(out, W.FRAME.format(to=name))
    if to == 'claude':
        _say(out, W.CLAUDE_NEVER_WRITES if plan['hand'].get('write') is True else W.READ_ONLY.format(to=name))
    else:
        _say(out, W.WRITE.format(to=name) if write else W.READ_ONLY.format(to=name))
    _say(out, '')
    _say(out, clean.rstrip('\n'))
    if cut:
        _say(out, '')
        _say(out, W.CUT.format(to=name, kb=ANSWER_MAX // 1024))
    _say(out, W.END.format(to=name))
    return 0


class _Got:
    """How one run ended: outcome (OUTCOMES), the answer's text, the app's last words on a failure, the key source
    Claude Code named, the model and effort really passed, and a sentence of the provider's own (line) when it gave
    one."""

    def __init__(self):
        self.outcome, self.text, self.words, self.source = 'missing', '', '', None
        self.model = self.effort = self.line = None

    def end(self, outcome, text='', words=''):
        self.outcome, self.text, self.words = outcome, text, words


def _sweep(folder, before):
    """Remove the answer files in folder last changed before `before`: left by a run that was killed (SIGKILL skips
    the removal after a run). A newer file may be another hand-over's, still running, and is left."""
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        path = os.path.join(folder, name)
        try:
            if os.lstat(path).st_mtime < before:
                os.unlink(path)
        except OSError:
            pass


def _to_codex(got, reach, folder, answer, prompt, write, max_s):
    from ..providers.codex import CodexProvider
    from ..providers.contract import ProviderError
    try:
        program = CodexProvider(reach)._runnable()
    except ProviderError as e:
        got.line = _pwords.one_line(str(e), 400) or None
        got.end('too_old' if e.kind == 'too_old' else 'missing')
        return
    if not program.version or _run.version_key(program.version) < _run.version_key(CODEX_SINCE):
        got.line = W.CODEX_TOO_OLD.format(version=program.version or '?', since=CODEX_SINCE)
        got.end('too_old')
        return
    folder_out = _private_dir(os.path.join(reach.data_dir, ANSWERS))
    _sweep(folder_out, time.time() - HAND_MAX_S - 60)
    out_path = os.path.join(folder_out, os.urandom(8).hex() + '.txt')
    argv, got.model, got.effort = codex_argv(program.path, program.version, folder, out_path, answer.get('model'),
                                             answer.get('effort'), write)
    try:
        ran = reach.run(argv, stdin=prompt, timeout=max_s, env=codex_env(reach.env), cwd=folder)
        try:
            with open(out_path, 'rb') as f:
                text = f.read(4 * ANSWER_MAX).decode('utf-8', 'replace')
        except OSError:
            text = ''
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass
    if ran.missing:
        got.end('missing')
    elif ran.timed_out:
        got.end('timed_out')
    elif ran.code is not None and ran.code < 0:
        got.end('signal')
    elif ran.code == 0:
        got.end('ok' if text.strip() else 'no_answer', text)
    else:
        said = (ran.err or '')[-4000:] + '\n' + text[-4000:]
        got.end(_from_words(said) or 'error', '', _last_words(ran.err or text, reach.env))


def _to_claude(got, reach, folder, answer, prompt, max_s):
    from ..providers.claude import ClaudeProvider
    from ..providers.contract import ProviderError
    try:
        program = ClaudeProvider(reach)._chosen()
    except ProviderError as e:
        got.line = _pwords.one_line(str(e), 400) or None
        got.end('missing')
        return
    if not program.version:
        got.end('missing')
        return
    if _run.version_key(program.version) < _run.version_key(CLAUDE_SINCE):
        got.line = W.CLAUDE_TOO_OLD.format(version=program.version, since=CLAUDE_SINCE)
        got.end('too_old')
        return
    argv, got.model, got.effort = claude_argv(program.path, program.version, answer.get('model'),
                                              answer.get('effort'))
    seen = _ClaudeLines(2 * ANSWER_MAX + 1)
    ran = stream(argv, prompt, max_s, claude_env(reach.env), folder, seen)
    src, res = seen.source, seen.result
    text = res['text'] if res else ''
    if ran.missing:
        got.end('missing')
    elif ran.timed_out:
        got.end('timed_out')
    elif ran.code is not None and ran.code < 0:
        got.end('signal')
    elif src is not None and src != PLAN_SIGN_IN:
        got.source = src if _KEY_SOURCE_RE.match(src) else '?'
        got.end('key_used')
    elif res is None or res['error'] or ran.code != 0:
        said = text[-4000:] + '\n' + (ran.err or '')[-4000:]
        if seen.rejected:
            got.end('limit')
        elif _from_words(said):
            got.end(_from_words(said))
        elif res is None and ran.code == 0:
            got.end('no_answer')
        else:
            got.end('error', '', _last_words(text or ran.err, reach.env))
    elif src is None:
        got.end('no_key_source')
    elif not text.strip():
        got.end('no_answer')
    else:
        got.end('ok', text)


# ---------------------------------------------------------------- the command a helper runs

def command(to, kind, here, area=None, python=None, top=None) -> str:
    """The one shell line a helper runs for a hand-over (the task goes on its standard input): this Python by its
    path, -I -S, the package's top folder put first on the path, and the words main() takes, each quoted."""
    import shlex
    python = python or sys.executable or '/usr/bin/python3'
    top = top or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    code = ('import sys; sys.path[:0] = [%s]; from switchboard.router import hand; raise SystemExit(hand.main())'
            % json.dumps(top))
    words = ['--to', to, '--kind', kind] + (['--area', area] if area else []) + ['--here', here]
    return '%s -I -S -c %s -- %s' % (shlex.quote(python), shlex.quote(code), ' '.join(shlex.quote(w) for w in words))



HELPER_BEGIN = 'BEGIN COMMAND'
HELPER_END = 'END COMMAND'


def helper_task(to, kind, here, task, area=None, python=None, top=None) -> str:
    """What the plugin gives the switchboard-hand helper: command() with the task on its standard input as a quoted
    here-document, between a line HELPER_BEGIN and a line HELPER_END, each followed by the here-document's end word.
    That word is random and drawn again until the task does not hold it anywhere, so no line of the task can end the
    here-document early and run as a command, and a task line "END COMMAND" does not read to the helper as the end."""
    while True:
        end = 'SWITCHBOARD_TASK_' + os.urandom(16).hex()
        if end not in task:
            break
    return '\n'.join(['%s %s' % (HELPER_BEGIN, end), "%s <<'%s'" % (command(to, kind, here, area, python, top), end),
                      task, end, '%s %s' % (HELPER_END, end)])

if __name__ == '__main__':
    raise SystemExit(main())
