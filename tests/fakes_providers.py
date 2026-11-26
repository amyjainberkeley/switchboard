"""Stand-ins for what a provider adapter reaches outside Switchboard (switchboard.providers.contract.Reach).

make_reach() builds a Reach whose folders are under a throwaway root, whose programs run for real (stand-in programs
a test wrote, never a vendor's), and whose exec, app links, clocks and terminal are recorded or set by the test.
Nothing here can reach a real app: tests/_isolate.py refuses to run any program called claude or codex, and every
stand-in is called fake-claude or fake-codex. No test here; the test files that use it hold the tests.
"""
import os
import select
import signal
import subprocess
import sys
import time

from switchboard import core
from switchboard.providers.contract import Pipe, ProviderError, Ran, Reach

OUT_MAX = 1 << 20           # what a run keeps of each of its two outputs, as run.run_program does


class Calls:
    """What a Reach was asked to do, in order: runs [(argv, stdin, env, cwd)], execs [(path, argv, env, cwd)],
    links [url], pipes [LinePipe]."""

    def __init__(self):
        self.runs, self.execs, self.links, self.pipes = [], [], [], []


def _end_group(proc, sig):
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def run_real(argv, stdin=None, timeout=30.0, env=None, cwd=None):
    """One stand-in program to its end, as run.run_program does: a list, never a shell; its own process group, so a
    time-out ends its children too."""
    start = time.monotonic()
    try:
        proc = subprocess.Popen([str(a) for a in argv], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=dict(env) if env is not None else None, cwd=cwd,
                                start_new_session=True)
    except OSError:
        return Ran(code=None, missing=True)
    try:
        out, err = proc.communicate((stdin or '').encode('utf-8'), timeout=timeout)
    except subprocess.TimeoutExpired:
        _end_group(proc, signal.SIGTERM)
        try:
            proc.communicate(timeout=2.0)
        except subprocess.TimeoutExpired:
            _end_group(proc, signal.SIGKILL)
            proc.communicate()
        return Ran(code=None, took_s=time.monotonic() - start, timed_out=True)
    return Ran(code=proc.returncode, out=out[:OUT_MAX].decode('utf-8', 'replace'),
               err=err[:OUT_MAX].decode('utf-8', 'replace'), took_s=time.monotonic() - start)


class LinePipe(Pipe):
    """A stand-in program kept talking, one line each way (an app server's stdio). sent: every line written. Its
    standard error goes to /dev/null, exactly as the contract says run.open_pipe does; lane A's own tests and the
    merge's flow test use run.open_pipe itself, so a stand-in is never kinder than the real pipe."""

    def __init__(self, argv, env=None, cwd=None):
        self.sent = []
        self._buf = b''
        self.proc = subprocess.Popen([str(a) for a in argv], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=dict(env) if env is not None else None,
                                     cwd=cwd, start_new_session=True)

    def send(self, line):
        self.sent.append(line)
        try:
            self.proc.stdin.write(line.encode('utf-8') + b'\n')
            self.proc.stdin.flush()
        except (OSError, ValueError):
            raise ProviderError('refused', 'The program stopped answering.')

    def receive(self, timeout):
        end = time.monotonic() + timeout
        fd = self.proc.stdout.fileno()
        while b'\n' not in self._buf:
            left = end - time.monotonic()
            if left <= 0:
                return None
            ready, _, _ = select.select([fd], [], [], left)
            if not ready:
                return None
            chunk = os.read(fd, 65536)
            if not chunk:
                return None
            self._buf += chunk
        line, self._buf = self._buf.split(b'\n', 1)
        return line.decode('utf-8', 'replace')

    def close(self, grace=3.0):
        try:
            self.proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            return self.proc.wait(grace)
        except subprocess.TimeoutExpired:
            _end_group(self.proc, signal.SIGTERM)
            try:
                return self.proc.wait(2.0)
            except subprocess.TimeoutExpired:
                _end_group(self.proc, signal.SIGKILL)
                return self.proc.wait()
        finally:
            for stream in (self.proc.stdout,):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass


def stand_in(folder, name, body):
    """Write a stand-in program: folder/name, run by this Python with -I, mode 0755. body is Python source. Returns
    its path. name must never be a vendor program's own name (claude, codex)."""
    if name in ('claude', 'codex'):
        raise ValueError('a stand-in is never called %s' % name)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, 'w') as f:
        f.write('#!%s -I\n%s' % (sys.executable, body))
    os.chmod(path, 0o755)
    return path


def child_python(code, root, timeout=60.0, **env):
    """Run Python source in a child process for a test that needs two real processes (a lock, two writers), as
    run_real does. The child always gets SWITCHBOARD_HOME (this run's data folder), HOME (root/home),
    PYTHONDONTWRITEBYTECODE and the parent's sys.path, plus env; any folder value outside the temp folder is refused
    before it starts, because a child is not watched by tests/_isolate.py and would otherwise find the real data folder
    on its own (the shape of the Nov 19 accident)."""
    import tempfile
    tmp = os.path.realpath(tempfile.gettempdir())
    data = os.path.realpath(core.data_dir())
    home = os.path.realpath(os.path.join(root, 'home'))
    the_env = {'SWITCHBOARD_HOME': data, 'HOME': home, 'PYTHONDONTWRITEBYTECODE': '1',
               'PYTHONPATH': os.pathsep.join(p for p in sys.path if p), 'PATH': '/usr/bin:/bin'}
    the_env.update(env)
    for name in ('SWITCHBOARD_HOME', 'HOME', 'CLAUDE_CONFIG_DIR', 'CODEX_HOME'):
        value = the_env.get(name)
        if value and not os.path.realpath(value).startswith(tmp + os.sep):
            raise ValueError('child_python: %s is outside the temp folder' % name)
    return run_real([sys.executable, '-B', '-c', code], timeout=timeout, env=the_env)


def make_reach(root, env=None, path_dirs=(), app_dirs=(), tty=False, wall=None, mono=None, run=None, pipe=None,
               alive_pids=(), alive=None):
    """(Reach, Calls) for a made-up Mac under root: its home is root/home (made here), its data folder is the one
    this test run was given (core.data_dir()), its PATH is path_dirs. run and pipe default to running stand-ins for
    real (and are recorded); execve and open_url are only recorded. A process is alive only when its pid is in
    alive_pids (or alive(pid) says so): a test never asks about a real pid, which may be one of the person's windows.
    Tests that need an adapter to ignore os.environ put made-up values in env (a made-up CODEX_API_KEY) and check
    the Launch."""
    home = os.path.join(root, 'home')
    os.makedirs(home, exist_ok=True)
    calls = Calls()
    the_env = dict(env) if env is not None else {'HOME': home, 'PATH': os.pathsep.join(path_dirs)}

    def _run(argv, stdin=None, timeout=30.0, env=None, cwd=None):
        calls.runs.append((tuple(argv), stdin, dict(env) if env is not None else None, cwd))
        return (run or run_real)(argv, stdin=stdin, timeout=timeout, env=env, cwd=cwd)

    def _pipe(argv, env=None, cwd=None):
        p = (pipe or LinePipe)(argv, env=env, cwd=cwd)
        calls.pipes.append(p)
        return p

    def _execve(path, argv, env, cwd):
        calls.execs.append((path, tuple(argv), dict(env), cwd))

    def _open_url(url, app=None):
        calls.links.append(url if app is None else (url, app))
        return Ran(code=0)

    pids = frozenset(alive_pids)
    reach = Reach(home=home, data_dir=core.data_dir(), env=the_env, path_dirs=tuple(path_dirs),
                  app_dirs=tuple(app_dirs), run=_run, pipe=_pipe, execve=_execve, open_url=_open_url,
                  wall=wall or time.time, mono=mono or time.monotonic, tty=lambda: tty,
                  alive=alive or (lambda pid: pid in pids))
    return reach, calls
