"""Claude Code's "done" and "needs you" as Mac notifications: the hooks Switchboard adds to Claude Code's settings
when Notifications is turned on, and the small program they run.

Three hooks go into Claude Code's user settings (the file the status line uses), each running the same command:

  UserPromptSubmit  notes when a turn starts: its session, its prompt's id and the time, nothing else
  Stop              a turn ended: "Claude Code is done" when it took a minute or more (notify.from_hook)
  Notification      Claude Code waits on you: a permission prompt, an MCP server's question or link, a background
                    session's question ("Claude Code needs you")

What this relies on, from https://code.claude.com/docs/en/hooks (checked 2026-11-14):
- UserPromptSubmit: "Claude Code adds plain-text stdout as context that Claude can see", so the program prints
  nothing. Its input also carries the prompt's text (`prompt`), which is never kept.
- Exit code 2 "Prevents Claude from stopping" on Stop and "Blocks the prompt" on UserPromptSubmit, so the command
  always exits 0, whatever happens (it ends in `|| true`).
- `async`: "If true, runs in the background without blocking", and "Claude Code doesn't enforce `timeout` on it".
  Stop and Notification run that way. UserPromptSubmit does not, so a Stop always finds its turn's start; it has to
  be quick (see below). The program stops itself after HOOK_LIMIT seconds.
- `timeout`: "Seconds before canceling" (600 by default for a command hook, 30 on UserPromptSubmit).
- `agent_id` is set when a hook "fires inside a subagent" or an in-process teammate: those get nothing.
- `disableAllHooks` (in any settings file: managed, a project's local and shared files, the user's, the first that
  sets it deciding) and, in managed settings, `allowManagedHooksOnly` keep these hooks from running
  (https://code.claude.com/docs/en/settings-reference, checked 2026-11-14). status() reads the managed and user files,
  which apply to every project; projects_off() names the projects whose own files turn them off there.
- Hooks in settings files "are normally picked up automatically by the file watcher": no restart.

Speed: UserPromptSubmit runs before each prompt is sent, and Claude Code waits for it. That path imports only os,
sys, time, fcntl and statusline.py (its JSON reader uses json's C scanner: no json package, no re, no subprocess),
and waits at most LOCK_WAIT for the turns folder. Everything else here imports what it needs inside the function
that needs it. The command runs this file as a module of its own, with its folder at the end of the path:

    <python> -I -S -c '<put this folder on the path; notify_hooks.run()>' || true # switchboard notify

Turning them on and off is plan, then apply, as for the status line: install_plan() and uninstall_plan() say exactly
what changes and change nothing; apply() makes the change, with the one backup beside the file and a record of what
Switchboard added, and reads the file back. Only "hooks" changes. Every other byte stays as it was, and taking them
out leaves every hook of yours byte for byte as it was.
"""
import os
import sys
import time

try:
    from . import statusline as _sl
except ImportError:             # run by Claude Code's hook: this file's folder is on the path (command())
    import statusline as _sl

MARK = '# switchboard notify'
EVENTS = ('UserPromptSubmit', 'Stop', 'Notification')
MATCHER = 'permission_prompt|elicitation_dialog|elicitation_url_dialog|agent_needs_input'   # notify.NEEDS_YOU
TIMEOUT = 15                    # seconds, on each hook
HOOK_LIMIT = 25                 # the program ends itself after this long, whatever it is doing
TURN_KEEP = 86400               # a turn's start is forgotten after a day
PRUNE_EVERY = 3600              # the turns folder is looked through at most once an hour
LOCK_WAIT = 1.0                 # a prompt never waits longer than this for the turns folder
MAX_PROMPTS = 16                # starts kept per session
MAX_INPUT = 32 << 20            # bytes of hook input read at most
AHEAD = 5                       # a time further than this past the clock is not trusted
CHECK = 'SwitchboardCheck'      # the input verify() sends; Claude Code never sends this event
CHECKED = 'switchboard notify ok'
MANAGED = '/Library/Application Support/ClaudeCode'      # Claude Code's managed settings on a Mac
OFF = "Done and needs you are off: Claude Code's %s is on."
PlanError = _sl.PlanError


# ---------- the program the hooks run ----------

def turns_dir():
    return os.path.join(_sl.data_dir(), 'turns')


def notes_on():
    """False when Notifications is turned off in Switchboard (its settings.json says "notify": false): the hooks
    stay quiet even if they could not be taken out of Claude Code's settings."""
    return _read(os.path.join(_sl.data_dir(), 'settings.json')).get('notify') is not False


def _read(path):
    try:
        with open(path, 'rb') as f:
            rec = _sl._loads(f.read())
    except (OSError, ValueError, RecursionError):
        return {}
    return rec if isinstance(rec, dict) else {}


def _prompt(event):
    """The prompt's id, if it is a short plain one (Claude Code's are UUIDs), else ''."""
    p = event.get('prompt_id')
    return p if isinstance(p, str) and 0 < len(p) <= 128 and set(p) <= _sl.ID_CHARS else ''


def _fresh(rec, now):
    """A session's record with only the starts worth keeping: under a day old, not dated ahead, the newest few."""
    def ok(t):
        return t is not None and -AHEAD <= now - t <= TURN_KEEP
    raw = rec.get('prompts') if isinstance(rec.get('prompts'), dict) else {}
    prompts = dict((k, _sl._num(v)) for k, v in raw.items() if isinstance(k, str) and ok(_sl._num(v)))
    if len(prompts) > MAX_PROMPTS:
        prompts = dict(sorted(prompts.items(), key=lambda kv: kv[1])[-MAX_PROMPTS:])
    paused = _sl._num(rec.get('paused'))
    return {'v': 1, 'prompts': prompts, 'paused': paused if ok(paused) else None}


def _write(path, rec, now):
    """Atomically, readable only by you, dated by the record's own clock (so pruning reads the same time)."""
    tmp = '%s.%d.tmp' % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(_sl._dumps(rec).encode('utf-8'))
        os.utime(tmp, (now, now))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _lock(folder):
    """The turns folder's lock, or None when another hook holds it for over LOCK_WAIT: a prompt never waits longer."""
    import fcntl
    os.makedirs(folder, mode=0o700, exist_ok=True)
    fd = os.open(os.path.join(folder, '.lock'), os.O_WRONLY | os.O_CREAT, 0o600)
    end = time.time() + LOCK_WAIT
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            if time.time() >= end:
                os.close(fd)
                return None
            time.sleep(0.02)


def mark(event, now=None, folder=None):
    """UserPromptSubmit: note when this turn started, by session and prompt id. Never the prompt's text. True when
    noted."""
    now = time.time() if now is None else now
    name = _sl._session_file_name(event.get('session_id')) if isinstance(event, dict) else None
    if not name:
        return False
    folder = folder or turns_dir()
    fd = _lock(folder)
    if fd is None:
        return False
    try:
        path = os.path.join(folder, name)
        rec = _fresh(_read(path), now)
        rec['prompts'][_prompt(event)] = now
        _write(path, _fresh(rec, now), now)
    finally:
        os.close(fd)
    prune(folder, now)
    return True


def take(event, now=None, folder=None):
    """Stop: when this turn started, or None when that is not known.

    A Stop that continues (stop_hook_active) changes nothing. A Stop with background work in flight is a pause: its
    start is kept, and the Stop that ends the work counts from it, so a long job whose last turn is short still
    ends with a "done". A start is taken once: by the Stop with the same prompt id, else (no id on either side) the
    newest one."""
    now = time.time() if now is None else now
    name = _sl._session_file_name(event.get('session_id')) if isinstance(event, dict) else None
    if not name or event.get('stop_hook_active'):
        return None
    folder = folder or turns_dir()
    fd = _lock(folder)
    if fd is None:
        return None
    try:
        path = os.path.join(folder, name)
        rec = _fresh(_read(path), now)
        prompts, pid = rec['prompts'], _prompt(event)
        key = pid if pid in prompts else '' if '' in prompts else None
        if key is None and not pid and prompts:
            key = max(prompts, key=prompts.get)
        start = prompts.pop(key) if key is not None else None
        if event.get('background_tasks'):
            if rec['paused'] is None:
                rec['paused'] = start
            began = None
        else:
            began = rec['paused'] if rec['paused'] is not None else start
            rec['paused'] = None
        if rec['prompts'] or rec['paused'] is not None:
            _write(path, rec, now)
        else:
            try:
                os.unlink(path)
            except OSError:
                pass
        return began
    finally:
        os.close(fd)


def prune(folder, now=None):
    """Remove session records over a day old (or dated ahead of the clock). One look at most every hour."""
    now = time.time() if now is None else now
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
                    if not e.name.startswith('.') and e.is_file(follow_symlinks=False) \
                            and not -AHEAD <= now - e.stat(follow_symlinks=False).st_mtime <= TURN_KEEP:
                        os.unlink(e.path)
                        gone += 1
                except OSError:
                    pass
    except OSError:
        pass
    return gone


def _clock(env):
    """The time. Tests fix it with SWITCHBOARD_NOTIFY_NOW."""
    try:
        t = float(env.get('SWITCHBOARD_NOTIFY_NOW') or 'none')
        if t == t and abs(t) < 1e12:
            return t
    except ValueError:
        pass
    return time.time()


def _notify():
    try:
        from . import notify
    except ImportError:
        import notify
    return notify


def _show(event, started, now, env):
    """The note for a Stop or a Notification, shown once (notify.send remembers)."""
    nt = _notify()
    note = nt.from_hook(event, started=started, now=now)
    if note is None:
        return False
    run, osa = nt._run, env.get('SWITCHBOARD_OSASCRIPT')
    if osa and os.path.isabs(osa):          # tests: a stand-in that only writes down what it was given
        run = lambda cmd: nt._run([osa] + list(cmd[1:]))    # noqa: E731
    return nt.send(note, run=run, now=now)


def hook(stdin=None, env=None, now=None):
    """What Claude Code runs: one hook input on stdin. Prints nothing (but for verify()'s check) and returns 0,
    whatever happens."""
    env = os.environ if env is None else env
    try:
        raw = (stdin if stdin is not None else sys.stdin.buffer).read(MAX_INPUT)
        event = _sl._loads(raw) if raw and raw.strip() else None
        if not isinstance(event, dict):
            return 0
        name = event.get('hook_event_name')
        if name == CHECK:
            _notify()                       # the Stop path's module loads too
            sys.stdout.write(CHECKED + '\n')
            sys.stdout.flush()
            return 0
        if event.get('agent_id') is not None or not _sl._session_file_name(event.get('session_id')) or not notes_on():
            return 0                        # a helper's, one with no session, or Notifications is off in Switchboard
        now = _clock(env) if now is None else now
        if name == 'UserPromptSubmit':
            mark(event, now)
        elif name == 'Stop':
            _show(event, take(event, now), now, env)
        elif name == 'Notification':
            _show(event, None, now, env)
    except BaseException:
        pass
    return 0


def run():
    """The command's entry point: ends itself after HOOK_LIMIT seconds, then hook(). The alarm comes from the C
    module itself: the signal package imports enum, which costs ~60 ms on Apple's Python 3.9, on every prompt."""
    try:
        try:
            import _signal as sig
        except ImportError:
            import signal as sig
        sig.alarm(HOOK_LIMIT)
    except Exception:
        pass
    hook()


# ---------- turning them on and off ----------

def settings_path():
    """Claude Code's user settings file: $CLAUDE_CONFIG_DIR/settings.json, else ~/.claude/settings.json."""
    return _sl.settings_path()


def record_path():
    """What Switchboard added to each settings file, so taking the hooks out removes exactly that."""
    return os.path.join(_sl.data_dir(), _sl.NOTIFY_RECORD)


def command(python=None, folder=None):
    """The shell command each hook runs: this Python (its real path), this file's folder at the end of the path, -I
    so no PYTHON* setting and no file in the folder Claude Code runs in changes what loads, -S for no site-packages.
    A moved install does nothing (the file is looked for first); `|| true` keeps the exit 0 when the Python is gone.
    The trailing comment marks it as Switchboard's."""
    import json
    import shlex
    python = python or sys.executable or '/usr/bin/python3'
    folder = folder or os.path.dirname(os.path.abspath(__file__))
    code = ('import sys; D = %s; sys.path[:] = [p for p in sys.path if p] + [D]; import os; '
            'os.path.isfile(os.path.join(D, "notify_hooks.py")) and __import__("notify_hooks").run()'
            % json.dumps(folder))               # a JSON string is a Python string
    return '%s -I -S -c %s || true %s' % (shlex.quote(python), shlex.quote(code), MARK)


def wanted(command_line):
    """{event: the matcher group Switchboard adds for it}."""
    def handler(**more):
        return dict({'type': 'command', 'command': command_line, 'timeout': TIMEOUT}, **more)
    return {'UserPromptSubmit': {'hooks': [handler()]},
            'Stop': {'hooks': [handler(**{'async': True})]},
            'Notification': {'matcher': MATCHER, 'hooks': [handler(**{'async': True})]}}


def is_ours(handler):
    """True for a hook handler whose command ends with Switchboard's mark."""
    return isinstance(handler, dict) and isinstance(handler.get('command'), str) \
        and handler['command'].rstrip().endswith(MARK)


def off_line(key):
    return OFF % key


def precompile():
    """Bytecode for the three files the hooks load, so each run skips compiling them (statusline.precompile)."""
    folder = os.path.dirname(os.path.abspath(__file__))
    return [_sl.precompile(script=os.path.join(folder, name)) for name in ('notify_hooks.py', 'notify.py', 'statusline.py')]


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
        return "The notification hooks didn't run here: they took over %d s. Nothing was changed." % timeout
    except OSError as e:
        return "The notification hooks didn't run here (%s). Nothing was changed." % (e.strerror or e)
    if r.stdout.decode('utf-8', 'replace').strip() == CHECKED:
        return None
    try:
        python = shlex.split(command_line)[0]
    except (ValueError, IndexError):
        python = ''
    if not (os.path.isfile(python) and os.access(python, os.X_OK)):
        return ("The notification hooks didn't run here: the Python they run (%s) is not there. Nothing was changed."
                % python)
    said = [x for x in r.stderr.decode('utf-8', 'replace').splitlines() if x.strip()]
    return "The notification hooks didn't run here (%s). Nothing was changed." % (said[-1].strip()[:160] if said
                                                                                  else 'no answer')


def turn(on, python=None):
    """Put the hooks in (on) or take them out, for the page's Notifications switch and `switchboard notify`.

    On: plan, run the command once (verify), apply, read back. Raises PlanError with one plain line when a step
    can't be done; nothing was changed then. Off: never raises for Claude Code's file. What could not be taken out is
    said in 'left' (the hooks stay quiet anyway: they read Switchboard's own setting). Off also takes them out of
    every other settings file its record names: the page (no CLAUDE_CONFIG_DIR of its own) and a terminal that
    exports one each find their own file, and either can turn them off; 'also' lists those files. Returns
    {'changed', 'backup', 'message', 'off_by', 'left', 'also'}: off_by names the key that keeps Claude Code from
    running them, if one does."""
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
    """Take Switchboard's hooks out of one settings file: (what apply returned, or a no-change answer; why they are
    still there, or None). Never raises. A record for a file that holds none of them any more is dropped. A file that
    can't be read or understood is named only when Switchboard's record names it, or the record can't be read: the
    record is saved before the hooks ever go in, so Switchboard never put them in any other file."""
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
        why = "Switchboard couldn't take its notification hooks out of %s (%s)." % (path, e)
    return r, (why if status(path)['in'] is not False else None)      # said only while they are still there


def status(path=None):
    """{'in': True when all three hooks are in the settings file, False when not, None when the file can't be read
    or understood; 'works': they are in and the Python and folder they name are there; 'off_by': 'disableAllHooks'
    or 'allowManagedHooksOnly' when Claude Code runs no hook of yours, else None; 'path'}."""
    f = _settings(path or settings_path())
    off = _off_by(None if f.get('error') else f['obj'])
    if f.get('error'):
        return {'in': None, 'works': False, 'off_by': off, 'path': f['path']}
    found = _found(f['obj'])
    whole = all(found.get(e) for e in EVENTS)
    works = whole and all(_runs(c) for e in EVENTS for c in found[e])
    return {'in': whole, 'works': works, 'off_by': off, 'path': f['path']}


def facts(notes_on, path=None):
    """What the page's Notifications row shows. 'notify': the switch, on only when Switchboard's notes are on and
    its hooks are in Claude Code's settings and can run (your setting alone when that file can't be read);
    'notify_hooks': True, False or None (can't be read); 'hooks_off': the key that stops them, or None."""
    s = status(path)
    shown = bool(notes_on) if s['in'] is None else bool(notes_on and s['in'] and s['works'])
    return {'notify': shown, 'notify_hooks': s['in'], 'hooks_off': s['off_by']}


def _runs(cmd):
    """The Python a command names is there, and so is the folder with this file."""
    import json
    import re
    import shlex
    try:
        words = shlex.split(cmd)
        m = re.search(r'D = ("(?:[^"\\]|\\.)*")', words[4]) if len(words) > 4 and words[3] == '-c' else None
        folder = json.loads(m.group(1)) if m else None
    except ValueError:
        return False
    return bool(words) and os.path.isfile(words[0]) and os.access(words[0], os.X_OK) and isinstance(folder, str) \
        and os.path.isfile(os.path.join(folder, 'notify_hooks.py'))


def repair(python=None):
    """At the page's start: Switchboard's hooks whose Python is gone (an app that held it was removed, an update chose
    another) run on this one instead, through install_plan, verify and apply as ever (checked, backed up, recorded for
    the undo). Only a file where all three are in, each exactly the command Switchboard's record says it put there; a
    hook of yours is never touched. Looks at this process's settings file and every other one the record names. Never
    raises. Returns one log line for each file it changed, or could not change."""
    python = python or sys.executable
    if not python or _sl.python_gone(python) or not notes_on():     # turned off in Switchboard: never written again
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
                said.append('the notification hooks in %s run on %s now: the Python they named is gone'
                            % (target, python))
                continue
        except PlanError as e:
            why = str(e)
        except Exception as e:  # noqa: BLE001 - the hooks stay as they were; the page starts either way
            why = repr(e)
        said.append('the notification hooks in %s name a Python that is gone and were left as they are: %s'
                    % (target, why))
    return said


def projects_off(path=None):
    """The projects Claude Code knows (the "projects" of its .claude.json) where its own settings turn every hook off:
    .claude/settings.local.json, then .claude/settings.json, the first that sets disableAllHooks deciding, then the
    user's file. Empty when the managed settings decide it for every project. For `switchboard notify`, not the page:
    it reads one file per project."""
    import json
    try:
        from .packs import codemap
    except ImportError:                 # loaded as the hooks' own program: never asked this
        return []
    if any('disableAllHooks' in d for d in _managed()):
        return []
    f = _settings(path or settings_path())
    user = None if f.get('error') else f['obj'].get('disableAllHooks')
    cd = os.path.dirname(os.path.abspath(path or settings_path()))
    try:
        with open(codemap._claude_json(cd, os.path.expanduser('~')), 'rb') as fh:
            known = json.loads(fh.read().decode('utf-8')).get('projects')
    except (OSError, ValueError, AttributeError):
        return []
    out = []
    for p in sorted(known) if isinstance(known, dict) else []:
        if not isinstance(p, str) or not os.path.isabs(p):
            continue
        said = user
        for name in ('settings.json', 'settings.local.json'):     # the later one wins
            d = _read(os.path.join(p, '.claude', name))
            if 'disableAllHooks' in d:
                said = d['disableAllHooks']
        if said is True and user is not True:
            out.append(p)
    return out


def _off_by(user):
    """The key that keeps Claude Code from running hooks in the user settings, or None. Managed settings win: their
    disableAllHooks (true or false) decides; allowManagedHooksOnly there blocks every hook of yours. Reads the managed
    and user files only, which apply to every project; a project's own files are projects_off()'s."""
    managed = _managed()
    if any(d.get('disableAllHooks') is True for d in managed):
        return 'disableAllHooks'
    if any(d.get('allowManagedHooksOnly') is True for d in managed):
        return 'allowManagedHooksOnly'
    if any('disableAllHooks' in d for d in managed):
        return None
    return 'disableAllHooks' if isinstance(user, dict) and user.get('disableAllHooks') is True else None


def _managed():
    """The managed settings files that can be read (managed-settings.json, then managed-settings.d/*.json)."""
    import json
    paths = [os.path.join(MANAGED, 'managed-settings.json')]
    drop = os.path.join(MANAGED, 'managed-settings.d')
    try:
        paths += [os.path.join(drop, n) for n in sorted(os.listdir(drop)) if n.endswith('.json') and not n.startswith('.')]
    except OSError:
        pass
    out = []
    for p in paths:
        try:
            with open(p, 'rb') as f:
                doc = json.loads(f.read().decode('utf-8'))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            out.append(doc)
    return out


# ---------- reading the settings file ----------

INVALID = ("%s isn't valid JSON, so Switchboard leaves it alone. Claude Code reports it as a Settings Error too; fix "
           "that first.")


def _decoder():
    """json's decoder, strict: NaN and Infinity are not JSON (Claude Code reads settings as strict JSON)."""
    import json

    def no(name):
        raise ValueError('%s is not JSON' % name)
    return json.JSONDecoder(parse_constant=no)


def _skip(text, i):
    n = len(text)
    while i < n and text[i] in ' \t\n\r':
        i += 1
    return i


def _object_at(text, i, dec):
    """(members, end) of the object whose '{' is text[i]. Each member: key, k0 (its opening quote), k1 (past its
    closing quote), v0 and v1 (its value). end is just past the '}'."""
    from json.decoder import scanstring
    if text[i:i + 1] != '{':
        raise ValueError('not an object')
    j, members = _skip(text, i + 1), []
    if text[j:j + 1] == '}':
        return members, j + 1
    while True:
        if text[j:j + 1] != '"':
            raise ValueError('expected a key')
        key, k1 = scanstring(text, j + 1, True)
        c = _skip(text, k1)
        if text[c:c + 1] != ':':
            raise ValueError('expected :')
        v0 = _skip(text, c + 1)
        _, v1 = dec.raw_decode(text, v0)
        members.append({'key': key, 'k0': j, 'k1': k1, 'v0': v0, 'v1': v1})
        e = _skip(text, v1)
        if text[e:e + 1] == ',':
            j = _skip(text, e + 1)
            continue
        if text[e:e + 1] == '}':
            return members, e + 1
        raise ValueError('expected , or }')


def _array_at(text, i, dec):
    """(elements, end) of the array whose '[' is text[i]: each element is (start, end)."""
    if text[i:i + 1] != '[':
        raise ValueError('not an array')
    j, out = _skip(text, i + 1), []
    if text[j:j + 1] == ']':
        return out, j + 1
    while True:
        _, e1 = dec.raw_decode(text, j)
        out.append((j, e1))
        e = _skip(text, e1)
        if text[e:e + 1] == ',':
            j = _skip(text, e + 1)
            continue
        if text[e:e + 1] == ']':
            return out, e + 1
        raise ValueError('expected , or ]')


def _one(members, key, problem):
    found = [m for m in members if m['key'] == key]
    if len(found) > 1:
        raise _Shape(problem)
    return found[0] if found else None


class _Shape(Exception):
    """A settings file whose "hooks" Switchboard won't edit. The text says why."""


def _settings(path):
    """Read and understand a settings file. Never raises for a bad file: it says what is wrong instead."""
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
    dec = _decoder()
    try:
        obj = dec.decode(text)
    except (ValueError, RecursionError):
        return dict(f, error=INVALID % path)
    if not isinstance(obj, dict):
        return dict(f, error="%s doesn't hold a JSON object, so Switchboard leaves it alone." % path)
    try:
        members, _ = _object_at(text, _skip(text, 0), dec)
        h = _one(members, 'hooks', '%s has "hooks" twice. Remove one first.' % path)
        if h is not None:
            if not isinstance(obj['hooks'], dict):
                raise _Shape('In %s, "hooks" is not an object, so Switchboard leaves the file alone.' % path)
            inner, _ = _object_at(text, h['v0'], dec)
            for e in EVENTS:
                if _one(inner, e, '%s has "%s" twice in "hooks". Remove one first.' % (path, e)) is not None \
                        and not isinstance(obj['hooks'][e], list):
                    raise _Shape('In %s, "%s" is not a list, so Switchboard leaves the file alone.' % (path, e))
    except _Shape as e:
        return dict(f, error=str(e))
    except (ValueError, RecursionError):
        return dict(f, error=INVALID % path)
    f['obj'] = obj
    return f


def _found(obj, events=EVENTS):
    """{event: [the commands of Switchboard's handlers in it]} for the given events of a settings object."""
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


# ---------- editing it in place ----------
#
# Each edit touches only the bytes of what it adds or takes out, the way models.py edits "env": a member added to an
# object copies the spacing of the one before it, an element added to an array copies the separator of the ones
# before it, so taking it out again gives back the text exactly. After every edit the result is parsed and compared
# with the object it should be, so a wrong splice is never written.

def _nl(text):
    return '\r\n' if '\r\n' in text else '\n'


def _ws_before(text, pos):
    p = pos
    while p > 0 and text[p - 1] in ' \t\n\r':
        p -= 1
    return text[p:pos]


def _line_indent(text, pos):
    start = text.rfind('\n', 0, pos) + 1
    j = start
    while j < len(text) and text[j] in ' \t':
        j += 1
    return text[start:j]


def _unit(text, o, members):
    """The file's indent step, from the first top-level member on a line of its own; two spaces if none is."""
    base = _line_indent(text, o)
    for m in members:
        sep = _ws_before(text, m['k0'])
        if '\n' in sep:
            ind = sep[sep.rfind('\n') + 1:]
            return (ind[len(base):] if ind.startswith(base) else ind) or '  '
    return '  '


def _render(value, ind, unit, nl):
    """A value's text: on one line when ind is None, else spread over lines in the file's style, its first line
    starting where it is put and the rest indented from ind."""
    import json
    if ind is None:
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, indent=unit, ensure_ascii=False).replace('\n', nl + ind)


def _add_member(text, o, end, members, key, value, unit, nl, spread):
    """Text with "key": value added as the last member of the object at text[o:end] (end just past its '}')."""
    import json
    if members:
        last = members[-1]
        sep, colon = _ws_before(text, last['k0']), text[last['k1']:last['v0']]
        if '\n' in sep:
            ind = sep[sep.rfind('\n') + 1:]
            piece = ',' + nl + ind + json.dumps(key) + colon + _render(value, ind, unit, nl)
        else:
            piece = ',' + sep + json.dumps(key) + colon + _render(value, None, unit, nl)
        return text[:last['v1']] + piece + text[last['v1']:]
    close = end - 1
    if spread:
        base = _line_indent(text, o)
        ind = base + unit
        inner = nl + ind + json.dumps(key) + ': ' + _render(value, ind, unit, nl) + nl + base
    else:
        inner = json.dumps(key) + ': ' + _render(value, None, unit, nl)
    return text[:o + 1] + inner + text[close:]


def _add_element(text, a, end, elements, value, unit, nl, spread):
    """Text with value added as the last element of the array at text[a:end] (end just past its ']')."""
    if elements:
        if len(elements) > 1:
            sep = text[elements[-2][1]:elements[-1][0]]
        else:
            ws = text[a + 1:elements[0][0]]
            sep = ',' + ws if '\n' in ws else ', '
        ind = sep[sep.rfind('\n') + 1:] if '\n' in sep else None
        at = elements[-1][1]
        return text[:at] + sep + _render(value, ind, unit, nl) + text[at:]
    close = end - 1
    if spread:
        base = _line_indent(text, a)
        ind = base + unit
        return text[:a + 1] + nl + ind + _render(value, ind, unit, nl) + nl + base + text[close:]
    return text[:a + 1] + _render(value, None, unit, nl) + text[close:]


def _drop_member(text, o, end, members, i, inner=None):
    """Text with member i of the object at text[o:end] taken out, with its comma. inner: what was between the braces
    before Switchboard's member went into the empty object, put back when it is the last to go."""
    m = members[i]
    if len(members) == 1:
        return text[:o + 1] + (inner if isinstance(inner, str) and not inner.strip() else '') + text[end - 1:]
    if i == 0:
        return text[:m['k0']] + text[members[1]['k0']:]
    return text[:members[i - 1]['v1']] + text[m['v1']:]


def _drop_element(text, a, end, elements, i):
    """Text with element i of the array at text[a:end] taken out, with its comma."""
    if len(elements) == 1:
        return text[:a + 1] + text[end - 1:]
    if i == 0:
        return text[:elements[0][0]] + text[elements[1][0]:]
    return text[:elements[i - 1][1]] + text[elements[i][1]:]


class _Where(object):
    """Where "hooks" and its members are in a text, worked out afresh after every edit."""

    def __init__(self, text, dec):
        self.text, self.dec = text, dec
        self.o = _skip(text, 0)
        self.top, self.end = _object_at(text, self.o, dec)
        self.hooks = next((m for m in self.top if m['key'] == 'hooks'), None)
        self.inner, self.hend = _object_at(text, self.hooks['v0'], dec) if self.hooks else ([], None)

    def event(self, name):
        return next((m for m in self.inner if m['key'] == name), None)

    def spread(self, member):
        """True when a member sits on a line of its own (its value is then spread over lines too)."""
        return '\n' in _ws_before(self.text, member['k0'])


def _put_in(text, want, cmd, made):
    """Text with Switchboard's groups added (or its commands brought up to date) in place. made: what Switchboard
    created, to take out again ({'hooks': bool, 'events': [...]}), updated here."""
    import json
    dec, nl = _decoder(), _nl(text)
    w = _Where(text, dec)
    unit = _unit(text, w.o, w.top)
    if w.hooks is None:
        made['hooks'], made['events'] = True, list(EVENTS)
        return _add_member(text, w.o, w.end, w.top, 'hooks', dict((e, [want[e]]) for e in EVENTS), unit, nl, True)
    for e in EVENTS:
        w = _Where(text, dec)
        m = w.event(e)
        if m is None:
            text = _add_member(text, w.hooks['v0'], w.hend, w.inner, e, [want[e]], unit, nl, w.spread(w.hooks))
            if e not in made['events']:
                made['events'].append(e)
            continue
        groups, end = _array_at(text, m['v0'], dec)
        ours = []
        for g0, _ in groups:
            hs = _object_at(text, g0, dec)[0] if text[g0] == '{' else []
            hm = next((x for x in hs if x['key'] == 'hooks'), None)
            if hm is None or text[hm['v0']] != '[':
                continue
            for h0, _ in _array_at(text, hm['v0'], dec)[0]:
                value = dec.raw_decode(text, h0)[0]
                if is_ours(value) and value['command'] != cmd:
                    c = next(x for x in reversed(_object_at(text, h0, dec)[0]) if x['key'] == 'command')
                    ours.append((c['v0'], c['v1']))
                elif is_ours(value):
                    ours.append(None)
        if not ours:
            text = _add_element(text, m['v0'], end, groups, want[e], unit, nl, w.spread(m))
            continue
        for span in sorted((s for s in ours if s), reverse=True):        # from the end, so the spans stay right
            text = text[:span[0]] + json.dumps(cmd, ensure_ascii=False) + text[span[1]:]
    return text


def _take_out(text, made):
    """Text with every handler of Switchboard's taken out (a group left with none of yours goes with it), then the
    events and "hooks" Switchboard made, once they are empty."""
    dec = _decoder()
    while True:                             # one removal at a time, each worked out on the text as it is now
        w = _Where(text, dec)
        cut = None
        for m in w.inner:
            if text[m['v0']] != '[':
                continue
            groups, end = _array_at(text, m['v0'], dec)
            for gi in range(len(groups) - 1, -1, -1):
                g0 = groups[gi][0]
                if text[g0] != '{':
                    continue
                hm = next((x for x in _object_at(text, g0, dec)[0] if x['key'] == 'hooks'), None)
                if hm is None or text[hm['v0']] != '[':
                    continue
                handlers, hend = _array_at(text, hm['v0'], dec)
                mine = [i for i, (h0, _) in enumerate(handlers) if is_ours(dec.raw_decode(text, h0)[0])]
                if mine and len(mine) == len(handlers):
                    cut = _drop_element(text, m['v0'], end, groups, gi)
                elif mine:
                    cut = _drop_element(text, hm['v0'], hend, handlers, mine[-1])
                if cut is not None:
                    break
            if cut is not None:
                break
        if cut is None:
            break
        text = cut
    for e in reversed(EVENTS):
        w = _Where(text, dec)
        m = w.event(e)
        if m is not None and e in made.get('events', []) and text[m['v0']] == '[' and not _array_at(text, m['v0'], dec)[0]:
            text = _drop_member(text, w.hooks['v0'], w.hend, w.inner, w.inner.index(m), made.get('hooks_inner'))
    w = _Where(text, dec)
    if w.hooks is not None and made.get('hooks') and not w.inner:
        text = _drop_member(text, w.o, w.end, w.top, w.top.index(w.hooks), made.get('inner'))
    return text


def _with_ours(obj, want, cmd):
    """The settings object after _put_in, worked out on the object itself."""
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
        for h in mine:
            h['command'] = cmd
        if not mine:
            groups.append(want[e])
    return o


def _without_ours(obj, made):
    """The settings object after _take_out, worked out on the object itself."""
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

def install_plan(path=None, command_line=None):
    """The exact change that puts Switchboard's three hooks in. Changes nothing by itself."""
    cmd = command_line or command()
    want = wanted(cmd)
    f = _settings(path or settings_path())
    if f.get('error'):
        return _refused(f, 'install')
    found = _found(f['obj'])
    if all(found.get(e) for e in EVENTS) and all(c == cmd for e in EVENTS for c in found[e]):
        return _nothing(f, 'install', "Switchboard's notification hooks are already on.")
    rec = _records().get(f['target']) or {}
    made = {'hooks': bool(rec.get('made_hooks')), 'events': list(rec.get('made_events') or [])}
    record = {'installed': cmd}
    line = _sl._records().get(f['target']) or {}
    for k in _sl.SHARED + ('hooks_inner',):
        if k in rec:
            record[k] = rec[k]
        elif k in line:                     # Switchboard's status line made this file, found it empty, or found an
            record[k] = line[k]             # empty object: the last of the two to go puts it back as it was
    expect = _with_ours(f['obj'], want, cmd)
    if f['blank']:
        text = '{\n  "hooks": %s\n}\n' % _render(expect['hooks'], '  ', '  ', '\n')
        made = {'hooks': True, 'events': list(EVENTS)}
        record['file_was'] = f['text'] if f['exists'] else None      # None: there was no file at all
    else:
        try:
            w = _Where(f['text'], _decoder())
            if not w.top and 'inner' not in record:                  # an empty object: its own spacing comes back
                record['inner'] = f['text'][w.o + 1:w.end - 1]
            if w.hooks is not None and not w.inner and 'hooks_inner' not in record:
                record['hooks_inner'] = f['text'][w.hooks['v0'] + 1:w.hend - 1]
            text = _put_in(f['text'], want, cmd, made)
        except (ValueError, RecursionError, KeyError, StopIteration):
            text = None
    record.update(made_hooks=made['hooks'], made_events=made['events'])
    msg = "Switchboard's notification hooks are updated to run from here." if found else \
        "Switchboard's notification hooks go on."
    return _change(f, 'install', text, expect, record, msg)


def uninstall_plan(path=None):
    """The exact change that takes Switchboard's hooks out, and only them. Changes nothing by itself."""
    f = _settings(path or settings_path())
    if f.get('error'):
        return _refused(f, 'uninstall')
    if not _found(f['obj'], events=None):
        return _nothing(f, 'uninstall', "Switchboard's notification hooks aren't on.")
    rec = _records().get(f['target']) or {}
    made = {'hooks': bool(rec.get('made_hooks')), 'events': list(rec.get('made_events') or []),
            'inner': rec.get('inner'), 'hooks_inner': rec.get('hooks_inner')}
    try:
        text = _take_out(f['text'], made)
    except (ValueError, RecursionError, KeyError, StopIteration):
        text = None
    plan = _change(f, 'uninstall', text, _without_ours(f['obj'], made), None, "Switchboard's notification hooks go off.")
    if plan['ok'] and 'file_was' in rec and plan['expect'] == {}:
        # Switchboard made this file, or found it empty, and nothing else is in it now: back to how it was.
        was = rec['file_was']
        plan.update(delete=was is None, text=was if was is not None else '', change=True,
                    message="Switchboard's notification hooks go off, and the settings file it made goes too."
                    if was is None else plan['message'])
    return plan


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
            backup = _sl.backup_path(plan['path'])  # the same one copy the status line and the models setting keep
            _sl._write_atomic(backup, now_bytes, 0o600)
        if plan['action'] == 'install':
            _save_record(target, plan['record'])    # saved before the change, so it can't be lost
    except OSError as e:                            # a folder that can't be written, a full disk: before any change
        raise PlanError(_sl.cant_write(plan['path'], e))
    try:
        if _sl._digest(_sl._read_bytes(target)) != plan['digest']:    # the last look, right before the swap
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
                _put_records(before)                # nothing changed, so no record of a change either
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
            _drop_record(target)
        except OSError:                             # the hooks are out; a record left behind names only that file
            pass
    return {'changed': True, 'path': plan['path'], 'backup': backup, 'message': plan['message']}


def _change(f, action, text, expect, record, message):
    import difflib
    ok = text is not None
    if ok:
        try:
            ok = (_decoder().decode(text) if text.strip() else {}) == expect
        except ValueError:
            ok = False
    if not ok:                              # never write a file that reads back as anything but the plan
        return dict(_refused(f, action), message='Switchboard could not make this edit to %s without changing something '
                    'else; nothing was written.' % f['path'])
    diff = ''.join(difflib.unified_diff(f['text'].splitlines(True), text.splitlines(True), f['path'],
                                        f['path'] + ' (after)'))
    return {'ok': True, 'action': action, 'change': text != f['text'] or (action == 'install' and not f['exists']),
            'path': f['path'], 'target': f['target'], 'digest': f['digest'], 'text': text, 'expect': expect,
            'diff': diff, 'record': record, 'delete': False, 'message': message}


def _nothing(f, action, message):
    return {'ok': True, 'action': action, 'change': False, 'path': f['path'], 'target': f['target'],
            'digest': f['digest'], 'text': f['text'], 'expect': f['obj'], 'diff': '', 'record': None, 'delete': False,
            'message': message}


def _refused(f, action):
    return {'ok': False, 'action': action, 'change': False, 'path': f['path'], 'target': f['target'],
            'message': f.get('error')}


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
