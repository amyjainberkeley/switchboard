"""Starting other programs for Switchboard's other assistants: the real Reach, the environment a child gets, the one
line a person could type instead, and how program versions sort.

The three functions above the line "the real Reach" are shared by every module of this package and do not change
without the lead. Everything else (default_reach, run_program, open_pipe, exec_program, open_link, try_lock) goes below
it.
"""
import re
import shlex
from typing import Mapping, Optional, Sequence, Tuple

from . import contract

_UNSAFE_RE = re.compile('[\x00-\x1f\x7f-\x9f  ]')
_VERSION_RE = re.compile(r'^\s*v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.]+))?')


def child_env(env: Optional[Mapping[str, str]], job: bool = False) -> dict:
    """The environment a started program gets: a copy of env without any BILLING_ENV name, so the work runs on the plan
    the person chose. With job=True only the JOB_ENV_NAMES and JOB_ENV_PREFIXES names are kept at all (a read-only job
    needs no secret and could print its environment back). Never adds a name. env is the Reach's, never os.environ.
    A terminal opened from a Claude Code chat does not pass that chat's marks on (nested_marks())."""
    src = dict(env or {})
    if job:
        src = {k: v for k, v in src.items() if k in contract.JOB_ENV_NAMES or k.startswith(contract.JOB_ENV_PREFIXES)}
    for name in contract.BILLING_ENV + nested_marks(src):
        src.pop(name, None)
    return src


def nested_marks(env: Optional[Mapping[str, str]]) -> tuple:
    """The SESSION_ENV names set in env when env is a Claude Code chat's own (one of NESTED_MARKS is there), in
    SESSION_ENV's order; () in a terminal of the person's own, where a name they set by hand stays."""
    if not env or not any(m in env for m in contract.NESTED_MARKS):
        return ()
    return tuple(n for n in contract.SESSION_ENV if n in env)


def shell_line(argv: Sequence[str], cwd: str, env: Optional[Mapping[str, str]] = None) -> str:
    """The one line a person could type in a terminal for the same start: `cd <folder> && [env -u NAME ...] <program>
    <words>`, each word quoted for a shell. `env -u` names each BILLING_ENV name set in env, so the typed line runs on
    the same plan as Switchboard's own start would, and each mark of another Claude Code chat (nested_marks()). ''
    when a word holds a control character (a terminal would act on it even inside quotes) or anything else
    words.safe() takes out of a printed line (a mark that reverses or hides text would show the line in another order
    than the shell reads it): the command then prints the note's path instead."""
    words = [cwd] + [str(a) for a in argv]
    if any(_UNSAFE_RE.search(w) or _words.safe(w) != w for w in words):
        return ''
    unset = [n for n in contract.BILLING_ENV if env and env.get(n)] + list(nested_marks(env))
    head = 'cd %s && ' % shlex.quote(cwd)
    if unset:
        head += 'env %s ' % ' '.join('-u %s' % n for n in unset)
    return head + ' '.join(shlex.quote(w) for w in words[1:])


def version_key(text: Optional[str]) -> Tuple[int, ...]:
    """A version as a tuple that sorts: '0.169.1' is (0, 169, 1, 1, 0, 0); '0.164.0-alpha.11.1' is
    (0, 164, 0, 0, 11, 1), so a pre-release sorts before its release; '2.1.330 (Claude Code)' is (2, 1, 330, 1, 0, 0).
    None or unreadable is () and sorts first."""
    m = _VERSION_RE.match(text) if isinstance(text, str) else None
    if not m:
        return ()
    head = tuple(int(m.group(i)) for i in (1, 2, 3))
    if m.group(4) is None:
        return head + (1, 0, 0)
    nums = [int(x) for x in re.findall(r'\d+', m.group(4))][:2]
    return head + (0,) + tuple(nums + [0] * (2 - len(nums)))


# ---------- the real Reach (lane A adds it below; nothing above changes) ----------
# The imports below stay below the line so the frozen top is byte for byte the lead's.
import errno  # noqa: E402
import fcntl  # noqa: E402
import os  # noqa: E402
import select  # noqa: E402
import signal  # noqa: E402
import stat  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from contextlib import contextmanager  # noqa: E402

from .. import core  # noqa: E402
from . import words as _words  # noqa: E402  (shell_line above checks each word as every printed line is)

OUT_MAX = 1 << 20           # what a run keeps of each of its two outputs; the rest is read and dropped
PIPE_LINE_MAX = 16 << 20    # a pipe's line longer than this is read past (contract.Pipe.receive)
END_GRACE = 2.0             # seconds between SIGTERM and SIGKILL to a program's process group
SEND_TIMEOUT = 10.0         # a pipe's send never waits longer than this for the program to read
LINK_TIMEOUT = 10.0
OPEN = '/usr/bin/open'      # macOS's own; a link is only ever handed to it as one word of a list
_CHUNK = 1 << 16
_LOCK_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z')


def _path_dirs(text: Optional[str]) -> Tuple[str, ...]:
    """The folders of a $PATH value: absolute ones only (a relative entry would depend on where the command was
    typed), in order, each once."""
    out = []
    for d in (text or '').split(os.pathsep):
        if d and os.path.isabs(d):
            d = os.path.normpath(d)
            if d not in out:
                out.append(d)
    return tuple(out)


def _app_dirs(home: str) -> Tuple[str, ...]:
    return ('/Applications', os.path.join(home, 'Applications'))


def _alive(pid) -> bool:
    """Is that process running. A pid of 0 or below would name a process group to os.kill, so it is never asked."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                 # another user's process: it runs
    except OSError:
        return False
    return True


def _tty() -> bool:
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def default_reach() -> contract.Reach:
    """The real Reach: this process's home, data folder and environment, its PATH, the two application folders, and
    the functions below. The only place this package reads os.environ for a Reach."""
    env = dict(os.environ)
    home = core.home()
    return contract.Reach(home=home, data_dir=core.data_dir(), env=env, path_dirs=_path_dirs(env.get('PATH')),
                          app_dirs=_app_dirs(home), run=run_program, pipe=open_pipe, execve=exec_program,
                          open_url=open_link, wall=time.time, mono=time.monotonic, tty=_tty, alive=_alive)


# ---------- running a program to its end ----------

def _signal_group(pid: int, sig: int) -> bool:
    try:
        os.killpg(pid, sig)
        return True
    except OSError:                 # gone already, or not ours
        return False


def _end(proc: subprocess.Popen) -> None:
    """End a program started in its own session and everything it started: SIGTERM to its process group, END_GRACE
    seconds, then SIGKILL. A child that still holds the group after its parent ended gets the SIGKILL too (the group
    id cannot be given to another process while any member lives)."""
    _signal_group(proc.pid, signal.SIGTERM)
    try:
        proc.wait(END_GRACE)
    except subprocess.TimeoutExpired:
        _signal_group(proc.pid, signal.SIGKILL)
        try:
            proc.wait(END_GRACE)
        except subprocess.TimeoutExpired:
            pass
        return
    if _signal_group(proc.pid, 0):  # its children are still there
        end = time.monotonic() + END_GRACE
        while time.monotonic() < end and _signal_group(proc.pid, 0):
            time.sleep(0.05)
        _signal_group(proc.pid, signal.SIGKILL)


def _close_streams(proc: subprocess.Popen) -> None:
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        if stream is not None:
            try:
                stream.close()
            except (OSError, ValueError):
                pass


def _pump(proc: subprocess.Popen, data: Optional[bytes], deadline: float):
    """Write data to the program's input and read both outputs until both end, keeping the first OUT_MAX bytes of
    each. Returns (out, err, how): how is 'done', 'time' (the deadline passed) or 'held' (the program ended but a child
    of it still holds an output open)."""
    bufs = {proc.stdout.fileno(): bytearray(), proc.stderr.fileno(): bytearray()}
    readers = list(bufs)
    writer, view = None, None
    if data is not None:
        view = memoryview(data)
        if view:
            writer = proc.stdin.fileno()
            os.set_blocking(writer, False)
        else:
            proc.stdin.close()
    ended_at = None
    how = 'done'
    while readers or writer is not None:
        now = time.monotonic()
        if now >= deadline:
            how = 'time'
            break
        if ended_at is None and proc.poll() is not None:
            ended_at = now
        if ended_at is not None and now - ended_at > END_GRACE:
            how = 'held'
            break
        r, w, _ = select.select(readers, [writer] if writer is not None else [], [], min(deadline - now, 0.25))
        for fd in r:
            try:
                chunk = os.read(fd, _CHUNK)
            except OSError:
                chunk = b''
            if not chunk:
                readers.remove(fd)
                continue
            buf = bufs[fd]
            if len(buf) < OUT_MAX:
                buf += chunk[:OUT_MAX - len(buf)]
        if w:
            try:
                n = os.write(writer, view[:_CHUNK])
                view = view[n:]
            except BlockingIOError:
                pass
            except OSError:         # it stopped reading (it ended, or closed its input)
                view = view[:0]
            if not view:
                proc.stdin.close()
                writer = None
    return bytes(bufs[proc.stdout.fileno()]), bytes(bufs[proc.stderr.fileno()]), how


def run_program(argv, stdin: Optional[str] = None, timeout: float = 30.0, env: Optional[Mapping[str, str]] = None,
                cwd: Optional[str] = None) -> contract.Ran:
    """One program to its end or its time limit: a list, never a shell, in its own session so that ending it ends what
    it started. stdin is written to its input and the input closed (None: /dev/null). Each output is kept to OUT_MAX
    bytes and read to its end, so a program that writes a lot never stalls on a full pipe. On the time limit, and on
    any exception while it waits (Ctrl-C, or the SIGTERM and SIGHUP handlers a command sets), its process group is
    ended (SIGTERM, END_GRACE seconds, SIGKILL) before the exception goes on. env None is child_env(os.environ): this
    process's environment without the billing names, never the whole of it."""
    start = time.monotonic()
    the_env = child_env(os.environ) if env is None else dict(env)
    try:
        proc = subprocess.Popen([str(a) for a in argv], stdin=subprocess.PIPE if stdin is not None else
                                subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=the_env,
                                cwd=cwd, start_new_session=True, close_fds=True)
    except OSError:
        missing = not (cwd is not None and not os.path.isdir(cwd))
        return contract.Ran(code=None, took_s=time.monotonic() - start, missing=missing)
    try:
        out, err, how = _pump(proc, stdin.encode('utf-8') if stdin is not None else None, start + timeout)
        if how == 'done':
            try:
                proc.wait(max(start + timeout - time.monotonic(), 0.0))
            except subprocess.TimeoutExpired:
                how = 'time'
        if how != 'done':
            _end(proc)
    except BaseException:
        _end(proc)
        _close_streams(proc)
        raise
    _close_streams(proc)
    took = time.monotonic() - start
    text_out, text_err = out.decode('utf-8', 'replace'), err.decode('utf-8', 'replace')
    if how == 'time':
        return contract.Ran(code=None, out=text_out, err=text_err, took_s=took, timed_out=True)
    return contract.Ran(code=proc.returncode, out=text_out, err=text_err, took_s=took)


# ---------- a program kept talking ----------

class _ProcPipe(contract.Pipe):
    """contract.Pipe over a started program: one line each way, every wait under a deadline."""

    def __init__(self, proc: subprocess.Popen):
        self.proc = proc
        self._buf = bytearray()
        self._skipping = False      # inside a line longer than PIPE_LINE_MAX
        self._eof = False
        os.set_blocking(proc.stdin.fileno(), False)

    def send(self, line: str) -> None:
        data = memoryview((line + '\n').encode('utf-8'))
        try:
            fd = self.proc.stdin.fileno()
        except (OSError, ValueError):
            raise contract.ProviderError('refused', 'The program stopped answering.')
        deadline = time.monotonic() + SEND_TIMEOUT
        while data:
            left = deadline - time.monotonic()
            if left <= 0:
                raise contract.ProviderError('timeout', 'The program stopped reading what Switchboard sent.')
            _, w, _ = select.select([], [fd], [], left)
            if not w:
                continue
            try:
                n = os.write(fd, data[:_CHUNK])
            except BlockingIOError:
                continue
            except OSError:
                raise contract.ProviderError('refused', 'The program stopped answering.')
            data = data[n:]

    def _line(self) -> Optional[bytes]:
        i = self._buf.find(b'\n')
        if i < 0:
            return None
        line = bytes(self._buf[:i])
        del self._buf[:i + 1]
        return line

    def receive(self, timeout: float) -> Optional[str]:
        deadline = time.monotonic() + timeout
        fd = self.proc.stdout.fileno()
        while True:
            line = self._line()
            if line is not None:
                if self._skipping or len(line) > PIPE_LINE_MAX:
                    self._skipping = False
                    return None     # the end of a line past the cap: read past, never kept
                return line.decode('utf-8', 'replace')
            if len(self._buf) > PIPE_LINE_MAX:
                self._skipping = True
                self._buf.clear()
            if self._eof:
                if self._buf and not self._skipping:
                    rest = bytes(self._buf)
                    self._buf.clear()
                    return rest.decode('utf-8', 'replace')
                return None
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            r, _, _ = select.select([fd], [], [], left)
            if not r:
                return None
            try:
                chunk = os.read(fd, _CHUNK)
            except OSError:
                chunk = b''
            if not chunk:
                self._eof = True
            else:
                self._buf += chunk

    def close(self, grace: float = 3.0) -> Optional[int]:
        try:
            self.proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self.proc.wait(grace)
        except subprocess.TimeoutExpired:
            _end(self.proc)
        _close_streams(self.proc)
        return self.proc.returncode


def open_pipe(argv, env: Optional[Mapping[str, str]] = None, cwd: Optional[str] = None) -> contract.Pipe:
    """A program kept talking (a vendor's app server on its standard input and output). Its standard error goes to
    /dev/null: an app server that logs past a pipe's buffer must never block. env None is child_env(os.environ).
    Raises ProviderError('not_installed') when the program cannot start."""
    the_env = child_env(os.environ) if env is None else dict(env)
    try:
        proc = subprocess.Popen([str(a) for a in argv], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=the_env, cwd=cwd, start_new_session=True,
                                close_fds=True)
    except OSError:
        if cwd is not None and not os.path.isdir(cwd):
            raise contract.ProviderError('not_found', 'The folder to start it in is not there any more.')
        raise contract.ProviderError('not_installed', 'The program is not there any more, or cannot run.')
    return _ProcPipe(proc)


# ---------- this process becomes the program ----------

def exec_program(path: str, argv, env: Mapping[str, str], cwd: str) -> None:
    """This process becomes the program, in cwd, with exactly env. Checks first that the program can run and the
    folder is there (OSError when not, and nothing has changed); never returns when it works. An OSError from execve
    itself goes to the caller too, with this process back in its own folder."""
    if not (os.path.isfile(path) and os.access(path, os.X_OK)):
        raise OSError(errno.ENOENT if not os.path.exists(path) else errno.EACCES, 'the program cannot run', path)
    if not os.path.isdir(cwd):
        raise OSError(errno.ENOENT, 'the folder is not there', cwd)
    try:
        here = os.getcwd()
    except OSError:
        here = None
    os.chdir(cwd)
    try:
        os.execve(path, [str(a) for a in argv], dict(env))
    except OSError:
        if here is not None:
            try:
                os.chdir(here)
            except OSError:
                pass
        raise


def open_link(url: str, app: Optional[str] = None) -> contract.Ran:
    """Hand a vendor's documented app link to macOS (to app, the bundle that should answer it, when given)."""
    argv = [OPEN, url] if app is None else [OPEN, '-a', app, url]
    return run_program(argv, timeout=LINK_TIMEOUT)


# ---------- a lock that does not wait ----------

@contextmanager
def try_lock(name: str):
    """One at a time across processes, without waiting: an exclusive flock on <data>/<name>. Yields True when this
    process holds it, False when another holder has it (the caller says so and does not wait). name is one plain file
    name (contract's lock names)."""
    if not isinstance(name, str) or not _LOCK_NAME_RE.match(name):
        raise ValueError('a lock name is one plain file name')
    folder = core.data_dir()
    os.makedirs(folder, mode=0o700, exist_ok=True)
    fd = os.open(os.path.join(folder, name), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held = True
        except OSError:
            held = False
        yield held
    finally:
        os.close(fd)                # closing it lets the lock go


# ---------- the notes folder ----------

def notes_root(reach: contract.Reach) -> str:
    """Where notes are kept: contract.NOTES_DIR_NAME beside the data folder, never inside it (so a rule that lets an
    assistant read notes opens nothing else of Switchboard's), made owner-only (0700), also when it was there wider."""
    data = os.path.normpath(reach.data_dir)
    path = os.path.join(os.path.dirname(data), contract.NOTES_DIR_NAME)
    os.makedirs(path, mode=0o700, exist_ok=True)
    if stat.S_IMODE(os.stat(path).st_mode) != 0o700:
        os.chmod(path, 0o700)
    return path
