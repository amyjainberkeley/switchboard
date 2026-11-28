"""Imported first by every test file, before anything from `switchboard`.

1. Points SWITCHBOARD_HOME at a throwaway folder when the run did not set one, and refuses to go
   on if switchboard was already imported without it. HOME and CLAUDE_CONFIG_DIR go next to it, so
   every path a module works out from the home folder is a throwaway one too.
2. Watches this test process (sys.addaudithook) for any write outside the temp folders: a file
   opened for writing, renamed, removed, made, chmod-ed, touched or copied anywhere else is
   refused and noted. So are commands no test may run: caffeinate, a notification, `open`,
   claude, launchctl, security, defaults.
3. Turns off bytecode writing for this process and the Pythons it starts (Apple's Python would write it under
   ~/Library/Caches).
4. Notes the size and change time (never the contents) of this Mac's real ~/.claude/settings.json
   and of every file in the real ~/.switchboard, to compare at the end.
5. Marks the run with SWITCHBOARD_TEST_RUN=1 in the environment, which every process a test starts inherits:
   switchboard's Keychain helper (accounts/keychain.py) never starts the real /usr/bin/security while it is set.

tests/test_zz_real_folders.py runs last and fails on anything noted. If a partial run never
reaches it, the same check runs when the process exits and turns the exit code into 1. A change to the
real ~/.claude/settings.json alone is printed as a note, not counted: Claude Code writes that file itself in
every open window, and this run has no way to (see below).

What the watch cannot see: writes made by child processes (the hook is per process). The children tests
start are headless Chrome (its own temp profile), /bin/sh and Python running the status line (with HOME,
SWITCHBOARD_HOME and CLAUDE_CONFIG_DIR in temp folders), and osascript (only the exact read-back below).
A command that names the real folders is refused before it starts, and so is a child whose environment points
HOME, CLAUDE_CONFIG_DIR or SWITCHBOARD_HOME at them; the size-and-time check of ~/.switchboard at the end
catches the rest. A write through a descriptor is checked when the descriptor is opened. A child that inherits
this process's environment cannot run the real Keychain tool through switchboard (point 5); one started with an
environment of its own does not carry the mark.

Run the suite only as:
    SWITCHBOARD_HOME="$(mktemp -d)/data" python3 -m unittest discover -s tests
"""
import atexit
import os
import pwd
import sys
import tempfile

# No bytecode from this run or the Pythons it starts: Apple's Python keeps it under ~/Library/Caches, which no
# test may write (importing a module from a folder it has no cache for yet would make it create folders there).
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

# Point 5: a test's child processes are outside the audit hook below; this mark reaches them, and the Keychain helper
# refuses the real tool while it is there (keychain.TEST_RUN_VAR).
os.environ['SWITCHBOARD_TEST_RUN'] = '1'

if 'switchboard.core' in sys.modules and not os.environ.get('SWITCHBOARD_HOME'):
    raise RuntimeError('switchboard was imported before tests/_isolate.py: these tests would use the real data folder')
if not os.environ.get('SWITCHBOARD_HOME'):
    os.environ['SWITCHBOARD_HOME'] = os.path.join(tempfile.mkdtemp(prefix='switchboard-test-'), 'data')

# The real home comes from the user database, not $HOME: tests point $HOME at temp folders.
REAL_HOME = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
REAL_DATA = os.path.join(REAL_HOME, '.switchboard')
REAL_SETTINGS = os.path.join(REAL_HOME, '.claude', 'settings.json')
_given = os.path.realpath(os.environ['SWITCHBOARD_HOME'])
if _given == REAL_DATA or _given.startswith(REAL_DATA + os.sep):
    raise RuntimeError('SWITCHBOARD_HOME points at the real data folder. Use SWITCHBOARD_HOME="$(mktemp -d)/data".')

# Every other folder a module finds from the home folder lands in a fresh throwaway place too.
TEST_HOME = tempfile.mkdtemp(prefix='switchboard-test-home-')
os.environ['HOME'] = TEST_HOME
os.environ['CLAUDE_CONFIG_DIR'] = os.path.join(TEST_HOME, '.claude')
# What the person's own shell may set to put Claude Code on a key or a cloud provider: no test may see or use it
# (a real key among them), and the lines `switchboard switch` and doctor say about it are not in any expected output.
for _name in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN', 'CLAUDE_CODE_USE_BEDROCK',
              'CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR', 'CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR',
              'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY', 'CLAUDE_CODE_USE_MANTLE',
              'CLAUDE_CODE_USE_ANTHROPIC_AWS', 'CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD', 'ANTHROPIC_PROFILE',
              'OPENAI_API_KEY', 'CODEX_API_KEY', 'CODEX_ACCESS_TOKEN', 'CODEX_HOME', 'OPENAI_BASE_URL'):   # Codex's too
    os.environ.pop(_name, None)

# This Mac's own running Switchboard (and its status line, in each Claude Code window) rewrites these by itself
# while tests run, so their size and time may move. The audit hook still proves this process never wrote them.
LIVE_REWRITES = frozenset(('state.json', '.lock', 'switchboard.log', 'switchboard.log.1', 'statusline.json',
                           'usage-cache.json', 'notify.json', 'notify.json.lock', 'live', 'live/.pruned',
                           'history.jsonl', 'history.jsonl.1', 'history.jsonl.lock', 'turns',
                           'settings.json.lock', '.auto-choice.lock',       # (core.file_lock's two files)
                           'engine', 'engine-auto.json', 'engine-auto.err', 'engine-auto.pid', 'agent.log',
                           'menubar', 'menubar.log',
                           'readings.jsonl', 'readings.jsonl.lock',        # (the page's readings log)
                           # A Switchboard running for real on the account engine keeps these current by itself:
                           # its list of accounts, the auto-switch loop's files, the usage numbers, the artifact
                           # owners, and the folders its locks, recovery copies, pictures and runs live in.
                           'accounts.json', 'accounts.lock', 'auto-heartbeat.json', 'auto-state.json',
                           'auto-state.json.lock', 'auto.lease', 'auto.log', 'auto.pid', 'consume', 'keychain.gate',
                           'keychain-argv.json', 'owners.json', 'recovery', 'switch.lock', 'usage.json',
                           'usage.json.lock', 'previews', 'hooks', 'chats.json', 'chats-view.json', 'run', 'logins',
                           # and a switch's journal, the loop's note of its own restart, the page's output
                           'switch-journal.json', 'auto-restarted.json', 'serve.out',
                           # the other assistants' folder and its three locks (a command run for real writes them)
                           'providers', 'providers.lock', 'providers-ask.lock', 'providers-hand.lock',
                           'week.json',                                     # (the last week read, kept for a restart)
                           'read-request.json',             # (the page asks the loop for a read by writing this)
                           'put-back.json',                 # (what the engine notes about putting a lost sign-in back)
                           # A Switchboard newer than this copy can be the one running on this Mac. Its sign-in
                           # watch rewrites its heartbeat every few seconds, with its lease, log, record and hold:
                           'signin-watch.json', 'signin-watch.lease', 'signin-watch.log', 'signin-watch.log.1',
                           'signin-watch.out', 'signin-record.jsonl', 'signin-record.jsonl.1', 'signin-hold.json',
                           # the last good copy of settings.json kept at each write, and what was repaired by itself
                           'settings.json.last-good', 'repairs.json', 'repairs.lock',
                           # the records of what Switchboard put in Claude Code's settings: its self-check rewrites
                           # them when it puts a missing entry back or mends one whose Python is gone
                           'statusline-install.json', 'notify-hooks-install.json', 'session-hooks-install.json',
                           # the page watching itself and the menu bar: the watchdog's restarts, the page's starts,
                           # the stop mark (launchd stops the page at logout; every page that starts removes it), the
                           # port another program holds, the watchers' starts of the page and its lock, the page's
                           # starts of the menu bar, and the spells of the notes said when Switchboard is not doing
                           # its job. (menubar-quit.json is left out: only Quit in the menu bar writes it.)
                           'page-watch.json', 'page-starts.json', 'page-stopped.json', 'page-port.json',
                           'page-tries.json', 'page-tries.lock', 'menubar-tries.json', 'watch-spells.json',
                           # The sign-in watch, a process of its own: its heartbeat (every 10 s), lease, log, its
                           # job's output and its record of what happened to Claude Code's sign-in; and what the
                           # engine keeps of Claude Code's own fallback file.
                           'signin-watch.json', 'signin-watch.lease', 'signin-watch.log', 'signin-watch.log.1',
                           'signin-watch.out', 'signin-record.jsonl', 'signin-record.jsonl.1', 'signin-hold.json'))
# A Switchboard running for real on this Mac rewrites the auto-switch loop's files, its logs, the menu bar
# app (rebuilt at each start) and the engine's whole store, and the folders' own times move when a file in them is
# replaced. This process's own writes there are refused by the audit hook, and a child given the real home is
# refused, so those changes are a live Switchboard's.
# turns/: each Claude Code window's notification hooks note when its turns start (notify_hooks.mark)
LIVE_PREFIXES = ('state.json.', 'statusline.json.', 'usage-cache.json.', 'notify.json.', 'live/', 'turns/',
                 'engine-auto.json.', 'engine/', 'menubar/', '.readings.jsonl.',
                 'accounts.json.', '.accounts.json.', 'auto-heartbeat.json.', '.auto-heartbeat.json.',
                 'auto-state.json.', '.auto-state.json.', 'auto.log.', 'consume/', 'owners.json.', '.owners.json.',
                 'recovery/', 'usage.json.', '.usage.json.', 'previews/', 'hooks/', 'chats.json.', '.chats.json.',
                 'chats-view.json.', '.chats-view.json.', 'run/', 'logins/',
                 'keychain-argv.json.', '.switch-journal.json.', '.auto-restarted.json.', 'serve.out.',
                 '.week.json.', 'providers/', 'read-request.json.', '.read-request.json.', 'put-back.json.',
                 '.put-back.json.',
                 # the sign-in watch's heartbeat, logs and records while they are written or rolled over, and the
                 # engine's hold note
                 'signin-watch.json.', '.signin-watch.json.', 'signin-watch.log.', 'signin-record.jsonl.',
                 'signin-hold.json.', '.signin-hold.json.',
                 # a damaged settings file the self-check set aside as settings.json.damaged-<time>
                 'settings.json.damaged-', '.settings.json.damaged-')

# Only the temp folder, the data folder this run was given, and the fresh home may be written.
ALLOWED = tuple(sorted({os.path.join(os.path.realpath(p), '') for p in (
    tempfile.gettempdir(), os.environ['SWITCHBOARD_HOME'], TEST_HOME)}))
PYCACHE = os.path.join(os.path.realpath(sys.pycache_prefix), '') if getattr(sys, 'pycache_prefix', None) else None
DEVICES = frozenset(('/dev/null', '/dev/tty', '/dev/stdout', '/dev/stderr'))
NEVER_RUN = frozenset(('caffeinate', 'open', 'claude', 'codex', 'switchboard-engine', 'launchctl',
                       'security', 'defaults', 'install.sh', 'uninstall.sh'))
NEVER_SAY = ('caffeinate', 'display notification', 'launchctl', 'switchboard-engine')

# osascript may only read one argument back (test_notify), never show or run anything
OSA_READ_BACK = ['-e', 'on run argv', '-e', 'return item 1 of argv', '-e', 'end run', '--']
# A command (a shell line especially) that names the real folders is refused, whatever it would do with them.
NEVER_NAME = tuple(x for home in ('~', '$HOME', '${HOME}', REAL_HOME)
                   for x in (home + '/.switchboard', home + '/.claude', home + '/.codex', home + '/.switchboard-notes'))
# Not even read (an open for reading is refused and noted like a write): Codex's folder, Claude Code's chats, its
# windows' files and its logins, its settings (they may hold a key), the notes Switchboard writes beside its data
# folder, its list of accounts, and its folders of logins, recovery copies and runs.
NEVER_READ_IN = tuple(os.path.join(os.path.realpath(os.path.join(REAL_HOME, x)), '') for x in (
    '.codex', os.path.join('.claude', 'projects'), os.path.join('.claude', 'sessions'), '.switchboard-notes')) + \
    tuple(os.path.join(os.path.realpath(os.path.join(REAL_DATA, x)), '') for x in ('logins', 'recovery', 'run'))
NEVER_READ = frozenset(os.path.realpath(x) for x in (
    os.path.join(REAL_HOME, '.claude.json'), os.path.join(REAL_HOME, '.claude', '.credentials.json'),
    os.path.join(REAL_DATA, 'accounts.json'), REAL_SETTINGS))
VIOLATIONS = []

_WRITE = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
# event -> [(index of a path it changes, index of the dir_fd that path is relative to, or None)]
_PATHS = {'os.rename': [(0, 2), (1, 3)], 'os.remove': [(0, 1)], 'os.rmdir': [(0, 1)], 'os.mkdir': [(0, 2)],
          'os.chmod': [(0, 2)], 'os.chown': [(0, 3)], 'os.chflags': [(0, None)], 'os.lchflags': [(0, None)],
          'os.utime': [(0, 3)], 'os.truncate': [(0, None)], 'os.symlink': [(1, 2)], 'os.link': [(1, 3)],
          'os.setxattr': [(0, None)], 'os.removexattr': [(0, None)],
          'shutil.copyfile': [(1, None)], 'shutil.copymode': [(1, None)], 'shutil.copystat': [(1, None)],
          'shutil.copytree': [(1, None)], 'shutil.move': [(0, None), (1, None)], 'shutil.rmtree': [(0, 1)],
          'shutil.chown': [(0, None)]}
_RUNS = ('subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.exec', 'os.spawn')
# Where each of those events carries the child's environment (None: it inherits ours, whose folders are temp ones).
_ENV_AT = {'subprocess.Popen': 3, 'os.posix_spawn': 2, 'os.exec': 2, 'os.spawn': 3}
_ENV_KEYS = ('HOME', 'CLAUDE_CONFIG_DIR', 'SWITCHBOARD_HOME', 'CODEX_HOME')


def _folder_of(fd):
    """The folder an open descriptor points at (macOS F_GETPATH), or None."""
    try:
        import fcntl
        return os.fsdecode(fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024)).rstrip(b'\0'))
    except (AttributeError, ImportError, OSError, TypeError, ValueError):
        return None


def _allowed(path, dir_fd=None):
    try:
        if isinstance(path, int):
            return True                              # an open file descriptor: its path was checked when it was opened
        p = os.fsdecode(os.fspath(path))
    except TypeError:
        return True
    if isinstance(dir_fd, int) and not os.path.isabs(p):
        base = _folder_of(dir_fd)
        if base is None:
            return True                              # cannot tell; rmtree's own top folder is checked as shutil.rmtree
        p = os.path.join(base, p)
    p = os.path.abspath(p)
    if p in DEVICES:
        return True
    # A link is judged by where it leads: a temp-folder link to a real file is not a way round.
    real = os.path.realpath(p) if os.path.islink(p) else os.path.join(os.path.realpath(os.path.dirname(p)),
                                                                        os.path.basename(p))
    if real.startswith(ALLOWED) or (real + os.sep).startswith(ALLOWED):
        return True
    # Python's own bytecode: in __pycache__, or under sys.pycache_prefix (Apple's Python uses ~/Library/Caches)
    return '.pyc' in os.path.basename(real) and ('%s__pycache__%s' % (os.sep, os.sep) in real or
                                                 (PYCACHE is not None and real.startswith(PYCACHE)))


def _never_read(path):
    """True when path is, or is in, a place in NEVER_READ or NEVER_READ_IN. Judged by its real path, so a link in a
    temp folder is not a way round."""
    try:
        if isinstance(path, int):
            return False                             # an open file descriptor: its path was checked when it was opened
        real = os.path.realpath(os.fsdecode(os.fspath(path)))
    except (TypeError, ValueError):
        return False
    return real in NEVER_READ or (real + os.sep).startswith(NEVER_READ_IN)


def _words(args):
    if isinstance(args, (str, bytes)):
        text = os.fsdecode(args)
        return text, os.path.basename(text.split()[0]) if text.split() else ''
    try:
        items = [os.fsdecode(os.fspath(a)) if not isinstance(a, str) else a for a in args]
    except TypeError:
        return '', ''
    return ' '.join(items), os.path.basename(items[0]) if items else ''


def _refuse(what):
    VIOLATIONS.append(what)
    raise PermissionError('Blocked in tests: %s' % what)


def _real_env(env):
    """'NAME=path' when an environment given to a child points it at the real home, ~/.claude, ~/.codex or
    ~/.switchboard (the child would find the real folders by itself), else None."""
    try:
        items = list(env.items())
    except AttributeError:
        return None
    claude, codex = os.path.join(REAL_HOME, '.claude'), os.path.join(REAL_HOME, '.codex')
    for k, v in items:
        k = os.fsdecode(k) if isinstance(k, bytes) else k
        if k not in _ENV_KEYS or not v:
            continue
        try:
            p = os.path.realpath(os.fsdecode(v) if isinstance(v, bytes) else v)
        except (TypeError, ValueError):
            continue
        if p == REAL_HOME or p == claude or p.startswith(claude + os.sep) or p == REAL_DATA or \
                p.startswith(REAL_DATA + os.sep) or p == codex or p.startswith(codex + os.sep):
            return '%s=%s' % (k, p)
    return None


def _hook(event, args):
    if event == 'open':
        flags = args[2] if len(args) > 2 else 0
        if isinstance(flags, int) and flags & _WRITE and not _allowed(args[0]):
            _refuse('write %s' % (args[0],))
        if not (isinstance(flags, int) and flags & _WRITE) and _never_read(args[0]):
            _refuse('read %s' % (args[0],))
        return
    where = _PATHS.get(event)
    if where is not None:
        for i, fd in where:
            if i < len(args) and args[i] is not None and \
                    not _allowed(args[i], args[fd] if fd is not None and fd < len(args) else None):
                _refuse('%s %s' % (event, args[i]))
        return
    if event in _RUNS:
        where = {'subprocess.Popen': 1, 'os.posix_spawn': 1, 'os.exec': 1, 'os.spawn': 2}.get(event, 0)
        raw = args[where] if where < len(args) else ''
        text, prog = _words(raw)
        items = [str(x) for x in raw] if isinstance(raw, (list, tuple)) else []
        if prog in NEVER_RUN or any(w in text for w in NEVER_SAY + NEVER_NAME) \
                or (prog == 'osascript' and (items[1:8] != OSA_READ_BACK or len(items) != 9)):
            _refuse('run %s' % text[:120])
        at = _ENV_AT.get(event)
        bad = _real_env(args[at]) if at is not None and at < len(args) and args[at] is not None else None
        if bad:
            _refuse('run %s with %s' % (text[:80], bad))


def _stat(path):
    try:
        st = os.lstat(path)
        return st.st_size, st.st_mtime_ns
    except OSError:
        return None


def snapshot():
    """Size and change time of the real settings file and of everything in the real data folder."""
    files = {}
    for folder, dirs, names in os.walk(REAL_DATA):
        for name in dirs + names:
            path = os.path.join(folder, name)
            files[os.path.relpath(path, REAL_DATA)] = _stat(path)
    return {'settings': _stat(REAL_SETTINGS), 'data': files}


def outside(before, after):
    """Changes this run has no way to make, said aloud but not counted. Claude Code itself writes
    ~/.claude/settings.json (a permission allowed for good, /config) in every open window. This process's writes
    there are refused by the audit hook, every child starts with HOME and CLAUDE_CONFIG_DIR in temp folders, and a
    child given the real ones is refused."""
    if before['settings'] != after['settings']:
        return ['~/.claude/settings.json changed during the run (size, time) %s -> %s, by something outside it'
                % (before['settings'], after['settings'])]
    return []


def _live_temp(rel):
    """A live file's own temporary name while it is written (core.write_bytes's ".<name>.<random>.tmp", notify's
    ".<name>.<pid>.<n>.tmp"): as live as the file itself."""
    return ('/' not in rel and rel.startswith('.') and rel.endswith('.tmp') and
            any(rel.startswith('.%s.' % n[:40]) for n in LIVE_REWRITES if not n.startswith('.')))


def changes(before, after):
    """What changed in the real data folder between two snapshots, as plain lines (paths and sizes only)."""
    out = []
    for rel in sorted(set(before['data']) | set(after['data'])):
        if rel in LIVE_REWRITES or rel.startswith(LIVE_PREFIXES) or _live_temp(rel):
            continue
        a, b = before['data'].get(rel, 'absent'), after['data'].get(rel, 'absent')
        if a != b:
            out.append('~/.switchboard/%s: %s -> %s' % (rel, a, b))
    return out


BEFORE = snapshot()


def problems():
    return list(VIOLATIONS) + changes(BEFORE, snapshot())


def _at_exit():
    found = problems()
    noted = outside(BEFORE, snapshot())
    try:
        sys.stdout.flush()
    except Exception:
        pass
    if noted:
        sys.stderr.write('\nNote: %s\n' % '\n  '.join(noted))
        sys.stderr.flush()
    if found:
        sys.stderr.write('\nTESTS TOUCHED THE REAL FOLDERS OR RAN A FORBIDDEN COMMAND:\n  %s\n' % '\n  '.join(found))
        sys.stderr.flush()
        os._exit(1)


sys.addaudithook(_hook)
atexit.register(_at_exit)
