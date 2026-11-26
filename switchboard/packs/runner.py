"""Runs the few outside programs the packs need, the same careful way every time.

Only these argv shapes run (the program is named by its absolute path; its file name is what is checked):

  curl        -fsSL --proto <=https | =http,https for a loopback test server> --retry 2 --connect-timeout 15
              --max-filesize N -o <file> <url>                           (a pinned download)
  node, bun, uv, codegraph, claude-mem   --version                       (is it there, and how new)
  npx         --yes file:<checked package> install --ide claude-code --provider claude --model haiku --no-auto-start
              --yes file:<checked package> (start | stop | status | repair | version | telemetry disable)
              (the package is <data>/packs/claude-mem/claude-mem-<version>.tgz, which claudemem_install.py downloads
              and checks against npm's pinned integrity right before each run: npx never looks a name up itself. It
              is given as package_spec builds it, never as a bare path, which npx runs as a command)
  claude-mem  start | stop | status                                      (its hook launcher)
  codegraph   init <folder> -y | sync <folder> | status <folder> --json | telemetry off | daemon
  graphify    extract <folder> --code-only | update <folder>

Never a shell: argv is a list, so a space or a ';' in a folder name stays part of one argument. The environment is
built here from nothing: HOME, a PATH of the folders named, the proxy settings the person has, CLAUDE_CONFIG_DIR when
they set it, and the few variables each step names (claude-mem's and codegraph's own switches, npm's and Bun's cache
folders). Never an ANTHROPIC_* variable, a token or a key. stdin is /dev/null; the working folder is a fresh temp
folder unless a step names one. Each run gets its own process group, so a timeout ends it and everything it started
that stayed in the group. Output is capped (its start and its end are kept), secrets that look like keys or tokens
are masked, and it goes to an owner-only log file under <data>/packs/logs.
"""
import os
import re
import select
import signal
import subprocess
import tempfile
import time

from switchboard.packs import common

RUNNING = {}                   # pid -> argv of each command running now (its process group has the same id)
OUTPUT_HEAD = 32 * 1024        # bytes kept from the start of a command's output
OUTPUT_TAIL = 96 * 1024        # and from its end
LOGS_KEPT = 12                 # log files kept per pack
DEFAULT_TIMEOUT = 600
CM_SUBS = ('start', 'stop', 'status', 'repair', 'version')
CM_INSTALL_ARGS = ['--ide', 'claude-code', '--provider', 'claude', '--model', 'haiku', '--no-auto-start']
PASS_ENV = ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'no_proxy', 'NO_PROXY', 'all_proxy',
            'ALL_PROXY', 'USER', 'LOGNAME')
EXTRA_OK = re.compile(r'^(CLAUDE_MEM_[A-Z0-9_]+|DO_NOT_TRACK|CODEGRAPH_TELEMETRY|CODEGRAPH_NO_UPDATE_CHECK|'
                      r'npm_config_(cache|update_notifier|fund|audit|loglevel|yes)|BUN_INSTALL_CACHE_DIR|'
                      r'UV_NO_MODIFY_PATH|NO_COLOR|GRAPHIFY_QUERY_LOG_DISABLE)$')
NEVER_PASS = re.compile(r'^(ANTHROPIC_|CLAUDE_CODE_OAUTH|AWS_|GITHUB_TOKEN|GH_TOKEN|NPM_TOKEN|OPENAI_)|'
                        r'(TOKEN|SECRET|PASSWORD|API_KEY)$')
SECRET_RE = re.compile(r'(sk-ant-[A-Za-z0-9_-]{6,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|'
                       r'npm_[A-Za-z0-9]{30,}|xox[abprs]-[A-Za-z0-9-]{10,}|(?i:bearer)\s+[A-Za-z0-9._~+/=-]{12,}|'
                       r'(?i:authorization:)\s*\S+)')


class NotAllowed(common.Refused):
    """A command outside the allow-list. Nothing was started."""


class Result(object):
    def __init__(self, argv, code, out, timed_out, seconds, log_path):
        self.argv, self.code, self.out, self.timed_out = argv, code, out, timed_out
        self.seconds, self.log_path = seconds, log_path

    @property
    def ok(self):
        return self.code == 0 and not self.timed_out

    def last_line(self):
        lines = [x.strip() for x in self.out.splitlines() if x.strip()]
        return lines[-1][:300] if lines else ''


# ---------------------------------------------------------------- the allow-list

def _is_abs_folder_arg(a):
    return isinstance(a, str) and os.path.isabs(a) and '\0' not in a


def check(argv):
    """Raises NotAllowed unless argv is one of the shapes above. argv[0] must be an absolute path."""
    if not argv or not all(isinstance(a, str) for a in argv):
        raise NotAllowed('Switchboard runs only a list of plain arguments.')
    exe, args = argv[0], list(argv[1:])
    if not os.path.isabs(exe):
        raise NotAllowed('Switchboard runs programs by their full path only (%s).' % exe)
    prog = os.path.basename(exe)
    ok = False
    if prog == 'curl':
        rest = args[11:]
        ok = (len(args) >= 12 and args[:2] == ['-fsSL', '--proto'] and args[2] in ('=https', '=http,https')
              and args[3:8] == ['--retry', '2', '--connect-timeout', '15', '--max-filesize'] and args[8].isdigit()
              and args[9] == '-o' and _is_abs_folder_arg(args[10])
              and ((args[2] == '=https' and len(rest) == 1 and rest[0].startswith('https://'))
                   or (args[2] == '=http,https' and len(rest) == 3 and rest[:2] == ['--noproxy', '*']
                       and _loopback(rest[2]))))
    elif prog in ('node', 'bun', 'uv', 'uvx', 'claude-mem', 'codegraph') and args == ['--version']:
        ok = True
    elif prog == 'npx':
        ok = (len(args) >= 3 and args[0] == '--yes' and _package(args[1])
              and ((args[2] == 'install' and args[3:] == CM_INSTALL_ARGS)
                   or (args[2] in CM_SUBS and args[3:] == [])
                   or args[2:] == ['telemetry', 'disable']))
    elif prog == 'claude-mem':
        ok = args in (['start'], ['stop'], ['status'])
    elif prog == 'codegraph':
        ok = ((len(args) == 3 and args[0] == 'init' and _is_abs_folder_arg(args[1]) and args[2] == '-y')
              or (len(args) == 2 and args[0] == 'sync' and _is_abs_folder_arg(args[1]))
              or (len(args) == 3 and args[0] == 'status' and _is_abs_folder_arg(args[1]) and args[2] == '--json')
              or args in (['telemetry', 'off'], ['daemon']))
    elif prog == 'graphify':
        ok = ((len(args) == 3 and args[0] == 'extract' and _is_abs_folder_arg(args[1]) and args[2] == '--code-only')
              or (len(args) == 2 and args[0] == 'update' and _is_abs_folder_arg(args[1])))
    if not ok:
        raise NotAllowed('Switchboard does not run that command: %s' % ' '.join(argv)[:200])
    return prog


def package_spec(path):
    """How npx is given claude-mem's checked package file, built here only: `file:` and the file's path as it is.

    Never the bare path: with no --package, npx first looks for a file named by its first word and runs it as a
    command (libnpmexec's localFileExists; an absolute path is that path), so a .tgz gives "Permission denied", exit
    126. Never encoded: npm percent-encodes a space, #, % and ? itself before it reads the spec as a URL
    (npm-package-arg's fromFile), so an encoded path names another file, and the plain one names this file (homes
    named with a space, #, % or ? all worked). Both checked 2026-11-14 against real npm 10.9.9 and 11.19.0, offline.

    npm also takes a '..' out by name, never through a link. When the path goes through a link and then '..', npm
    would read another file than the one checked, so that is refused; a '..' with no link before it is the same file."""
    if not isinstance(path, str) or not os.path.isabs(path) or '\0' in path:
        raise NotAllowed("claude-mem's package was not run: Switchboard gives npx its full path only.")
    if os.path.realpath(os.path.normpath(path)) != os.path.realpath(path):
        raise NotAllowed("claude-mem's package was not run: its path goes through a link and then '..', which npm "
                         'reads by name, so npm would read another file (%s).' % path)
    return 'file:' + path


def _package(spec):
    """npx's spec for claude-mem's npm package file as Switchboard keeps it: exactly what package_spec builds for
    that file. Never a name npx would look up by itself, and never the bare path."""
    if not isinstance(spec, str) or not spec.startswith('file:'):
        return False
    path = spec[len('file:'):]
    if not (os.path.isabs(path) and os.path.dirname(path) == os.path.join(common.packs_dir(), 'claude-mem')
            and re.match(r'^claude-mem-\d+\.\d+\.\d+\.tgz$', os.path.basename(path))):
        return False
    try:
        return package_spec(path) == spec
    except NotAllowed:
        return False


def _loopback(url):
    return bool(re.match(r'^http://127\.0\.0\.1:\d+/', url))


def curl_argv(url, dest, max_bytes, curl='/usr/bin/curl'):
    """The one download command: fails on an HTTP error, follows GitHub's redirect, https only (a loopback test
    server aside), a size cap, and no proxy for a loopback address."""
    local = _loopback(url)
    argv = [curl, '-fsSL', '--proto', '=http,https' if local else '=https', '--retry', '2', '--connect-timeout', '15',
            '--max-filesize', str(int(max_bytes)), '-o', dest]
    return argv + (['--noproxy', '*', url] if local else [url])


# ---------------------------------------------------------------- the environment

def environment(path_dirs, extra=None, environ=None, home=None):
    """The environment a command gets: built from nothing, never copied whole."""
    environ = os.environ if environ is None else environ
    env = {'HOME': home or common.home(), 'PATH': os.pathsep.join(d for d in path_dirs if d),
           'LANG': 'en_US.UTF-8', 'LC_ALL': 'en_US.UTF-8', 'NO_COLOR': '1', 'TERM': 'dumb'}
    for k in PASS_ENV:
        if environ.get(k):
            env[k] = environ[k]
    if environ.get('CLAUDE_CONFIG_DIR'):
        env['CLAUDE_CONFIG_DIR'] = environ['CLAUDE_CONFIG_DIR']
    for k, v in (extra or {}).items():
        if NEVER_PASS.search(k) or not EXTRA_OK.match(k):
            raise NotAllowed('Switchboard does not pass %s to an outside program.' % k)
        env[k] = str(v)
    return env


def scrub(text):
    return SECRET_RE.sub('[hidden]', text)


# ---------------------------------------------------------------- logs

def logs_dir():
    return os.path.join(common.packs_dir(), 'logs')


def new_log(pack):
    """A fresh owner-only log file for one pack action; the oldest beyond LOGS_KEPT go."""
    d = common.make_private_dir(logs_dir())
    path = os.path.join(d, '%s-%s-%d.log' % (pack, time.strftime('%Y%m%d-%H%M%S'), os.getpid()))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.close(fd)
    try:
        mine = sorted(n for n in os.listdir(d) if n.startswith(pack + '-') and n.endswith('.log'))
        for old in mine[:-LOGS_KEPT]:
            os.unlink(os.path.join(d, old))
    except OSError:
        pass
    return path


def note(log_path, text):
    """One line in the log (already plain text, scrubbed here)."""
    if not log_path:
        return
    try:
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(scrub(text).rstrip('\n') + '\n')
    except OSError:
        pass


def tail(log_path, lines=40):
    raw = common.read_bytes(log_path) if log_path else None
    if not raw:
        return []
    return raw.decode('utf-8', 'replace').splitlines()[-lines:]


# ---------------------------------------------------------------- running

def run(argv, env, cwd=None, timeout=DEFAULT_TIMEOUT, log_path=None, on_start=None):
    """Run one allowed command. Returns a Result; never raises for the command's own failure. A download is watched
    as well as capped: curl's --max-filesize does not stop a body whose size is not known first in every curl (before
    8.4, its manual says so), so the file it writes is looked at while it runs, and past the cap the download ends.
    on_start, when given, is called with the command's pid as soon as it has started, before anything is read from
    it (claude-mem's long runs note it down). If on_start raises, the command is ended and the error goes on: no run
    goes on that its caller could not note down."""
    prog = check(argv)
    cap, dest = (int(argv[9]), argv[11]) if prog == 'curl' else (None, None)
    too_big = False
    own = None
    if cwd is None:
        own = cwd = tempfile.mkdtemp(prefix='switchboard-run-')
    note(log_path, '$ %s' % ' '.join(argv))
    t0 = time.time()
    head, tail_buf, total = bytearray(), bytearray(), 0
    timed_out = False
    try:
        try:
            p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 cwd=cwd, env=env, start_new_session=True, close_fds=True)
        except OSError as e:
            note(log_path, 'could not start: %s' % e)
            return Result(argv, 127, 'could not start: %s' % e, False, 0.0, log_path)
        RUNNING[p.pid] = list(argv)
        note(log_path, '(pid %d)' % p.pid)
        if on_start is not None:
            try:
                on_start(p.pid)
            except BaseException:
                _end_group(p)
                RUNNING.pop(p.pid, None)
                p.stdout.close()
                raise
        fd = p.stdout.fileno()
        exited_at = None
        while True:
            now = time.time()
            if exited_at is None and p.poll() is not None:
                exited_at = now
            if exited_at is not None and now - exited_at > 1.0:
                break                    # a daemon it started may hold the pipe open: stop reading
            if exited_at is None and now - t0 > timeout:
                timed_out = True
                _end_group(p)
                exited_at = time.time()
                continue
            if exited_at is None and cap is not None and _over(dest, cap):
                too_big = True
                _end_group(p)
                exited_at = time.time()
                continue
            r, _, _ = select.select([fd], [], [], 0.2)
            if not r:
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                if exited_at is None:
                    try:
                        p.wait(timeout=max(0.1, timeout - (time.time() - t0)))
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        _end_group(p)
                break
            total += len(chunk)
            if len(head) < OUTPUT_HEAD:
                take = chunk[:OUTPUT_HEAD - len(head)]
                head += take
                chunk = chunk[len(take):]
            if chunk:
                tail_buf += chunk
                if len(tail_buf) > OUTPUT_TAIL:
                    del tail_buf[:len(tail_buf) - OUTPUT_TAIL]
        try:
            p.stdout.close()
        except OSError:
            pass
        code = p.wait() if p.returncode is None else p.returncode
        if cap is not None and (too_big or _over(dest, cap)):
            too_big, code = True, 63                       # curl's own code for a download over its cap
        RUNNING.pop(p.pid, None)
    finally:
        if own:
            try:
                os.rmdir(own)
            except OSError:
                import shutil
                shutil.rmtree(own, ignore_errors=True)
    skipped = total - len(head) - len(tail_buf)
    out = head.decode('utf-8', 'replace') + ('\n[... %d bytes left out ...]\n' % skipped if skipped > 0 else '') + \
        tail_buf.decode('utf-8', 'replace')
    if too_big:
        out += '\nstopped: the download was more than its cap of %d bytes\n' % cap
    out = scrub(out)
    secs = time.time() - t0
    note(log_path, out)
    note(log_path, '%s (%.1f s)' % ('timed out after %d s' % timeout if timed_out else 'exit %s' % code, secs))
    return Result(argv, code, out, timed_out, secs, log_path)


def _over(path, cap):
    try:
        return os.path.getsize(path) > cap
    except OSError:
        return False


def _end_group(p):
    """End the command's whole process group: asked first, told after 5 s."""
    for sig, wait in ((signal.SIGTERM, 5), (signal.SIGKILL, 5)):
        try:
            os.killpg(p.pid, sig)
        except OSError:
            return
        try:
            p.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue
