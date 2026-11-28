"""What looks like a secret, and files that usually hold one: taken out of a note before it is written.

scrub() replaces each match with MARK and counts it by kind, in this order: private keys, values from the person's
own environment whose names look secret, known key shapes and webhook addresses, JSON web tokens, authorization
headers, NAME=value pairs whose name looks secret, a login inside an address, and long random-looking runs. It is best
effort, and every sentence that reports it says so.

It is bounded. A caller hands it a piece cut to twice that piece's cap. Every pattern is linear in its input (each
starts only where a word starts, or after a literal it was checked for first), the private-key scan looks at each
BEGIN once and never starts again inside a match, and the whole pass for one note shares a deadline (BUDGET_S):
past it, the rest of the piece is replaced by TOO_LONG and counted. Nothing here is ever written or printed.
"""
import bisect
import fnmatch
import os
import re
import shlex
import time
from typing import Dict, Iterable, Optional, Tuple

from . import contract

MARK = '[secret removed]'
TOO_LONG = '[left out: too long to check for secrets]'
BUDGET_S = 2.0              # seconds for the whole pass over one note
KINDS = ('private_key', 'known_value', 'key', 'jwt', 'auth_header', 'named_value', 'url_login', 'long_run')
_CHUNK = 1 << 16            # characters of whole lines checked between two looks at the deadline

# ---------- private keys: one left-to-right scan ----------

_BEGIN = '-----BEGIN '
_END = '-----END '
_BEGIN_RE = re.compile(r'-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----')     # PGP's ends BLOCK
_END_LINE_RE = re.compile(r'-----END [A-Z0-9 ]{0,40}-----')
# A key whose END is not within KEY_SPAN (cut, or never there): its header, then the lines that look like its body
# (base64, or "Proc-Type: 4,ENCRYPTED" headers), each a whole line. Each repeat starts at a newline: linear.
_BODY_RE = re.compile(r'(?:\r?\n[ \t]*(?:[A-Za-z0-9+/=]+|[A-Za-z][A-Za-z-]*:[^\n]*)[ \t]*(?=\r?\n|\Z))*')
KEY_SPAN = 64 << 10

# ---------- key shapes: (literal hints, pattern). A pattern runs only when one of its hints is in the text. ----------
# Each match goes whole, except a group named k, which stays (a webhook's host, AccountKey=).
_B = r'(?<![A-Za-z0-9])'
_KEYS = (
    (('sk-ant-', 'sk-proj-'), _B + r'sk-(?:ant|proj)-[A-Za-z0-9_\-]{10,}'),
    (('sk-',), _B + r'sk-[A-Za-z0-9]{20,}'),
    (('k_live_', 'k_test_'), _B + r'[sr]k_(?:live|test)_[A-Za-z0-9]{10,}'),
    (('ghp_', 'gho_', 'ghu_', 'ghs_', 'ghr_'), _B + r'gh[opusr]_[A-Za-z0-9_]{20,}'),
    (('github_pat_',), _B + r'github_pat_[A-Za-z0-9_]{20,}'),
    (('glpat-',), _B + r'glpat-[A-Za-z0-9_\-]{20,}'),
    (('xoxa-', 'xoxb-', 'xoxp-', 'xoxr-', 'xoxs-'), _B + r'xox[abprs]-[A-Za-z0-9\-]{10,}'),
    (('xapp-',), _B + r'xapp-[A-Za-z0-9\-]{10,}'),
    (('hf_',), _B + r'hf_[A-Za-z0-9]{20,}'),
    (('npm_',), _B + r'npm_[A-Za-z0-9]{20,}'),
    (('pypi-',), _B + r'pypi-[A-Za-z0-9_\-]{20,}'),
    (('whsec_',), _B + r'whsec_[A-Za-z0-9+/=]{20,}'),
    (('SG.',), _B + r'SG\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}'),
    (('sbp_',), _B + r'sbp_[A-Za-z0-9]{20,}'),
    (('sb_secret_',), _B + r'sb_secret_[A-Za-z0-9_\-]{10,}'),
    (('shpat_', 'shpss_'), _B + r'shp(?:at|ss)_[A-Za-z0-9]{20,}'),
    (('AKIA', 'ASIA'), _B + r'(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Za-z0-9])'),
    (('AIza',), _B + r'AIza[A-Za-z0-9_\-]{35}(?![A-Za-z0-9_\-])'),
    (('ya29.',), _B + r'ya29\.[A-Za-z0-9_\-]{20,}'),
    (('1//',), r'(?<![A-Za-z0-9/])1//[A-Za-z0-9_\-]{20,}'),
    (('AccountKey=',), r'(?P<k>AccountKey=)[A-Za-z0-9+/=]{20,}'),
    (('hooks.slack.com',), r'(?P<k>hooks\.slack\.com/)services/[A-Za-z0-9/_\-]+'),
    (('discord.com', 'discordapp.com'), r'(?P<k>discord(?:app)?\.com/)api/webhooks/[A-Za-z0-9/_\-]+'),
)
_KEYS = tuple((hints, re.compile(p)) for hints, p in _KEYS)
# A Twilio account id is 'AC' and 32 hex: taken only with 'twilio' or 'account' on its line (ids of that shape are
# common otherwise).
_TWILIO_RE = re.compile(_B + r'AC[0-9a-f]{32}(?![A-Za-z0-9])')
_TWILIO_LINE_RE = re.compile(r'(?i)twilio|account')

_JWT_RE = re.compile(r'(?<![A-Za-z0-9_\-])eyJ[A-Za-z0-9_\-]{3,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}')

# Authorization headers (hints are checked against the lower-cased text).
_AUTH = (
    (('authorization',), re.compile(r'(?i)(?P<k>\bauthorization[ \t]*:[ \t]*(?:bearer|basic|token)[ \t]+)'
                                    r'(?!\[secret removed\])[^\s"\']+')),
    (('bearer',), re.compile(r'(?i)(?P<k>\bbearer[ \t]+)[A-Za-z0-9._~+/\-=]{12,}')),
    (('x-api-key',), re.compile(r'(?i)(?P<k>\bx-api-key[ \t]*:[ \t]*)(?!\[secret removed\])[^\s"\']+')),
)

# NAME=value, NAME: value and "name": "value", when the name holds one of these words: the value goes, the name stays.
_SECRET_WORD_RE = re.compile(r'(?i)KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD|CREDENTIAL|AUTH(?!OR)|COOKIE|SESSION')
_NOT_SECRET_NAMES = frozenset(('pwd', 'oldpwd'))     # the shell's own working folder, not a password
_JSON_PAIR_RE = re.compile(r'"(?P<n>[A-Za-z_][\w.\- ]{0,63})"(?P<s>[ \t]*:[ \t]*)"(?P<dq>[^"\n]*)"')
# A name starts a word (never after ? or &: a query's value is the address rule's), and stays on its line.
_PAIR_RE = re.compile(r'(?<![\w.\-?&])(?P<n>[A-Za-z_][\w.\-]{0,63})(?P<s>[ \t]*[:=][ \t]*)'
                      r'(?:"(?P<dq>[^"\n]*)"|\'(?P<sq>[^\'\n]*)\'|(?P<v>[^\s"\',;]+))')
_PLAIN_VALUES = frozenset(('true', 'false', 'null', 'none', 'nil', 'undefined', 'yes', 'no'))
# A value that is a shell variable's name, whole, stays: $NAME (in capitals, as the environment's names are; a password
# can begin with $ and lower-case letters) or ${name}. Anything more after it goes.
_SHELL_VAR_RE = re.compile(r'\$(?:[A-Z_][A-Z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\})\Z')

# A command's flag that carries a password or a token: --password=VALUE, --password VALUE, --api-key VALUE. With a
# space, a next word that is a flag (-x) is no value. The flag's name is judged by _FLAG_WORD_RE, and one that says it
# is not the value itself (--no-password, --password-stdin, --password-file) stays.
_FLAG_RE = re.compile(r'(?<![\w\-])(?P<k>--?(?P<n>[A-Za-z][\w\-]{0,63})(?:=|[ \t]+(?!-)))'
                      r'(?:"(?P<dq>[^"\n]*)"|\'(?P<sq>[^\'\n]*)\'|(?P<v>[^\s"\']+))')
_FLAG_WORD_RE = re.compile(r'(?i)pass(?:word|wd)|token|secret|api[-_]?key|credential')
_FLAG_NOT_RE = re.compile(r'(?i)^no-|-(?:file|stdin|path|prompt|env)$')
# A login with its password after -u or --user (curl's -u user:pass): the user stays.
_USER_RE = re.compile(r'(?<![\w\-])(?P<k>(?:-u|--user)(?:=|[ \t]+)[^\s:"\'@/]{1,64}:)(?!/)(?P<v>[^\s"\'@]{1,256})')
# mysql's and mariadb's password joined to its flag (-pVALUE), on a line that names one of them: -p takes other values
# in other commands (gcc -pthread, ssh -p2222).
_JOINED_P_RE = re.compile(r'(?<![\w\-])(?P<k>-p)(?P<v>[^\s"\'\-][^\s"\']{3,255})')
_MYSQL_LINE_RE = re.compile(r'(?i)\b(?:mysql|mariadb)\w*')

# A login inside an address, and secret-named query values.
_URL_LOGIN_RE = re.compile(r'(?P<k>\b[A-Za-z][A-Za-z0-9+.\-]{0,20}://)[^\s/:@\[\]]{1,256}:[^\s/@\[\]]{1,256}@')
_QUERY_RE = re.compile(r'(?i)(?P<k>[?&](?:token|key|sig|signature|access_token|api_key|password)=)'
                       r'(?!\[secret)[^&\s#"\']+')

# Long random-looking runs.
_RUN_RE = re.compile(r'(?<![A-Za-z0-9_\-+/=])[A-Za-z0-9_\-+/=]{32,}')
_HEX_RE = re.compile(r'[0-9a-fA-F]+\Z')
_GIT_RE = re.compile(r'(?i)\b(?:commit\w*|sha\w*|rev\w*|head|merge\w*|git\w*)\b')
_WORD_SPLIT_RE = re.compile(r'[-_]')
# An ordinary word: all lower case (and digits), all upper case (and digits), or words joined in camel case.
_ORDINARY_RE = re.compile(r'(?:[a-z0-9]+|[A-Z0-9]+|[A-Z]?[a-z]+(?:[0-9]*[A-Z][a-z]+)*[0-9]*)\Z')
_UPPER_RE, _LOWER_RE, _DIGIT_RE = re.compile('[A-Z]'), re.compile('[a-z]'), re.compile('[0-9]')


def _add(counts: Dict[str, int], kind: str, n: int = 1) -> None:
    counts[kind] = counts.get(kind, 0) + n


class _Lines:
    """Which line a position is on, for rules that look at the rest of that line; each line is searched once."""

    def __init__(self, text: str):
        self.text = text
        self._nl = None
        self._seen = {}

    def has(self, pos: int, rx) -> bool:
        if self._nl is None:
            self._nl = [m.start() for m in re.finditer('\n', self.text)]
        i = bisect.bisect_left(self._nl, pos)
        key = (i, rx.pattern)
        if key not in self._seen:
            start = self._nl[i - 1] + 1 if i else 0
            end = self._nl[i] if i < len(self._nl) else len(self.text)
            self._seen[key] = rx.search(self.text, start, end) is not None
        return self._seen[key]


def _private_keys(text: str, counts: Dict[str, int], past) -> Tuple[str, bool]:
    """(text with every private key replaced, True when the deadline cut the scan short: the rest is not here)."""
    out, pos, steps, next_end = [], 0, 0, None
    i = text.find(_BEGIN)
    while i >= 0:
        steps += 1
        if steps % 4096 == 0 and past():
            out.append(text[pos:i])
            return ''.join(out), True
        m = _BEGIN_RE.match(text, i)
        if not m:
            i = text.find(_BEGIN, i + 1)
            continue
        if next_end is None or (next_end != -1 and next_end < m.end()):
            next_end = text.find(_END, m.end())     # moves forward only: the scan stays linear
        if next_end != -1 and next_end - i <= KEY_SPAN:
            e = _END_LINE_RE.match(text, next_end)
            stop = e.end() if e else next_end + len(_END)
        else:
            stop = _BODY_RE.match(text, m.end(), min(len(text), i + KEY_SPAN)).end()
        out.append(text[pos:i])
        out.append(MARK)
        _add(counts, 'private_key')
        pos = stop
        i = text.find(_BEGIN, stop)
    out.append(text[pos:])
    return ''.join(out), False


def _keep_and_mark(counts: Dict[str, int], kind: str):
    def sub(m):
        _add(counts, kind)
        return (m.groupdict().get('k') or '') + MARK
    return sub


def _value_goes(value: str) -> bool:
    v = value.strip()
    if len(v) < 4 or v.startswith('[secret') or _SHELL_VAR_RE.match(v):
        return False                # too short to be one, already gone (an unquoted value stops at its space), or a
                                    # shell variable's name
    if v.lower() in _PLAIN_VALUES or (v.isdigit() and len(v) < 8):
        return False
    return True


def _pairs(text: str, counts: Dict[str, int]) -> str:
    def secret_name(name: str) -> bool:
        return name.lower() not in _NOT_SECRET_NAMES and _SECRET_WORD_RE.search(name) is not None

    def json_sub(m):
        if not (secret_name(m.group('n')) and _value_goes(m.group('dq'))):
            return m.group(0)
        _add(counts, 'named_value')
        return '"%s"%s"%s"' % (m.group('n'), m.group('s'), MARK)

    def pair_sub(m):
        name = m.group('n')
        for group, quote in (('dq', '"'), ('sq', "'"), ('v', '')):
            value = m.group(group)
            if value is not None:
                break
        if not (secret_name(name) and _value_goes(value)):
            return m.group(0)
        _add(counts, 'named_value')
        return '%s%s%s%s%s' % (name, m.group('s'), quote, MARK, quote)

    if '"' in text:
        text = _JSON_PAIR_RE.sub(json_sub, text)
    return _PAIR_RE.sub(pair_sub, text)


def _flags(text: str, counts: Dict[str, int]) -> str:
    """A password or token given to a command as a flag (_FLAG_RE, _USER_RE, _JOINED_P_RE): the value goes, the flag
    and the user stay. A value _value_goes keeps (a shell variable's name, a short or plain word) stays."""
    def flag_sub(m):
        name = m.group('n')
        for group, quote in (('dq', '"'), ('sq', "'"), ('v', '')):
            value = m.group(group)
            if value is not None:
                break
        if not (_FLAG_WORD_RE.search(name) and not _FLAG_NOT_RE.search(name) and _value_goes(value)):
            return m.group(0)
        _add(counts, 'named_value')
        return '%s%s%s%s' % (m.group('k'), quote, MARK, quote)

    def value_sub(m):
        if not _value_goes(m.group('v')):
            return m.group(0)
        _add(counts, 'named_value')
        return m.group('k') + MARK

    low = text.lower()
    if any(h in low for h in ('pass', 'token', 'secret', 'api', 'credential')):
        text = _FLAG_RE.sub(flag_sub, text)
    if '-u' in text:                                # -u and --user
        text = _USER_RE.sub(value_sub, text)
    if '-p' in text and ('mysql' in low or 'mariadb' in low):
        lines = _Lines(text)

        def joined_sub(m):
            return value_sub(m) if lines.has(m.start(), _MYSQL_LINE_RE) else m.group(0)
        text = _JOINED_P_RE.sub(joined_sub, text)
    return text


def _has_letters_and_digits(run: str, upper_and_lower: bool) -> bool:
    up, low, digit = (_UPPER_RE.search(run) is not None, _LOWER_RE.search(run) is not None,
                      _DIGIT_RE.search(run) is not None)
    return digit and (up and low if upper_and_lower else (up or low))


def _ordinary(segment: str) -> bool:
    words = [w for w in _WORD_SPLIT_RE.split(segment) if w]
    return bool(words) and all(len(w) < 20 and _ORDINARY_RE.match(w) for w in words)


def _run_goes(run: str, git) -> bool:
    """Does one run of 32 or more [A-Za-z0-9_-+/=] go? Pure hex of 32 to 64 goes unless a git word is near (a commit);
    a list of ordinary words (a path, a long file name) stays; else 32 or more with upper, lower and a digit, or 40 or
    more with a letter and a digit, goes. git() says whether a git word is on the run's line or in its tool call."""
    if _HEX_RE.match(run):
        if len(run) <= 12:
            return False
        if len(run) <= 64:
            return not git()
        return _has_letters_and_digits(run, False)
    if all(_ordinary(s) for s in run.split('/') if s) and run.strip('/'):
        return False
    return (len(run) >= 32 and _has_letters_and_digits(run, True)) or \
        (len(run) >= 40 and _has_letters_and_digits(run, False))


def _long_runs(text: str, counts: Dict[str, int], context_git: bool) -> str:
    lines = _Lines(text)

    def sub(m):
        run = m.group(0)

        def git() -> bool:
            return context_git or lines.has(m.start(), _GIT_RE)

        parts = run.split('/')
        named = [p for p in parts if p]
        if len(parts) > 1 and (run.startswith('/') or
                               (len(named) >= 3 and 2 * sum(1 for p in named if _ordinary(p)) > len(named))):
            # A path (it starts at /, or most of its parts are ordinary words) or an address: each part is judged
            # alone, so one random-looking folder name (macOS's own temporary folders have one) keeps the rest, and
            # a commit in a link goes only when no git word is near.
            new = []
            for p in parts:
                if len(p) >= 32 and _run_goes(p, git):
                    _add(counts, 'long_run')
                    new.append(MARK)
                else:
                    new.append(p)
            return '/'.join(new)
        if _run_goes(run, git):
            _add(counts, 'long_run')
            return MARK
        return run

    return _RUN_RE.sub(sub, text)


def _line_rules(text: str, counts: Dict[str, int], context_git: bool) -> str:
    """Every rule after the private keys and the known values, on whole lines."""
    for hints, rx in _KEYS:
        if any(h in text for h in hints):
            text = rx.sub(_keep_and_mark(counts, 'key'), text)
    if 'AC' in text:
        lines = _Lines(text)

        def twilio(m):
            if not lines.has(m.start(), _TWILIO_LINE_RE):
                return m.group(0)
            _add(counts, 'key')
            return MARK
        text = _TWILIO_RE.sub(twilio, text)
    if 'eyJ' in text:
        text = _JWT_RE.sub(_keep_and_mark(counts, 'jwt'), text)
    low = text.lower()
    for hints, rx in _AUTH:
        if any(h in low for h in hints):
            text = rx.sub(_keep_and_mark(counts, 'auth_header'), text)
    if ':' in text or '=' in text:
        text = _pairs(text, counts)
    if '-' in text:
        text = _flags(text, counts)
    if '://' in text:
        text = _URL_LOGIN_RE.sub(_keep_and_mark(counts, 'url_login'), text)
    if '=' in text:
        text = _QUERY_RE.sub(_keep_and_mark(counts, 'url_login'), text)
    return _long_runs(text, counts, context_git)


def _chunks(text: str) -> Iterable[Tuple[int, int]]:
    """Spans of whole lines of about _CHUNK characters (a longer line is one span)."""
    i, n = 0, len(text)
    while i < n:
        j = min(n, i + _CHUNK)
        if j < n:
            k = text.rfind('\n', i, j)
            j = k + 1 if k >= i else (text.find('\n', j) + 1 or n)
        yield i, j
        i = j


def scrub(text: str, known: Iterable[str] = (), *, context: str = '', deadline: Optional[float] = None,
          clock=time.monotonic) -> Tuple[str, Dict[str, int]]:
    """(text with what looks like a secret replaced by MARK, {kind: count}). known: values from env_secrets(), taken
    out wherever they appear. context: the text of the tool call a result came from (a git word there keeps a commit
    id in the result). deadline: a clock() time shared by every piece of one note; past it, the rest of this piece is
    TOO_LONG and counted as 'too_long'."""
    counts: Dict[str, int] = {}
    text = text if isinstance(text, str) else str(text)
    if not text:
        return text, counts

    def past() -> bool:
        return deadline is not None and clock() >= deadline

    if past():
        return TOO_LONG, {'too_long': 1}
    text, cut = _private_keys(text, counts, past)
    for value in sorted({v for v in known if isinstance(v, str) and len(v) >= 8}, key=len, reverse=True):
        n = text.count(value)
        if n:
            text = text.replace(value, MARK)
            _add(counts, 'known_value', n)
    context_git = bool(context) and _GIT_RE.search(context) is not None
    out = []
    for i, j in _chunks(text):
        if past():
            cut = True
            break
        out.append(_line_rules(text[i:j], counts, context_git))
    if cut:
        out.append(TOO_LONG)
        _add(counts, 'too_long')
    return ''.join(out), counts


def env_secrets(env) -> Tuple[str, ...]:
    """The values, 8 characters or longer, of the names in env that contract.SECRET_ENV_RE says hold a secret,
    longest first. Kept in memory for one command; never written or printed."""
    found = set()
    for name, value in dict(env or {}).items():
        if isinstance(name, str) and isinstance(value, str) and len(value) >= 8 and \
                contract.SECRET_ENV_RE.search(name):
            found.add(value)
    return tuple(sorted(found, key=len, reverse=True))


# ---------- files that usually hold a secret ----------

_SECRET_NAMES = ('.env', '.env.*', '*.pem', '*.key', '*.p12', '*.pfx', '*.keystore', 'id_rsa*', 'id_ed25519*',
                 '.netrc', '.npmrc', '.pypirc', 'credentials*', 'auth.json', '.credentials.json', 'secrets.*')
# Every name above as one pattern, each part exactly what fnmatch compiles for that name, so a word is asked once:
# a file's text given in one command is tens of thousands of words, and a call a name took a second of its read.
_SECRET_NAME_RE = re.compile('|'.join(fnmatch.translate(name) for name in _SECRET_NAMES))
_SECRET_DIRS = frozenset(('.ssh', '.aws', '.gnupg', '.codex', '.switchboard'))
_ENV_LISTINGS = ('env', 'printenv', 'set')


def _parts(path: str):
    p = path.strip().strip('\'"') if isinstance(path, str) else ''
    return [x for x in re.split(r'[\\/]+', p) if x]


def secret_file(path: str) -> bool:
    """Is path a file that usually holds a secret (.env, a key, a login, anything under .ssh, .aws, .gnupg, .codex or
    .switchboard)? Judged by its name alone; nothing is opened."""
    parts = _parts(path)
    if not parts:
        return False
    base = parts[-1].lower()
    if _SECRET_NAME_RE.match(base):
        return True
    return any(x.lower() in _SECRET_DIRS for x in parts[:-1])


def _candidates(word: str):
    w = word.lstrip('<>|&;(').rstrip(';|&)')
    yield w
    if '=' in w:
        yield w.split('=', 1)[1]


def command_files(command: str, _inner: bool = False) -> Tuple[str, ...]:
    """The words of a shell command that name a file secret_file() names (split with shlex, never run), so the
    result of `cat .env` is left out too. A quoted inner command (`bash -lc "cat .env"`) is split once more. A
    command longer than contract.SPLIT_MAX characters is split at its blanks."""
    if not isinstance(command, str) or not command.strip():
        return ()
    if len(command) > contract.SPLIT_MAX:           # a script or a file's text in one command: as lines.shell_words()
        words = [w for w in (x.strip('\'"') for x in command.split()) if w]
    else:
        try:
            words = shlex.split(command)
        except ValueError:
            words = command.split()
    out = []
    for word in words:
        for cand in _candidates(word):
            if not _inner and ' ' in cand.strip():
                found = command_files(cand, True)
            else:
                found = (cand,) if secret_file(cand) else ()
            out.extend(f for f in found if f not in out)
    return tuple(out)


def environment_listing(command: str) -> Optional[str]:
    """'env', 'printenv' or 'set' when the command is that word alone (its output lists the environment), else None."""
    words = command.split() if isinstance(command, str) else []
    if len(words) != 1:
        return None
    word = os.path.basename(words[0])
    return word if word in _ENV_LISTINGS else None


def file_left_out(path: str) -> str:
    parts = _parts(path)
    return '[the contents of %s were left out: such files usually hold secrets]' % (parts[-1] if parts else 'a file')


def listing_left_out(word: str) -> str:
    return '[the output of %s was left out: it lists the environment]' % word
