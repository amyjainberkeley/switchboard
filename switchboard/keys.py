"""Named API keys ("lanes") for the gateway, kept in the macOS Keychain under Switchboard's own service name.

What is stored where
- The key itself: one generic-password item per lane in the default (login) Keychain, service `switchboard-gateway`,
  account = the lane's name. Nothing else in Switchboard ever holds it on disk.
- The lane's record (name, kind, base URL, header to send the key in, model name map, last four characters, order):
  data/gateway-lanes.json, mode 0600. It never holds a key.

How a key reaches the Keychain without showing in the process list
`man security` (checked 2026-11-13) offers three ways to give add-generic-password a password:
- "-w password  Specify password to be added. Put at end of command to be prompted (recommended)". As an argument the
  key is in the process list for anyone to read with `ps`. The prompt reads the terminal, so it needs a person typing.
- "-X password  Specify password data to be added as a hexadecimal string": still an argument, so still visible.
- "-i  Run security in interactive mode. A prompt (security> by default) will be displayed and the user will be able
  to type commands on stdin until an EOF is encountered."
This module uses the third: it starts `/usr/bin/security -i` (no secret in its arguments) and writes one command
line, `add-generic-password -U -s switchboard-gateway -a <lane> -w <key>`, to its stdin. Only the pipe carries the
key, and a pipe's contents are not in the process list. The man page states no length limit for that line and none
for the item; to be sure nothing was cut, every add reads the item back and compares it with what was given (as
fixed-length digests), and removes it and fails if they differ. Keys are limited to 512 plain characters with no
spaces or quotes, so the line splits exactly into its words. Reading a key back
(`find-generic-password -s switchboard-gateway -a <lane> -w`) prints it on stdout, again never as an argument.
The man page also says "By default, the application which creates an item is trusted to access its data without
warning", so reads through the same /usr/bin/security do not prompt.

Rules kept here: a key is never taken from the command line (add_from_prompt() asks with getpass, which does not echo),
never written to a file, never logged, and never returned by a public function. Only _secret() returns it, for the
gateway's upstream request. Every Keychain command names this module's own service; Claude Code's own login item
is never named or read.
"""
import getpass
import hashlib
import hmac
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from . import core

SERVICE = 'switchboard-gateway'
SECURITY = '/usr/bin/security'
# \Z, not $: a $ would let a trailing newline through (and a newline would end the Keychain command line early).
NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,31}\Z')
KEY_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._~+/=-]{15,511}\Z')
MODEL_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/@~\[\]-]{0,127}\Z')
# https://platform.claude.com/docs/en/manage-claude/authentication: an API key is a "Static `sk-ant-api...` secret";
# https://platform.claude.com/docs/en/cli-sdks-libraries/cli/authentication shows an OAuth login token as `sk-ant-oat01-...`.
ANTHROPIC_KEY_PREFIX = 'sk-ant-api'
OAUTH_TOKEN_PREFIX = 'sk-ant-oat'
# Where each kind of lane sends requests, and in which header its key goes.
# - anthropic: https://platform.claude.com/docs/en/api/overview, `x-api-key` "Your API key from Console".
# - openrouter: https://openrouter.ai/docs/cookbook/coding-agents/claude-code-integration "Use the full OpenRouter API
#   URL, including the scheme: `https://openrouter.ai/api`"; its Messages API takes the "API key as bearer token in
#   Authorization header" (https://openrouter.ai/docs/api/api-reference/anthropic-messages/create-a-message).
# - custom: any endpoint that speaks Anthropic's Messages format; its base URL and header are given when it is added.
KINDS = {'anthropic': ('https://api.anthropic.com', 'x-api-key'),
         'openrouter': ('https://openrouter.ai/api', 'bearer'),
         'custom': (None, 'bearer')}
AUTHS = ('x-api-key', 'bearer')
LOOPBACK = ('127.0.0.1', 'localhost', '::1')
CACHE_SECONDS = 60
NOT_FOUND = 44            # security's exit status when no item matches (errSecItemNotFound)


def _run(argv, stdin=None):
    """Run a command; return (exit status, stdout). Its stderr and stdout are never logged or put in an error."""
    p = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=30,
                       env={'PATH': '/usr/bin:/bin', 'LANG': 'C'})
    return p.returncode, p.stdout


def _digest(text):
    return hashlib.sha256(text.encode('utf-8')).digest()


def check_name(name):
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise core.SwitchError('A lane name is 1 to 32 letters, digits, dots, dashes or underscores, '
                               'starting with a letter or digit.')
    return name


def check_key(key, kind):
    """Refuse anything that is not plainly an API key. Never puts the key in the message."""
    if not isinstance(key, str):
        raise core.SwitchError('The key must be text.')
    if key.startswith(OAUTH_TOKEN_PREFIX):
        raise core.SwitchError('That is a Claude subscription login token, not an API key. The gateway works only '
                               'with API keys.')
    if not KEY_RE.match(key):
        raise core.SwitchError('That does not look like an API key: 16 to 512 letters, digits and - _ . ~ + / =, '
                               'with no spaces.')
    if kind == 'anthropic' and not key.startswith(ANTHROPIC_KEY_PREFIX):
        raise core.SwitchError('An Anthropic API key starts with sk-ant-api. Make one in the Claude Console.')
    return key


def check_base_url(url):
    """https anywhere, or plain http to this Mac only (a key must never cross a network in clear text)."""
    parts = urlsplit(url or '')
    local = parts.scheme == 'http' and parts.hostname in LOOPBACK
    if not (parts.scheme == 'https' or local) or not parts.hostname or parts.username or parts.password \
            or parts.query or parts.fragment:
        raise core.SwitchError('The base URL must be https://host[/path] (plain http only to this Mac).')
    return url.rstrip('/')


def check_names(names):
    """A lane's model name map: the name Claude Code asks for -> the name this lane knows it by.

    Every target must name a Claude model: Anthropic "doesn't support routing Claude Code to non-Claude models
    through any gateway" (https://code.claude.com/docs/en/llm-gateway).
    """
    if names is None:
        return None
    if not isinstance(names, dict) or not names or len(names) > 100:
        raise core.SwitchError('A name map is 1 to 100 pairs of model names.')
    for asked, sent in names.items():
        if not (isinstance(asked, str) and MODEL_RE.match(asked) and isinstance(sent, str) and MODEL_RE.match(sent)):
            raise core.SwitchError('A name map pairs plain model names.')
        if 'claude' not in sent.lower():
            raise core.SwitchError('A name map may only point at Claude models.')
    return dict(names)


class Keys(object):
    """The lanes on this Mac. `runner(argv, stdin)` runs a command; tests give a fake Keychain."""

    def __init__(self, runner=None, path=None):
        self._run = runner or _run
        self._path = path
        self._lock = threading.RLock()
        self._cache = {}

    # ---------- the lane records (never a key) ----------

    def _file(self):
        return self._path or core.data_path('gateway-lanes.json')

    def _records(self):
        lanes = core.read_json(self._file()).get('lanes')
        return [dict(x) for x in lanes if isinstance(x, dict) and NAME_RE.match(str(x.get('name', '')))] \
            if isinstance(lanes, list) else []

    def _save(self, records):
        core.write_json(self._file(), {'lanes': records})

    def list(self):
        """Every lane in order: its name, kind, base URL, header, name map and the key's last four characters."""
        with self._lock:
            return [{'name': r['name'], 'kind': r.get('kind'), 'base_url': r.get('base_url'), 'auth': r.get('auth'),
                     'names': dict(r['names']) if isinstance(r.get('names'), dict) else None,
                     'last4': r.get('last4', '')} for r in self._records()]

    # ---------- the Keychain ----------

    def _keychain_add(self, name, key):
        line = 'add-generic-password -U -s %s -a %s -w %s\n' % (SERVICE, name, key)
        code, _ = self._run([SECURITY, '-i'], line)
        return code

    def _keychain_read(self, name):
        code, out = self._run([SECURITY, 'find-generic-password', '-s', SERVICE, '-a', name, '-w'], None)
        if code != 0:
            return None
        value = out[:-1] if out.endswith('\n') else out
        return value if KEY_RE.match(value) else None

    def present(self, name):
        """Is there a Keychain item for this lane? Asks for its attributes only, never the key."""
        check_name(name)
        code, _ = self._run([SECURITY, 'find-generic-password', '-s', SERVICE, '-a', name], None)
        return code == 0

    def _keychain_delete(self, name):
        code, _ = self._run([SECURITY, 'delete-generic-password', '-s', SERVICE, '-a', name], None)
        return code

    def _secret(self, name):
        """The key for one lane, for the gateway's upstream request only. Never shown, logged or sent anywhere else."""
        check_name(name)
        with self._lock:
            hit = self._cache.get(name)
            if hit and time.time() - hit[1] < CACHE_SECONDS:
                return hit[0]
        with self._lock:                             # one at a time with add/remove, so a stale key is never cached
            value = self._keychain_read(name)
            if value is None:
                raise core.SwitchError('The Keychain has no usable key for lane %s.' % name)
            self._cache[name] = (value, time.time())
        return value

    # ---------- changes ----------

    def add(self, name, key, kind='anthropic', base_url=None, auth=None, names=None):
        """Store a lane's key in the Keychain and its record in the lane list. Same name: replaced in place."""
        check_name(name)
        if kind not in KINDS:
            raise core.SwitchError('A lane is anthropic, openrouter or custom.')
        check_key(key, kind)
        default_url, default_auth = KINDS[kind]
        if kind == 'custom' and not base_url:
            raise core.SwitchError('A custom lane needs its base URL.')
        url = check_base_url(base_url or default_url)
        auth = auth or default_auth
        if auth not in AUTHS:
            raise core.SwitchError('The key goes in x-api-key or as a bearer token.')
        if names is None and kind == 'openrouter':
            from .gateway import OPENROUTER_NAMES      # the shipped table, copied into the lane so it is visible there
            names = dict(OPENROUTER_NAMES)
        names = check_names(names)
        with self._lock:
            self._cache.pop(name, None)
            old = self._keychain_read(name)              # a lane being replaced keeps its key if the new one fails
            self._keychain_add(name, key)
            back = self._keychain_read(name)
            self._cache.pop(name, None)
            if back is None or not hmac.compare_digest(_digest(back), _digest(key)):
                if old is None:
                    self._keychain_delete(name)
                elif back is None or not hmac.compare_digest(_digest(back), _digest(old)):
                    self._keychain_add(name, old)
                old = back = None
                raise core.SwitchError('The Keychain did not keep the key exactly, so nothing was changed.')
            old = back = None
            record = {'name': name, 'kind': kind, 'base_url': url, 'auth': auth, 'names': names, 'last4': key[-4:],
                      'added': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
            records = self._records()
            for i, r in enumerate(records):
                if r['name'] == name:
                    records[i] = record
                    break
            else:
                records.append(record)
            self._save(records)
        return {'name': name, 'kind': kind, 'last4': key[-4:]}

    def add_from_prompt(self, name, kind='anthropic', base_url=None, auth=None, names=None, ask=None):
        """Ask for the key without echoing it (getpass), then add it. Never takes it from the command line."""
        check_name(name)
        key = (ask or getpass.getpass)('API key for lane %s (not shown): ' % name).strip()
        return self.add(name, key, kind=kind, base_url=base_url, auth=auth, names=names)

    def remove(self, name):
        """Delete the lane's Keychain item and its record."""
        check_name(name)
        with self._lock:
            self._cache.pop(name, None)
            records = self._records()
            if not any(r['name'] == name for r in records):
                raise core.SwitchError('There is no lane named %s.' % name)
            code = self._keychain_delete(name)
            if code not in (0, NOT_FOUND):
                raise core.SwitchError('The Keychain would not delete the key for lane %s, so the lane stays.' % name)
            self._save([r for r in records if r['name'] != name])
        return {'name': name, 'removed': True}

    def reorder(self, names):
        """Put the lanes in this order (every lane named exactly once). The gateway tries them in this order."""
        with self._lock:
            records = self._records()
            have = [r['name'] for r in records]
            if not isinstance(names, (list, tuple)) or sorted(names) != sorted(have):
                raise core.SwitchError('Name every lane exactly once.')
            by_name = {r['name']: r for r in records}
            self._save([by_name[n] for n in names])
        return {'order': list(names)}


_DEFAULT = []
_DEFAULT_LOCK = threading.Lock()


def default():
    """The one Keys this process uses (the real Keychain)."""
    with _DEFAULT_LOCK:
        if not _DEFAULT:
            _DEFAULT.append(Keys())
        return _DEFAULT[0]
