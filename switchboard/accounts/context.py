"""Everything the account engine reaches outside itself, in one place, so tests can hand it fakes.

current() is what api.py uses: the environment and the folders read now (never fixed at import), the one Keychain
gate of this process, the real network and clocks, and core.event for the history. use(ctx) puts a made-up one in
place for a block (tests): every Keychain call, request, clock, process listing and event then goes to the fakes.
"""
import fcntl
import os
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

from . import keychain as keychain_mod
from . import net


def _version():
    try:
        from .. import core
        return core.VERSION
    except Exception:  # noqa: BLE001 - the engine runs without the app too
        return '0'


@dataclass
class Context:
    env: Dict[str, str]
    home: str
    data_dir: str
    keychain: object
    http: object
    wall: Callable[[], float] = time.time
    mono: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    events: Optional[Callable] = None               # events(text, kind, facts dict)
    log: Optional[Callable] = None                  # log(text)
    disk_free: Optional[Callable] = None            # disk_free(path) -> bytes
    proc_start: Optional[Callable] = None           # proc_start(pid) -> str or None (ps lstart)
    pid_alive: Optional[Callable] = None            # pid_alive(pid) -> bool
    claude: Optional[str] = None                    # the claude program for a run-as
    user_agent: str = field(default_factory=lambda: 'Switchboard/%s' % _version())
    hold_awake: Optional[Callable] = None           # hold_awake(seconds) -> a context manager keeping the Mac awake
                                                    # (awake.held_awake); None holds nothing
    sleeping_soon: Optional[Callable] = None        # sleeping_soon() -> True while the lid is shut on battery
                                                    # (awake.lid_shut_on_battery); None: never known

    def __post_init__(self):
        if self.log is None:
            self.log = _core_log
        if self.events is None:
            self.events = _core_event
        if self.disk_free is None:
            from .ccfiles import free_bytes
            self.disk_free = free_bytes
        if self.proc_start is None:
            self.proc_start = ps_start
        if self.pid_alive is None:
            self.pid_alive = pid_alive

    def path(self, *parts):
        return os.path.join(self.data_dir, *parts)

    def say(self, text, kind='info', **facts):
        """Record one event (the history, the page's list, switchboard.log). Never raises."""
        try:
            self.events(text, kind, facts)
        except Exception:  # noqa: BLE001 - recording never stops what it records
            pass


_OVERRIDE = threading.local()
_KEYCHAINS = {}
_KC_LOCK = threading.Lock()


def _core_log(text):
    try:
        from .. import core
        core.log(text)
    except Exception:  # noqa: BLE001
        pass


def _core_event(text, kind, facts):
    """core.event under the app's state lock when it can be had within a second; else straight to the history and
    the log (the caller may itself be inside core.locked(), and flock would wait on ourselves)."""
    from .. import core
    path = core.data_path('.lock')
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        got = False
        for _ in range(20):
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                got = True
                break
            except (BlockingIOError, PermissionError):
                time.sleep(0.05)
        if got:
            st = core.load()
            core.event(st, text, kind, **facts)
            core.save(st)
            return
    finally:
        os.close(fd)
    core.log(text)
    try:
        from .. import history
        history.from_event(text, kind, facts)
    except Exception:  # noqa: BLE001
        pass


def ps_start(pid):
    """When a process started, as ps prints it (LC_ALL=C, UTC), or None when that can't be told."""
    try:
        r = subprocess.run(['/bin/ps', '-o', 'lstart=', '-p', str(int(pid))], capture_output=True, timeout=5,
                           env={'LC_ALL': 'C', 'TZ': 'UTC', 'PATH': '/usr/bin:/bin'})
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    out = r.stdout.decode('ascii', 'replace').strip()
    return out if r.returncode == 0 and out else None


def pid_alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def keychain_for(data_dir, tool=keychain_mod.TOOL):
    """This process's one Keychain gate (per data folder and tool): deadlines that grow with this Mac's load, and a
    late answer told apart as "a Keychain dialog is waiting" while macOS shows one."""
    key = (data_dir, tool)
    with _KC_LOCK:
        kc = _KEYCHAINS.get(key)
        if kc is None:
            kc = keychain_mod.Keychain(tool=tool, gate_path=os.path.join(data_dir, 'keychain.gate'), log=_core_log,
                                       load=keychain_mod.system_load_per_core,
                                       dialog=keychain_mod.security_agent_up)
            _KEYCHAINS[key] = kc
        return kc


def default():
    from .. import core
    data = core.data_dir()
    claude = os.environ.get('SWITCHBOARD_CLAUDE') or shutil.which('claude') or None
    from .. import awake
    return Context(env=dict(os.environ), home=core.home(), data_dir=data, keychain=keychain_for(data),
                   http=net.Http(), claude=claude, hold_awake=awake.held_awake,
                   sleeping_soon=awake.lid_shut_on_battery)


def current():
    ctx = getattr(_OVERRIDE, 'ctx', None) or _GLOBAL_OVERRIDE.get('ctx')
    return ctx if ctx is not None else default()


_GLOBAL_OVERRIDE = {}


@contextmanager
def use(ctx, all_threads=True):
    """Make ctx the engine's context for the block (all threads by default: the engine starts worker threads)."""
    if all_threads:
        before = _GLOBAL_OVERRIDE.get('ctx')
        _GLOBAL_OVERRIDE['ctx'] = ctx
        try:
            yield ctx
        finally:
            if before is None:
                _GLOBAL_OVERRIDE.pop('ctx', None)
            else:
                _GLOBAL_OVERRIDE['ctx'] = before
    else:
        before = getattr(_OVERRIDE, 'ctx', None)
        _OVERRIDE.ctx = ctx
        try:
            yield ctx
        finally:
            _OVERRIDE.ctx = before
