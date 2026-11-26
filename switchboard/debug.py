"""`--debug` on any command, and SWITCHBOARD_DEBUG=1: what the command did, on standard error.

One line each, starting `debug: `: each file it read or wrote (its path, once), each program it started (its name and
arguments, a value after a flag that takes a secret masked), each call to the running page (method, path, status, how
long) and to the network (method, host, path: never a query, a header or a body), each call to the Keychain (its verb,
exit status, how long: never a service, an account or a value), and at the end which side did the work: this command,
or the running page, and how long it took and where the time went.

Every line goes through core.redact with emails hidden too, so nothing shaped like a token, a key or an email reaches
the terminal, whatever a path or an argument holds.

The hooks are put in only while tracing() runs and taken out after it, so with it off nothing is hooked and a command's
output is byte for byte what it is without it. They sit where everything passes through: Python's open() and
os.open(), the tree's whole-file write (core.write_bytes), subprocess.Popen (every program, the Keychain's `security`
too), the page's own opener (__main__._page_open), the account engine's HTTPS (accounts/net.py) and urllib's openers
for any other address.
"""
import builtins
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from urllib.parse import urlsplit

from . import core

PREFIX = 'debug: '
FLAG = '--debug'
VARIABLE = 'SWITCHBOARD_DEBUG'
OFF_WORDS = ('', '0', 'false', 'no', 'off')
# Flags whose next argument is a secret (security's -w and -X take a password or its hex; the rest by their names).
SECRET_FLAGS = ('-w', '-X', '-p', '--password', '--token', '--key', '--api-key', '--secret')
GLUED_FLAGS = ('-w', '-X')      # security also takes these with the value in the same word (-wVALUE)
HIDDEN = '[hidden]'
# Commands the variable never turns on for: each runs in the background (the page, the demo, the menu bar), is run by
# Claude Code (the status line, hooks), or hands the terminal to Claude Code (claude, run, resume, hand). --debug
# typed on one still counts: a person asked for it.
NOT_BY_VARIABLE = ('serve', 'demo', 'menubar', 'statusline', 'notify', 'hooks', 'session', 'claude', 'run', 'resume',
                   'hand', 'awake')
# A value after a name that says it is a secret (ANTHROPIC_AUTH_TOKEN=..., "MY_API_KEY": "...", db_password=...),
# whatever its length: core.redact needs a word boundary before the name, so NAME_TOKEN= slips past it.
_NAMED = re.compile(r'(?<![A-Za-z0-9])((?:[A-Za-z0-9]+[_-])*(?:token|key|secret|password|passphrase)["\']?\s*[=:]\s*'
                    r'["\']?)[^\s"\',;&]+', re.I)

STATE = {'on': False}


def _fresh():
    return {'on': True, 'start': time.monotonic(), 'page': 0.0, 'keychain': 0.0, 'network': 0.0, 'programs': 0.0,
            'seen': set(), 'posted': 0, 'unanswered': 0, 'read_state': 0, 'in_page': False, 'in_keychain': False}


# A run of 40 or more key-like characters with letters and digits, wherever it starts: core.redact leaves one that
# follows a hyphen or a slash alone (a path's own parts), and a file name or an argument can hold one after either.
LONG_RUN = re.compile(r'(?=[A-Za-z0-9_+]*\d)(?=[A-Za-z0-9_+]*[A-Za-z])[A-Za-z0-9_+]{40,}')


def say(text):
    """One line on standard error, redacted: core.redact with emails hidden, then any long key-like run."""
    try:
        text = _NAMED.sub(lambda m: m.group(1) + HIDDEN, LONG_RUN.sub(HIDDEN, core.redact(text, emails=True)))
        sys.stderr.write(PREFIX + text + '\n')
        sys.stderr.flush()
    except (OSError, ValueError, AttributeError):
        pass


def at_terminal():
    """True when standard error, where the lines go, is a terminal a person reads."""
    try:
        return sys.stderr.isatty()
    except (AttributeError, ValueError):
        return False


def take(args):
    """(the words without --debug, whether to trace). --debug counts before or after the command word, but not after
    `--` (those words are Claude Code's, or a job's) and not after `switchboard claude` (all of its words are Claude
    Code's). SWITCHBOARD_DEBUG set to anything but 0, false, no or off turns it on too, but only for a command a person
    runs at a terminal: never for one of NOT_BY_VARIABLE, and never when standard error is not a terminal (a variable
    set in a shell profile must not trace the page, a hook or the status line)."""
    if STATE['on']:
        return args, False
    out, ours, found = [], True, False
    for a in args:
        if ours and a == FLAG:
            found = True
            continue
        out.append(a)
        if ours and (a == '--' or (a == 'claude' and len(out) == 1)):
            ours = False
    word = next((a for a in out if not a.startswith('-')), None)
    wanted = found or (os.environ.get(VARIABLE, '').strip().lower() not in OFF_WORDS and word is not None and
                       word not in NOT_BY_VARIABLE and at_terminal())
    return out, wanted


def _ms(seconds):
    return '%d ms' % round(seconds * 1000)


def _where(url):
    """host and path of an address, never its query or fragment."""
    u = urlsplit(url)
    return (u.netloc.rsplit('@', 1)[-1] + (u.path or '/')) if u.netloc else (u.path or url)


def program(args):
    """A program's name and arguments as one line, each value after a flag that takes a secret masked."""
    if isinstance(args, (str, bytes)):
        return args if isinstance(args, str) else args.decode('utf-8', 'replace')
    words, hide = [], False
    for a in args:
        a = a.decode('utf-8', 'replace') if isinstance(a, bytes) else str(a)
        flag = a.split('=', 1)[0]
        if hide:
            a = HIDDEN
        elif flag.startswith('--') and flag in SECRET_FLAGS and '=' in a:
            a = flag + '=' + HIDDEN                  # --password=VALUE
        elif a[:2] in GLUED_FLAGS and len(a) > 2:
            a = a[:2] + HIDDEN                       # -wVALUE
        words.append(a)
        hide = a in SECRET_FLAGS
    return ' '.join(words)


def _file(verb, path):
    try:
        path = os.fsdecode(path)
    except TypeError:
        return
    key = (verb, path)
    if key in STATE['seen']:
        return
    STATE['seen'].add(key)
    say('%s %s' % (verb, path))


# ---------- the hooks: each returns what takes it out again ----------

def _patch(owner, name, new):
    old = getattr(owner, name)
    setattr(owner, name, new)
    return lambda: setattr(owner, name, old)


def _hook_open():
    real = builtins.open

    def traced_open(file, mode='r', *a, **k):
        if isinstance(file, (str, bytes, os.PathLike)):
            _file('read' if set(str(mode)) <= set('rbt') else 'wrote', file)
        return real(file, mode, *a, **k)
    return _patch(builtins, 'open', traced_open)


def _hook_os_open():
    real = os.open
    writes = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT

    def traced_os_open(path, flags, *a, **k):
        name = os.path.basename(os.fsdecode(path)) if isinstance(path, (str, bytes, os.PathLike)) else ''
        if name and not (name.startswith('.') and name.endswith(core.TMP_SUFFIX)):     # (a whole-file write's own
            _file('wrote' if flags & writes else 'read', path)                       # temporary file: its target is)
        return real(path, flags, *a, **k)
    return _patch(os, 'open', traced_os_open)


def _hook_write():
    real = core.write_bytes

    def traced_write(path, data, *a, **k):
        _file('wrote', path)
        return real(path, data, *a, **k)
    return _patch(core, 'write_bytes', traced_write)


def _hook_programs():
    real = subprocess.Popen

    class Traced(real):
        def __init__(self, args, *a, **k):
            say('started %s' % program(args))
            super(Traced, self).__init__(args, *a, **k)

        def _timed(self, fn, *a, **k):
            t = time.monotonic()
            try:
                return fn(*a, **k)
            finally:
                if not STATE['in_keychain']:
                    STATE['programs'] += time.monotonic() - t

        def communicate(self, *a, **k):
            return self._timed(super(Traced, self).communicate, *a, **k)

        def wait(self, *a, **k):
            return self._timed(super(Traced, self).wait, *a, **k)
    return _patch(subprocess, 'Popen', Traced)


def _hook_page(cli):
    real = cli._page_open

    def traced_page(req, timeout):
        method = req.get_method() if hasattr(req, 'get_method') else 'GET'
        url = req.full_url if hasattr(req, 'full_url') else str(req)
        path = urlsplit(url).path or '/'
        t = time.monotonic()
        STATE['in_page'] = True
        try:
            r = real(req, timeout)
        except urllib.error.HTTPError as e:
            STATE['posted' if method == 'POST' else 'read_state'] += 1
            say('page: %s %s -> %d (%s)' % (method, path, e.code, _ms(time.monotonic() - t)))
            raise
        except Exception as e:
            if method == 'POST':
                STATE['unanswered'] += 1
            say('page: %s %s: no answer (%s, %s)' % (method, path, type(e).__name__, _ms(time.monotonic() - t)))
            raise
        finally:
            STATE['in_page'] = False
            STATE['page'] += time.monotonic() - t
        STATE['posted' if method == 'POST' else 'read_state'] += 1
        say('page: %s %s -> %s (%s)' % (method, path, getattr(r, 'status', '?'), _ms(time.monotonic() - t)))
        return r
    return _patch(cli, '_page_open', traced_page)


def _hook_urllib():
    real = urllib.request.OpenerDirector.open

    def traced_urllib(self, fullurl, data=None, *a, **k):
        if STATE['in_page']:
            return real(self, fullurl, data, *a, **k)
        method = fullurl.get_method() if hasattr(fullurl, 'get_method') else ('POST' if data is not None else 'GET')
        url = fullurl.full_url if hasattr(fullurl, 'full_url') else str(fullurl)
        t = time.monotonic()
        try:
            r = real(self, fullurl, data, *a, **k)
        except Exception as e:
            STATE['network'] += time.monotonic() - t
            say('network: %s %s -> %s (%s)' % (method, _where(url), getattr(e, 'code', None) or type(e).__name__,
                                               _ms(time.monotonic() - t)))
            raise
        STATE['network'] += time.monotonic() - t
        say('network: %s %s -> %s (%s)' % (method, _where(url), getattr(r, 'status', '?'), _ms(time.monotonic() - t)))
        return r
    return _patch(urllib.request.OpenerDirector, 'open', traced_urllib)


def _hook_net():
    from .accounts import net
    real = net.Http.request

    def traced_net(self, method, url, headers=None, body=None, timeout_s=10.0):
        t = time.monotonic()
        try:
            r = real(self, method, url, headers=headers, body=body, timeout_s=timeout_s)
        except Exception as e:
            STATE['network'] += time.monotonic() - t
            say('network: %s %s -> %s (%s)' % (method, _where(url), getattr(e, 'kind', None) or type(e).__name__,
                                               _ms(time.monotonic() - t)))
            raise
        STATE['network'] += time.monotonic() - t
        say('network: %s %s -> %s (%s)' % (method, _where(url), r[0] if isinstance(r, tuple) else '?',
                                           _ms(time.monotonic() - t)))
        return r
    return _patch(net.Http, 'request', traced_net)


def _hook_keychain():
    from .accounts import keychain
    real = keychain.Keychain._run

    def traced_keychain(self, key, args, deadline, verb, data=None):
        what = str(args[0]) if args else str(verb)
        t = time.monotonic()
        STATE['in_keychain'] = True
        try:
            r = real(self, key, args, deadline, verb, data)
        except Exception as e:
            say('keychain: %s -> %s (%s)' % (what, type(e).__name__, _ms(time.monotonic() - t)))
            raise
        finally:
            STATE['in_keychain'] = False
            STATE['keychain'] += time.monotonic() - t
        say('keychain: %s -> exit %s (%s)' % (what, r[0] if isinstance(r, tuple) else '?', _ms(time.monotonic() - t)))
        return r
    return _patch(keychain.Keychain, '_run', traced_keychain)


def side():
    """Which side did the work, in one line."""
    if STATE['posted']:
        return 'the running page did the work.'
    if STATE['unanswered']:
        return 'the page did not answer, so this command did the work.'
    if STATE['read_state']:
        return "this command did the work, with the running page's state."
    return 'this command did the work.'


def timings():
    """The last line: the total time and how it was spent. This command's own share is what the others leave, so the
    parts add up to the total (each in whole milliseconds)."""
    total = round((time.monotonic() - STATE['start']) * 1000)
    parts = [round(STATE[k] * 1000) for k in ('page', 'keychain', 'network', 'programs')]
    return '%d ms in all: this command %d ms, the page %d ms, the Keychain %d ms, the network %d ms, other programs ' \
           '%d ms' % tuple([total, max(0, total - sum(parts))] + parts)


@contextmanager
def tracing(cli, args):
    """Trace the command `args` runs: the hooks in, a first line naming it, and at the end the side that did the work.
    `cli` is the command's own module (its _page_open)."""
    STATE.clear()
    STATE.update(_fresh())
    undo = []
    try:
        for hook in (_hook_open, _hook_os_open, _hook_write, _hook_programs, lambda: _hook_page(cli), _hook_urllib,
                     _hook_net, _hook_keychain):
            undo.append(hook())
        say('switchboard %s (Switchboard %s, Python %d.%d)' % (
            program(args[:1]) or '(no command)', core.VERSION, sys.version_info[0], sys.version_info[1]))
        yield
    finally:
        for u in reversed(undo):
            u()
        say(side())
        say(timings())
        STATE.clear()
        STATE['on'] = False
