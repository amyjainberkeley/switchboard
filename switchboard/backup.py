"""Carrying the accounts to another Mac: one protected file of their saved logins (`switchboard export` and
`switchboard import`).

The file is protected with a passphrase unless the person asks otherwise, with no cipher of our own and no
dependency: Python's standard library and the /usr/bin/openssl macOS ships (LibreSSL; 3.3.6 on macOS 26, checked
in November 2026).

1. From the passphrase (NFC, UTF-8), a random 16-byte salt and ROUNDS rounds, PBKDF2-HMAC-SHA256 (hashlib) gives 32
   bytes, one block. The cipher secret is HMAC-SHA256 of the label "switchboard-accounts cipher" under them, the HMAC
   key HMAC-SHA256 of "switchboard-accounts check". So each key costs the whole of PBKDF2's rounds: had it been asked
   for 64 bytes and the halves used, each half would be a block of its own, and a guess could be tested for half.
2. LibreSSL's `enc` takes a raw key and IV only as arguments (-K, -iv), where `ps` shows them; the one secret it
   reads from a descriptor is a passphrase (-pass fd:N). So the cipher secret goes to it that way, on a pipe of its
   own, and `-pbkdf2 -iter 1 -md sha256 -S <cipher salt>` fixes how it makes the AES-256 key and the CBC IV from
   it: PBKDF2-HMAC-SHA256(secret, cipher salt, 1 round, 48 bytes). One round is enough there: the secret is already
   256 random-looking bits, and the slow step is step 1. Nothing secret is in an argument or the environment.
3. Encrypt, then authenticate: the tag is HMAC-SHA256 under the HMAC key of every byte before it, the clear header
   and the ciphertext together. On reading, the tag is compared in constant time before openssl sees the body.
4. A passphrase check in the header (HMAC-SHA256 of a fixed text under the HMAC key, 16 bytes) tells a wrong
   passphrase from a changed body. Testing a guess against it costs the same derivation as testing it against the
   tag, so it gives a guesser nothing.
5. The last line is a plain SHA-256 of every byte before it. It is no defence (anyone can work it out again); it
   tells a damaged or cut file from a wrong passphrase before the slow derivation. Every version of the format keeps
   the first line and this last line, so a newer file is told apart from a damaged one.

The file is ASCII text, so it passes through a terminal, ssh and copy and paste unchanged:

    switchboard-accounts
    {"format": "switchboard-accounts", "version": 1, "derive": "pbkdf2-sha256", "rounds": 600000, "salt": ...}
    <the ciphertext, base64, 76 characters a line>
    tag <64 hex>
    sum <64 hex>
"""
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import time
import unicodedata

from . import core, term
from .outside import said as plain          # every value a file chose, before it is printed
from .accounts import live as live_mod

FORMAT = 'switchboard-accounts'
VERSION = 1
MAGIC = (FORMAT + '\n').encode('ascii')
OPENSSL = '/usr/bin/openssl'
ROUNDS = 600000                 # PBKDF2-HMAC-SHA256 rounds for a new file
ROUNDS_MIN = 600000             # a file asking for fewer is not one Switchboard made
ROUNDS_MAX = 3000000            # one asking for more is from a newer Switchboard: opening it would take too long
SALT_BYTES = 16
CIPHER_SALT_BYTES = 8           # openssl enc's own salt is 8 bytes
CHECK_BYTES = 16
CHECK_TEXT = b'switchboard-accounts passphrase check'
LABEL_CIPHER = b'switchboard-accounts cipher'       # step 1: the two keys made from PBKDF2's one output
LABEL_CHECK = b'switchboard-accounts check'
OPENSSL_DEADLINE = 120.0
PASSPHRASE_MIN = 8              # characters
_SUM = re.compile(rb'\nsum ([0-9a-f]{64})\n\Z')
_TAG = re.compile(rb'tag ([0-9a-f]{64})\n\Z')

# What went wrong with a file, each with its own exit code (docs/commands.md lists them).
PASSPHRASE, CHANGED, CUT, NEWER = 'passphrase', 'changed', 'cut', 'newer'
CODES = {PASSPHRASE: 3, CHANGED: 4, CUT: 5, NEWER: 6}


class FileError(Exception):
    """A file that can't be opened: kind is PASSPHRASE, CHANGED (also: not an accounts file), CUT or NEWER. str() is
    the plain line a person reads; code is the exit code."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.code = CODES[kind]


class ToolError(Exception):
    """openssl is missing, timed out or refused: nothing was written or kept."""


def _words(kind):
    return {PASSPHRASE: 'That passphrase does not open this file. Nothing was kept.',
            CHANGED: 'This file was changed or damaged after it was made, so Switchboard will not use it. Nothing '
                     'was kept.',
            CUT: 'This file is cut short: part of it is missing. Copy it again from the other Mac. Nothing was kept.',
            NEWER: 'This file was made by a newer Switchboard. Update this one (switchboard update), then try again. '
                   'Nothing was kept.'}[kind]


def fail(kind, message=None):
    return FileError(kind, message or _words(kind))


# ---------- keys ----------

def derive(passphrase, salt, rounds):
    """(cipher secret, HMAC key) from the passphrase, 32 bytes each (step 1 above)."""
    text = unicodedata.normalize('NFC', passphrase).encode('utf-8')
    dk = hashlib.pbkdf2_hmac('sha256', text, salt, rounds, 32)      # one block: all of its cost is in each key
    return (hmac.new(dk, LABEL_CIPHER, hashlib.sha256).digest(), hmac.new(dk, LABEL_CHECK, hashlib.sha256).digest())


def cipher_key_iv(secret, cipher_salt):
    """The AES-256 key and CBC IV openssl makes from the cipher secret (step 2), worked out here the same way. Only
    tests use it, to show the scheme is what this file says."""
    km = hashlib.pbkdf2_hmac('sha256', secret.hex().encode('ascii'), cipher_salt, 1, 48)
    return km[:32], km[32:]


def _check(mac_key):
    return hmac.new(mac_key, CHECK_TEXT, hashlib.sha256).digest()[:CHECK_BYTES]


def _openssl(decrypt, secret, cipher_salt, data, tool=OPENSSL):
    """openssl enc -aes-256-cbc over `data`, the cipher secret on a pipe of its own (never an argument, never the
    environment). Returns its output. Raises ToolError."""
    r, w = os.pipe()
    try:
        try:
            os.write(w, secret.hex().encode('ascii') + b'\n')      # 65 bytes: under a pipe's buffer, never blocks
        finally:
            os.close(w)
        argv = [tool, 'enc', '-d' if decrypt else '-e', '-aes-256-cbc', '-pbkdf2', '-iter', '1', '-md', 'sha256',
                '-S', cipher_salt.hex(), '-pass', 'fd:%d' % r]
        try:
            p = subprocess.run(argv, input=data, capture_output=True, pass_fds=(r,), close_fds=True,
                               env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}, timeout=OPENSSL_DEADLINE)
        except FileNotFoundError:
            raise ToolError('macOS\'s %s is not there, so the file can\'t be %s.'
                            % (tool, 'opened' if decrypt else 'made'))
        except (OSError, subprocess.SubprocessError) as e:
            raise ToolError('%s did not answer as it should (%s).' % (tool, type(e).__name__))
    finally:
        os.close(r)
    if p.returncode != 0:
        raise ToolError('%s refused (exit %d).' % (tool, p.returncode))
    return p.stdout


# ---------- writing ----------

def _layout(header, ct):
    """The bytes before the tag line: the first line, the header, the ciphertext in base64."""
    line = json.dumps(header, ensure_ascii=True, separators=(', ', ': ')).encode('ascii') + b'\n'
    return MAGIC + line + base64.encodebytes(ct)


def seal(payload, passphrase, tool=OPENSSL, rounds=ROUNDS):
    """The protected file (bytes) holding `payload` (bytes)."""
    salt, cipher_salt = secrets.token_bytes(SALT_BYTES), secrets.token_bytes(CIPHER_SALT_BYTES)
    secret, mac_key = derive(passphrase, salt, rounds)
    out = _openssl(False, secret, cipher_salt, payload, tool)
    lead = b'Salted__' + cipher_salt                         # openssl writes its salt first; the header holds it
    if not out.startswith(lead) or (len(out) - len(lead)) % 16 or len(out) == len(lead):
        raise ToolError('%s gave back something that is not what was asked for.' % tool)
    ct = out[len(lead):]
    header = {'format': FORMAT, 'version': VERSION, 'derive': 'pbkdf2-sha256', 'rounds': rounds,
              'salt': base64.b64encode(salt).decode('ascii'), 'cipher': 'aes-256-cbc',
              'cipher_salt': cipher_salt.hex(), 'check': base64.b64encode(_check(mac_key)).decode('ascii'),
              'body': len(ct)}
    signed = _layout(header, ct)
    tag = hmac.new(mac_key, signed, hashlib.sha256).hexdigest()
    head = signed + ('tag %s\n' % tag).encode('ascii')
    return head + ('sum %s\n' % hashlib.sha256(head).hexdigest()).encode('ascii')


# ---------- reading ----------

def _size(header):
    """How long a whole version-1 file with this header is, or None when the header does not say."""
    n = header.get('body') if isinstance(header, dict) else None
    if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
        return None
    b64 = 4 * ((n + 2) // 3)
    lines = (b64 + 75) // 76
    line = json.dumps(header, ensure_ascii=True, separators=(', ', ': ')).encode('ascii') + b'\n'
    return len(MAGIC) + len(line) + b64 + lines + (4 + 64 + 1) * 2


def _b64(text, size):
    try:
        raw = base64.b64decode(text.encode('ascii'), validate=True) if isinstance(text, str) else None
    except (binascii.Error, ValueError, UnicodeEncodeError):
        return None
    return raw if raw is not None and len(raw) == size else None


def read_header(data):
    """(header, ciphertext, the signed bytes, tag) of a file, checked for everything that needs no passphrase: that it
    is an accounts file, whole, not damaged, and of a version this Switchboard reads. Raises FileError."""
    if not isinstance(data, (bytes, bytearray)):
        raise fail(CHANGED)
    data = bytes(data)
    if len(data) < len(MAGIC):
        if MAGIC.startswith(data):
            raise fail(CUT)
        raise fail(CHANGED, "This isn't a Switchboard accounts file. Nothing was kept.")
    if not data.startswith(MAGIC):
        raise fail(CHANGED, "This isn't a Switchboard accounts file. Nothing was kept.")
    end = data.find(b'\n', len(MAGIC))
    if end < 0:
        raise fail(CUT)
    deep = False
    try:
        header = json.loads(data[len(MAGIC):end].decode('ascii'))
    except (ValueError, UnicodeDecodeError):
        header = None
    except RecursionError:                  # nested past Python's limit: no Switchboard writes that
        header, deep = None, True
    m = _SUM.search(data)
    whole = m is not None and hmac.compare_digest(hashlib.sha256(data[:m.start() + 1]).hexdigest().encode('ascii'),
                                                  m.group(1))
    if not whole or deep:
        size = _size(header)
        if m is None and size is not None and len(data) < size:
            raise fail(CUT)
        raise fail(CHANGED)
    # Whole as it was written: what can't be understood now is a newer version's.
    if not isinstance(header, dict) or isinstance(header.get('version'), bool) \
            or not isinstance(header.get('version'), int) or header.get('format') != FORMAT:
        raise fail(NEWER)
    if header['version'] > VERSION:
        raise fail(NEWER)
    rounds = header.get('rounds')
    if isinstance(rounds, int) and not isinstance(rounds, bool) and rounds > ROUNDS_MAX:
        raise fail(NEWER)
    if header['version'] < 1 or header.get('derive') != 'pbkdf2-sha256' or header.get('cipher') != 'aes-256-cbc' \
            or isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < ROUNDS_MIN \
            or _b64(header.get('salt'), SALT_BYTES) is None or _b64(header.get('check'), CHECK_BYTES) is None \
            or not isinstance(header.get('cipher_salt'), str) \
            or not re.fullmatch(r'[0-9a-f]{%d}' % (2 * CIPHER_SALT_BYTES), header['cipher_salt']) \
            or _size(header) != len(data) or header['body'] % 16:
        raise fail(CHANGED)
    rest = data[end + 1:m.start() + 1]                     # the base64 lines and the tag line
    t = _TAG.search(rest)
    if t is None:
        raise fail(CHANGED)
    body = rest[:t.start()]
    try:
        ct = base64.b64decode(body.replace(b'\n', b''), validate=True)
    except (binascii.Error, ValueError):
        raise fail(CHANGED)
    if len(ct) != header['body'] or base64.encodebytes(ct) != body:
        raise fail(CHANGED)
    signed = data[:end + 1] + body
    return header, ct, signed, t.group(1).decode('ascii')


def open_sealed(data, passphrase, tool=OPENSSL):
    """The payload (bytes) of a protected file. Raises FileError (nothing is decrypted unless the tag holds) or
    ToolError."""
    header, ct, signed, tag = read_header(data)
    secret, mac_key = derive(passphrase, _b64(header['salt'], SALT_BYTES), header['rounds'])
    if not hmac.compare_digest(_check(mac_key), _b64(header['check'], CHECK_BYTES)):
        raise fail(PASSPHRASE)
    if not hmac.compare_digest(hmac.new(mac_key, signed, hashlib.sha256).hexdigest(), tag):
        raise fail(CHANGED)
    try:
        return _openssl(True, secret, bytes.fromhex(header['cipher_salt']), b'Salted__' +
                        bytes.fromhex(header['cipher_salt']) + ct, tool)
    except ToolError:
        raise fail(CHANGED)                 # the tag held, so this is not damage in transit; it is not ours to use


# ---------- what the file holds ----------
#
# The payload, as JSON inside the protection (and alone, unprotected, from `export - --plain`):
#   {"schema": 1, "made_at": "2026-11-25T06:10:00Z", "made_by": "switchboard 0.2.0",
#    "accounts": [{"email", "short_name", "place", "rule", "login", "saved_at", "sign_in_ends_by", "account",
#                  "organization_id", "organization", "plan", "settings"}],
#    "keys": [{"name", "kind", "base_url", "auth", "names", "key"}]}           (keys only with --with-keys)
# "login" is the saved login as Switchboard keeps it (without the parts of Claude Code's item that belong to the Mac);
# "settings" is Claude Code's oauthAccount block for the account as it was last kept. Nothing else: no folders, no
# History, no readings, no settings of Switchboard's.

PAYLOAD_SCHEMA = 1
READ_MAX = 32 * 1024 * 1024         # bytes: an accounts file is a few kilobytes an account
RULES = ('last', 'out')
SYNCED = ('Library/Mobile Documents', 'Library/CloudStorage', 'Dropbox', 'Google Drive', 'My Drive', 'OneDrive',
          'Box', 'Box Sync', 'pCloud Drive', 'Sync', 'iCloud Drive')
# The service a folder in ~/Library/CloudStorage belongs to, by the start of its name ("Dropbox-Personal",
# "GoogleDrive-someone@mail.example", "OneDrive-SharedLibraries-Company"): the longer starts first.
CLOUD_STORAGE = (('icloud', 'iCloud Drive'), ('dropbox', 'Dropbox'), ('googledrive', 'Google Drive'),
                 ('onedrive', 'OneDrive'), ('box', 'Box'), ('pclouddrive', 'pCloud Drive'), ('pcloud', 'pCloud Drive'),
                 ('protondrive', 'Proton Drive'), ('synologydrive', 'Synology Drive'), ('nextcloud', 'Nextcloud'))


def _iso(t):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t)) if _sane(t) else None


def _sane(t):
    return isinstance(t, (int, float)) and not isinstance(t, bool) and 0 < t < 1e11


def _when(text):
    """A time the file holds, as a person reads it here: 'Nov 25 6:10 AM' (this Mac's time zone), or ''."""
    t = core.parse_iso(text) if isinstance(text, str) else None
    return '%s %s' % (term.day(t), term._hour_minute(t)) if _sane(t) else ''


def synced_folder(path, home=None):
    """The name of the service that copies `path`'s folder off the Mac, told by the path (its real path, symbolic
    links followed), or None. iCloud Drive keeps its files under ~/Library/Mobile Documents, and with "Desktop &
    Documents Folders" on it also copies ~/Desktop and ~/Documents (they then show in iCloud Drive as Desktop and
    Documents); Dropbox, Google Drive, OneDrive and others keep theirs under ~/Library/CloudStorage/<name>, or an
    older install in a folder of their name in the home folder."""
    home = os.path.realpath(home or core.home())
    folder = os.path.realpath(os.path.dirname(os.path.abspath(path)) or '.')
    if not (folder + os.sep).lower().startswith((home + os.sep).lower()):
        return None
    rel = folder[len(home):].strip(os.sep)
    low = rel.lower() + '/'
    if low.startswith('library/mobile documents/'):
        return 'iCloud Drive'
    if low.startswith('library/cloudstorage/'):
        name = rel.split(os.sep)[2] if len(rel.split(os.sep)) > 2 else 'a sync service'
        for word, said in CLOUD_STORAGE:
            if name.lower().startswith(word):
                return said
        return name.split('-')[0] or 'a sync service'
    first = rel.split(os.sep)[0].lower() if rel else ''
    for name in sorted(SYNCED[2:], key=len, reverse=True):
        n = name.lower()
        # its own name, or with more after a space, a dash or a bracket: "Dropbox (Personal)", "OneDrive - Company"
        if first == n or (first.startswith(n) and first[len(n)] in ' -(_'):
            return 'Box' if name == 'Box Sync' else name
    if first in ('desktop', 'documents'):
        cloud = os.path.join(home, 'Library', 'Mobile Documents', 'com~apple~CloudDocs', rel.split(os.sep)[0])
        if os.path.isdir(cloud):
            return 'iCloud Drive'
    return None


# ---------- the passphrase ----------

class Words(Exception):
    """The command line was wrong (exit 2): str() is the line to say, before the command's own lines."""


def _ask_hidden(prompt):
    """One line typed at this terminal without showing it. Never from standard input: that may be the file."""
    import getpass
    try:
        open('/dev/tty').close()
    except OSError:
        raise core.SwitchError('There is no terminal to ask the passphrase at. Give it in a file only you can read '
                               '(--passphrase-file PATH), or on a descriptor (--passphrase-fd N).')
    return getpass.getpass(prompt)


def _ask_yes(prompt):
    """[y/N] at this terminal; False when there is none."""
    try:
        with open('/dev/tty', 'r+') as tty:
            tty.write(prompt)
            tty.flush()
            return tty.readline().strip().lower() in ('y', 'yes')
    except OSError:
        return False


def _first_line(raw):
    text = raw.decode('utf-8')
    return text.split('\n', 1)[0].rstrip('\r')


def passphrase_from_file(path):
    """The first line of a file that only this user can read (owner-only: no group or other bits); refused otherwise,
    and when it is not a plain file of this user's."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    except OSError as e:
        raise core.SwitchError('The passphrase file %s can\'t be read (%s).' % (path, e.strerror or e))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            raise core.SwitchError('The passphrase file %s is not a file of yours. Nothing was done.' % path)
        if st.st_mode & 0o077:
            raise core.SwitchError('The passphrase file %s can be read by others. Make it yours alone first '
                                   '(chmod 600 %s). Nothing was done.' % (path, path))
        raw = os.read(fd, 4096)
    finally:
        os.close(fd)
    try:
        return _first_line(raw)
    except UnicodeDecodeError:
        raise core.SwitchError('The passphrase file %s is not text.' % path)


def passphrase_from_fd(n):
    """Everything up to the first newline (or the end) on descriptor n."""
    chunks = []
    try:
        while sum(len(c) for c in chunks) < 4096:
            got = os.read(n, 4096)
            if not got:
                break
            chunks.append(got)
            if b'\n' in got:
                break
    except OSError as e:
        raise core.SwitchError('Descriptor %d can\'t be read (%s).' % (n, e.strerror or e))
    try:
        return _first_line(b''.join(chunks))
    except UnicodeDecodeError:
        raise core.SwitchError('What came on descriptor %d is not text.' % n)


def passphrase(opts, twice):
    """The passphrase: from --passphrase-file, --passphrase-fd, or asked at this terminal (twice for a new file)."""
    if opts.get('file') is not None:
        said = passphrase_from_file(opts['file'])
    elif opts.get('fd') is not None:
        said = passphrase_from_fd(opts['fd'])
    else:
        said = _ask_hidden('Passphrase for the file (not shown): ' if twice else 'Passphrase (not shown): ')
        if twice and _ask_hidden('The same passphrase again: ') != said:
            raise core.SwitchError('The two passphrases are not the same. Nothing was written.')
    if not said:
        raise core.SwitchError('The passphrase is empty. Nothing was done.')
    if twice and len(said) < PASSPHRASE_MIN:
        raise core.SwitchError('Choose a passphrase of at least %d characters: the file holds working logins. '
                               'Nothing was written.' % PASSPHRASE_MIN)
    return said


# ---------- export ----------

EXPORT_USAGE = ('switchboard export FILE|- [--account NAME]... [--with-keys] [--passphrase-file PATH | '
                '--passphrase-fd N] [--anyway] [--plain]\n'
                'switchboard export --check FILE|- [--passphrase-file PATH | --passphrase-fd N]')
IMPORT_USAGE = 'switchboard import FILE|- [--replace] [--yes] [--passphrase-file PATH | --passphrase-fd N]'
AFTER = ('It holds working logins: delete it once the other Mac has taken it. Switchboard never deletes it.')
WAITING = 'Waiting for the accounts file on standard input: paste it, then press Control-D.'


def _options(args, flags, valued):
    """{'dest', flag: True, valued flag: [values]} from a command's words. Raises Words."""
    opts, i = {'dest': None}, 0
    while i < len(args):
        a = args[i]
        if a in flags:
            opts[a] = True
        elif a in valued:
            if i + 1 >= len(args):
                raise Words('%s needs a value.' % a)
            i += 1
            opts.setdefault(a, []).append(args[i])
        elif a == '-' or not a.startswith('-'):
            if opts['dest'] is not None:
                raise Words('Name one file.')
            opts['dest'] = a
        else:
            raise Words('Unknown word: %s.' % a)
        i += 1
    if opts['dest'] is None:
        raise Words('Name the file (or - for standard input or output).')
    pf, pd = opts.get('--passphrase-file') or [], opts.get('--passphrase-fd') or []
    if len(pf) + len(pd) > 1:
        raise Words('Give the passphrase one way: --passphrase-file or --passphrase-fd, once.')
    opts['pass'] = {'file': pf[0] if pf else None, 'fd': None}
    if pd:
        if not pd[0].isdigit():
            raise Words('--passphrase-fd needs a descriptor number, like --passphrase-fd 3.')
        opts['pass']['fd'] = int(pd[0])
    return opts


def gather(ctx, names=None, with_keys=False):
    """(payload dict, [lines about accounts left out]). Read under the locks a switch takes; nothing is written."""
    from .accounts import api as api_mod, roster, vault
    from .accounts.api import PRESENT, UNREADABLE
    rules = {}
    try:
        rules = (api_mod.auto_settings() or {}).get('rules') or {}
    except (api_mod.AccountsError, OSError, ValueError):
        pass
    out, left = [], []
    try:
        with vault.switch_lock(ctx, timeout=30.0), vault.account_lock(ctx, timeout=10.0):
            doc = vault.load(ctx)
            order = [aid for aid in doc['order'] if aid in doc['accounts']]
            if names:
                want = []
                for name in names:
                    try:
                        aid = roster.find_id(ctx, name, doc)
                    except api_mod.AccountsError:
                        _not_a_token(doc, name)
                        raise
                    if aid not in want:
                        want.append(aid)
                order = [aid for aid in order if aid in want]
            for aid in order:
                a = doc['accounts'][aid]
                email = a.get('email') or aid
                status = vault.status_of(ctx, doc, aid)
                if status in ('needs_login', 'conflict'):
                    left.append('Left out %s: it needs a fresh sign-in here.' % email)
                    continue
                r = vault.read_current(ctx, aid, fresh=True, interactive=True)
                if r.kind == UNREADABLE:
                    raise core.SwitchError("%s's saved login can't be read just now (%s). Nothing was written."
                                           % (email, r.why))
                login = live_mod.parse(r.value) if r.kind == PRESENT else None
                if login is None or not login.full_pair:
                    left.append('Left out %s: no saved login here.' % email)
                    continue
                rule = rules.get(aid)
                out.append({'email': email, 'short_name': a.get('alias') or None,
                            'place': doc['order'].index(aid) + 1, 'rule': rule if rule in RULES else None,
                            'login': vault.bound_text(r.value), 'saved_at': _iso(a.get('saved_at')),
                            'sign_in_ends_by': _iso(login.refresh_expires_ms / 1000.0)
                            if login.refresh_expires_ms else None,
                            'account': aid, 'organization_id': a.get('org_id') or None,
                            'organization': a.get('org_name') or None, 'plan': a.get('plan') or None,
                            'settings': a.get('identity') if isinstance(a.get('identity'), dict) else None})
    except vault.LockBusy:
        raise core.SwitchError('Switchboard is busy; try again in a moment. Nothing was written.')
    except vault.VaultError as e:
        raise core.SwitchError(str(e))
    except api_mod.AccountsError as e:
        raise core.SwitchError(str(e))
    payload = {'schema': PAYLOAD_SCHEMA, 'made_at': _iso(ctx.wall()), 'made_by': 'switchboard %s' % core.VERSION,
               'accounts': out}
    if with_keys:
        payload['keys'] = _keys_out()
    return payload, left


def _not_a_token(doc, name):
    """A one-year token or an API key kept as an account (accounts/bearer.py) never goes in the file: its name is
    refused in one line that says export is for sign-ins."""
    from .accounts import bearer
    bid = bearer.find_id(doc, name)
    entry = next((x for x in bearer.view(doc) if x['id'] == bid), None) if bid else None
    if entry is not None:
        raise core.SwitchError(bearer.sign_ins_only(entry, name, 'export'))


def _keys_out():
    """The API keys (keys.py), each with its record. A key that can't be read stops the export."""
    from . import lanes
    out = []
    store = lanes.store()
    for x in store.list():
        out.append({'name': x['name'], 'kind': x.get('kind'), 'base_url': x.get('base_url'), 'auth': x.get('auth'),
                    'names': x.get('names'), 'key': store._secret(x['name'])})
    return out


def export_main(args, ctx=None, out=None, err=None):
    out, err = out or sys.stdout, err or sys.stderr
    try:
        opts = _options(args, ('--with-keys', '--anyway', '--plain', '--check'),
                        ('--account', '--passphrase-file', '--passphrase-fd'))
        if opts.get('--check'):
            if len(set(opts) - {'dest', 'pass', '--check', '--passphrase-file', '--passphrase-fd'}):
                raise Words('--check takes only the file and how the passphrase comes.')
            return check_main(opts, out, err)
        if opts.get('--plain'):
            if opts['dest'] != '-':
                raise Words('--plain writes only to standard output (switchboard export - --plain): an unprotected '
                            'file of logins is never written to disk.')
            if opts['pass']['file'] is not None or opts['pass']['fd'] is not None:
                raise Words('--plain asks for no passphrase.')
    except Words as e:
        err.write('%s\n%s\n' % (e, EXPORT_USAGE))
        return 2
    from .accounts import context
    ctx = ctx or context.current()
    dest = opts['dest']
    if dest != '-':
        dest = os.path.abspath(os.path.expanduser(dest))
        if os.path.lexists(dest):
            raise core.SwitchError('%s is there already. Name another file, or remove that one first.' % dest)
        if not os.path.isdir(os.path.dirname(dest)):
            raise core.SwitchError('There is no folder %s.' % os.path.dirname(dest))
        cloud = synced_folder(dest)
        if cloud and not opts.get('--anyway'):
            raise core.SwitchError('%s is in a folder %s copies off this Mac, so the logins would leave it too. Pick '
                                   'a folder that stays on this Mac, or add --anyway.' % (dest, cloud))
    said = err if dest == '-' else out
    payload, left = gather(ctx, opts.get('--account'), bool(opts.get('--with-keys')))
    for line in left:
        err.write(line + '\n')
    if not payload['accounts'] and not payload.get('keys'):
        raise core.SwitchError('There is no account to put in the file. Nothing was written.')
    raw = (json.dumps(payload, indent=1, ensure_ascii=True) + '\n').encode('ascii')
    if opts.get('--plain'):
        err.write('Unprotected: what follows holds working logins in plain text. Keep it off disk and out of '
                  'chats.\n')
        _stdout_bytes(out, raw)
        ctx.log('Gave %d accounts on standard output, unprotected' % len(payload['accounts']))
        return 0
    words = passphrase(opts['pass'], twice=True)
    try:
        data = seal(raw, words)
    except ToolError as e:
        raise core.SwitchError('%s Nothing was written.' % e)
    finally:
        words = raw = None              # noqa: F841 - nothing here keeps them after the file is made
    if dest == '-':
        _stdout_bytes(out, data)
    else:
        core.write_bytes(dest, data, mode=0o600)
    n, k = len(payload['accounts']), len(payload.get('keys') or [])
    what = '%d account%s' % (n, '' if n == 1 else 's') + (' and %d key%s' % (k, '' if k == 1 else 's') if k else '')
    said.write('Wrote %s to %s, protected with your passphrase.\n' % (what, 'standard output' if dest == '-' else dest))
    said.write(AFTER + '\n')
    ctx.log('Wrote %s to a protected file' % what)
    return 0


def _stdout_bytes(out, data):
    buf = getattr(out, 'buffer', None)
    if buf is not None:
        out.flush()
        buf.write(data)
        buf.flush()
    else:
        out.write(data.decode('ascii'))
        out.flush()


# ---------- reading a file in ----------

def _terminal_in():
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _read_source(src, opts, err=None):
    """The bytes of FILE, or of standard input for '-' (at a terminal, with nothing piped in, it first says on `err`
    what it waits for)."""
    if src == '-':
        if opts['pass']['fd'] == 0:
            raise Words('Standard input holds the file, so the passphrase must come another way.')
        if err is not None and _terminal_in():
            err.write(WAITING + '\n')
            err.flush()
        data = sys.stdin.buffer.read(READ_MAX + 1)
    else:
        try:
            with open(os.path.expanduser(src), 'rb') as f:
                data = f.read(READ_MAX + 1)
        except OSError as e:
            raise core.SwitchError('%s can\'t be read (%s).' % (src, e.strerror or e))
    if len(data) > READ_MAX:
        raise core.SwitchError("This is far larger than an accounts file; it isn't one. Nothing was kept.")
    return data


def _payload(raw):
    """The payload dict of an opened file. Raises FileError: NEWER for a later schema, CHANGED for one that can't be
    read as this one (the tag held, so it was written that way)."""
    try:
        doc = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise fail(CHANGED)
    if not isinstance(doc, dict) or isinstance(doc.get('schema'), bool) or not isinstance(doc.get('schema'), int):
        raise fail(CHANGED)
    if doc['schema'] > PAYLOAD_SCHEMA:
        raise fail(NEWER)
    accounts = doc.get('accounts')
    keys = doc.get('keys', [])
    if not isinstance(accounts, list) or not isinstance(keys, list) \
            or not all(isinstance(a, dict) and isinstance(a.get('email'), str) and isinstance(a.get('login'), str)
                       for a in accounts) \
            or not all(isinstance(k, dict) and isinstance(k.get('name'), str) and isinstance(k.get('key'), str)
                       for k in keys):
        raise fail(CHANGED)
    return doc


def open_file(data, opts):
    """(payload dict) of a protected file, the passphrase asked once. Raises FileError or SwitchError."""
    read_header(data)                       # whole, ours, not newer: said before a passphrase is asked for
    words = passphrase(opts['pass'], twice=False)
    try:
        raw = open_sealed(data, words)
    except ToolError as e:
        raise core.SwitchError('%s Nothing was kept.' % e)
    finally:
        words = None                        # noqa: F841
    return _payload(raw)


def check_main(opts, out, err):
    """`switchboard export --check FILE`: whether the file is whole and opens, and what it holds. Keeps nothing."""
    try:
        doc = open_file(_read_source(opts['dest'], opts, err), opts)
    except Words as e:
        err.write('%s\n%s\n' % (e, EXPORT_USAGE))
        return 2
    except FileError as e:
        err.write('%s\n' % e)
        return e.code
    n, keys = len(doc['accounts']), doc.get('keys') or []
    made = _when(doc.get('made_at'))
    out.write('The file is whole and opens with that passphrase%s. It holds %d account%s%s:\n'
              % (' (made %s)' % made if made else '', n, '' if n == 1 else 's',
                 ' and %d key%s' % (len(keys), '' if len(keys) == 1 else 's') if keys else ''))
    for a in doc['accounts']:
        out.write('  %s%s\n' % (plain(a['email']), _saved_words(a)))
    for k in keys:
        out.write('  key %s (ends in %s)\n' % (plain(k['name']), plain(k['key'][-4:])))
    out.write('Nothing was kept.\n')
    return 0


def _saved_words(a):
    """', login saved Nov 25 6:10 AM, sign-in ends by Dec 18 4:53 AM' for an account in the file."""
    parts = []
    if _when(a.get('saved_at')):
        parts.append('login saved %s' % _when(a['saved_at']))
    if _when(a.get('sign_in_ends_by')):
        parts.append('sign-in ends by %s' % _when(a['sign_in_ends_by']))
    return (', ' + ', '.join(parts)) if parts else ''


# What import does with each account of the file, and the words for it.
ADD, REPLACE = 'add', 'replace'
SKIP_WORDS = {'same': 'skip: it is here already',
              'newer_here': 'skip: the login here is newer',
              'older_here': 'skip: the login here is older; --replace takes the newer one from the file',
              'unknown': "skip: which login is newer can't be told, so the one here stays",
              'broken_here': 'skip: the login here does not work; --replace takes the one from the file',
              'in_use': 'skip: Claude Code is on it here',
              'unreadable': "skip: the login here can't be read just now",
              'not_login': 'skip: it holds no login that can be kept',
              'other_owner': "skip: its login is another account's",
              'no_owner': 'skip: it does not say whose account it is',
              'odd_email': 'skip: its email is not one an account can have',
              'odd_id': 'skip: it names its account in a way an account is never named',
              'two_emails': 'skip: the file names two different emails for it',
              'unproved_expired': "skip: --replace takes only a login Anthropic says is this account's, and its access "
                                  "token has run out, so Anthropic can't be asked",
              'unproved': "skip: --replace takes only a login Anthropic says is this account's, and Anthropic could "
                          "not be asked just now"}

# What a file may say, checked before any of it names a Keychain item, a file or a line of `security -i`: an id is
# letters, digits, dots, dashes and underscores (an id kept here may add "/<organization>"); an email has one @, no
# space, no double quote or backslash, and no control or format character (a bidi mark is one).
_ID = re.compile(r'[A-Za-z0-9._-]{1,80}')
_EMAIL = re.compile(r'[^\s"\\@]{1,64}@[^\s"\\@]{1,190}')
# The keys of Claude Code's own oauthAccount block that are kept from a file, each a short string or a yes/no.
SETTINGS_TEXT = ('accountUuid', 'emailAddress', 'organizationUuid', 'organizationName', 'displayName',
                 'organizationRole', 'workspaceRole', 'billingType', 'organizationType', 'accountCreatedAt',
                 'subscriptionCreatedAt')
SETTINGS_FLAGS = ('hasExtraUsageEnabled',)
SETTING_MAX = 200                   # characters


def plain_id(text):
    """True for an account or organization id this Mac could have made."""
    if not isinstance(text, str):
        return False
    parts = text.split('/')
    return len(parts) <= 2 and all(_ID.fullmatch(p) for p in parts)


def plain_email(text):
    """True for an email an account can have (and that can be shown and kept as it is)."""
    return isinstance(text, str) and len(text) <= 254 and _EMAIL.fullmatch(text) is not None and \
        not any(unicodedata.category(c)[0] == 'C' for c in text)


def _settings_kept(block):
    """The file's settings block as it is kept: only the known keys, each a string of at most SETTING_MAX characters
    with no control character, or a yes/no."""
    out = {}
    for k in SETTINGS_TEXT:
        v = block.get(k) if isinstance(block, dict) else None
        if isinstance(v, str) and len(v) <= SETTING_MAX and not any(unicodedata.category(c)[0] == 'C' for c in v):
            out[k] = v
    for k in SETTINGS_FLAGS:
        if isinstance(block, dict) and isinstance(block.get(k), bool):
            out[k] = block[k]
    return out


def _shape(item):
    """None when every name in an account of the file is of a shape this Mac can use, else a key of SKIP_WORDS."""
    block = item.get('settings') if isinstance(item.get('settings'), dict) else {}
    if not plain_email(item.get('email')) or ('emailAddress' in block and not plain_email(block['emailAddress'])):
        return 'odd_email'
    for v in (block.get('accountUuid'), block.get('organizationUuid'), item.get('account'),
              item.get('organization_id')):
        if v is not None and not plain_id(v):
            return 'odd_id'
    if 'emailAddress' in block and block['emailAddress'] != item['email']:
        return 'two_emails'
    return None


def _incoming(item, doc):
    """(account id here, Login, settings block) for an account of the file, keyed the way this Mac keys it. The
    account has passed _shape."""
    from .accounts import vault
    block = _settings_kept(item.get('settings'))
    uuid, email, org = live_mod.label(block)
    if not uuid:
        aid = item.get('account') if isinstance(item.get('account'), str) else ''
        uuid, org = aid.split('/')[0], item.get('organization_id') or ''
        block = dict(block, accountUuid=uuid, emailAddress=item['email'], organizationUuid=org or None)
    return (vault.account_key(doc, uuid, org) if uuid else None), live_mod.parse(item['login']), block


def decide(login, stored, in_use, replace):
    """What to do with one incoming login: ADD, REPLACE or a key of SKIP_WORDS. `stored` is the Read of the saved login
    here (None: this Mac does not keep the account). Uses the tree's one comparison of two logins (live.compare) and
    never puts an older login, or one whose age can't be told, over the one here."""
    from .accounts.api import ABSENT, PRESENT
    from .accounts import vault
    if not login.full_pair:
        return 'not_login'
    if in_use:
        return 'in_use'
    if stored is None or stored.kind == ABSENT:
        return ADD
    if stored.kind != PRESENT:
        return 'unreadable'
    here = live_mod.parse(stored.value)
    if not here.full_pair:
        return REPLACE if replace else 'broken_here'      # nothing that works is here to be newer
    if vault.same_saved(stored.value, login.text):
        return 'same'
    verdict = live_mod.compare(login, here)
    if verdict == 'same':
        return 'same'
    if verdict == 'older':
        return 'newer_here'
    if verdict == 'newer':
        return REPLACE if replace else 'older_here'
    return 'unknown'


def _in_use_ids(ctx, doc):
    """The ids the login Claude Code is on may be: its proven owner, and the account its settings name."""
    from .accounts import roster, vault
    ids = set()
    try:
        live = roster.live_view(ctx)
        if live.account_id:
            ids.add(live.account_id)
    except Exception as e:  # noqa: BLE001 - not known: its settings still name it below
        ctx.log('import: the live login could not be told (%s)' % type(e).__name__)
    r, block, _ = live_mod.config_identity(ctx)
    uuid, _, org = live_mod.label(block)
    if uuid:
        ids.add(vault.account_key(doc, uuid, org))
    return ids


def plan(ctx, payload, replace):
    """[(item, account id, action)] for every account of the file, in the file's order. Reads only."""
    from .accounts import identity, vault
    from .accounts.api import ABSENT, Read
    doc = vault.load(ctx)
    in_use = _in_use_ids(ctx, doc)
    rows = []
    for item in sorted(payload['accounts'], key=lambda x: x.get('place') if isinstance(x.get('place'), int) else 1e9):
        odd = _shape(item)
        if odd:
            rows.append((item, None, odd))
            continue
        aid, login, _ = _incoming(item, doc)
        if not aid:
            rows.append((item, None, 'no_owner'))
            continue
        p = identity.offline(login, doc)
        if p.account_id and p.account_id != aid:
            rows.append((item, aid, 'other_owner'))
            continue
        stored = vault.read_current(ctx, aid, fresh=True, interactive=True) if aid in doc['accounts'] else Read(ABSENT)
        action = decide(login, stored, aid in in_use, replace)
        if action == REPLACE and _expired(ctx, login):
            action = 'unproved_expired'
        rows.append((item, aid, action))
    return rows


def _expired(ctx, login):
    """True when Anthropic can't be asked whose this login is: its access token is gone or has run out."""
    return not login.has_access or login.expired(ctx.wall())


def _proved(ctx, rows):
    """{account id: 'profile' | 'other_owner' | None}: Anthropic's own answer about whose each login is, asked with its
    access token while that has not expired (it spends nothing). None: not asked, or no answer."""
    from .accounts import identity
    out = {}
    for item, aid, action in rows:
        if action not in (ADD, REPLACE):
            continue
        login = live_mod.parse(item['login'])
        out[aid] = None
        if _expired(ctx, login):
            continue
        ans = identity.lookup_profile(ctx, login.access_token)
        if ans is None or ans.status != 'resolved':
            continue
        uuid, org = aid.split('/')[0], (aid.split('/', 1)[1] if '/' in aid else '')
        block = _settings_kept(item.get('settings'))
        org = org or live_mod.label(block)[2]
        if ans.uuid != uuid or (org and ans.org and ans.org != org):
            out[aid] = 'other_owner'
        else:
            out[aid] = 'profile'
    return out


def keep(ctx, rows, proved, replace):
    """Keep what `rows` says, under the switch and account locks, each account decided again there with the same
    `replace` (another import, a switch or a refresh may have come in between). Returns [(item, account id, what was done)]: ADD, REPLACE, a key of
    SKIP_WORDS, or 'failed:<why>'."""
    from .accounts import roster, vault
    from .accounts.api import ABSENT, Read
    done = []
    try:
        with vault.switch_lock(ctx, timeout=30.0), vault.account_lock(ctx, timeout=10.0):
            doc = vault.load(ctx)
            in_use = _in_use_ids(ctx, doc)
            now = ctx.wall()
            for item, aid, action in rows:
                if action not in (ADD, REPLACE):
                    continue
                if proved.get(aid) == 'other_owner':
                    done.append((item, aid, 'other_owner'))
                    continue
                login = live_mod.parse(item['login'])
                held = aid in doc['accounts']
                stored = vault.read_current(ctx, aid, fresh=True, interactive=True) if held else Read(ABSENT)
                now_action = decide(login, stored, aid in in_use, replace)
                if now_action == REPLACE and proved.get(aid) != 'profile':
                    now_action = 'unproved_expired' if _expired(ctx, login) else 'unproved'   # never on its word
                if now_action not in (ADD, REPLACE):
                    done.append((item, aid, now_action))
                    continue
                got = vault.save_login(ctx, aid, login.text, old=stored)
                if not got.ok:
                    done.append((item, aid, 'failed:%s' % got.why))
                    continue
                if held:
                    vault.note_login(doc['accounts'][aid], login, proved.get(aid), now)
                else:
                    _, _, block = _incoming(item, doc)
                    a = vault.new_account(doc, aid, block, login, proved.get(aid), now)
                    a['email'] = item['email']                  # the one the person was shown (_shape: the same)
                    alias = item.get('short_name')
                    if isinstance(alias, str) and roster._ALIAS.fullmatch(alias) and not alias.isdigit() \
                            and alias not in doc['accounts'] \
                            and not any((b.get('alias') or '') == alias for b in doc['accounts'].values()):
                        a['alias'] = alias
                doc['removed'].pop(aid, None)
                done.append((item, aid, now_action))
            vault.save(ctx, doc)
    except vault.LockBusy:
        raise core.SwitchError('Switchboard is busy; try again in a moment. Nothing was kept.')
    except vault.VaultError as e:
        raise core.SwitchError(str(e))
    roster.forget_live(ctx)
    return done


def _rules_in(ctx, done):
    """The rule in auto-switch each newly added account had on the other Mac."""
    from .accounts import api as api_mod, autoloop
    for item, aid, what in done:
        if what == ADD and item.get('rule') in RULES:
            try:
                autoloop.save_settings(rule=(aid, item['rule']))
            except (api_mod.AccountsError, OSError, ValueError) as e:
                ctx.log('import: the rule of %s was not set (%s)' % (plain(item['email']), type(e).__name__))


def _check_works(ctx, done, proved):
    """{account id: words} for each account kept: Anthropic's answer said whose it is, or a renewal through the
    refresh transaction (only when its access has run out) says it works or that it needs a fresh sign-in."""
    from .accounts import tokens
    said = {}
    for item, aid, what in done:
        if what not in (ADD, REPLACE):
            continue
        if proved.get(aid) == 'profile':
            said[aid] = 'it works'
            continue
        res = tokens.refresh(ctx, aid, budget_s=60.0, reason='import', when_due=False)
        if res.outcome in ('refreshed', 'adopted'):
            said[aid] = 'it works'
        elif res.outcome == 'permanent':
            said[aid] = 'Anthropic refused its login: it needs a fresh sign-in'
        else:
            said[aid] = 'not checked yet; it is checked when it is next renewed'
    return said


def _keys_plan(payload):
    from . import lanes
    from .keys import NAME_RE
    have = set(x['name'] for x in lanes.lanes())
    return [(k, 'odd' if not NAME_RE.match(k['name']) else 'same' if k['name'] in have else ADD)
            for k in payload.get('keys') or []]


def _keys_in(ctx, rows):
    from . import lanes
    out = []
    for k, action in rows:
        if action != ADD:
            out.append((k, action))
            continue
        try:
            lanes.store().add(k['name'], k['key'], kind=k.get('kind') or 'anthropic', base_url=k.get('base_url'),
                              auth=k.get('auth'), names=k.get('names'))
            out.append((k, ADD))
        except core.SwitchError as e:
            out.append((k, 'failed:%s' % e))
    return out


def import_main(args, ctx=None, out=None, err=None):
    out, err = out or sys.stdout, err or sys.stderr
    try:
        opts = _options(args, ('--replace', '--yes'), ('--passphrase-file', '--passphrase-fd'))
        data = _read_source(opts['dest'], opts, err)
    except Words as e:
        err.write('%s\n%s\n' % (e, IMPORT_USAGE))
        return 2
    from .accounts import context, roster
    ctx = ctx or context.current()
    try:
        roster._refuse_inside_run(ctx)
    except Exception as e:  # noqa: BLE001 - AccountsError: one sentence
        raise core.SwitchError(str(e))
    try:
        payload = open_file(data, opts)
    except FileError as e:
        err.write('%s\n' % e)
        return e.code
    data = None
    rows = plan(ctx, payload, bool(opts.get('--replace')))
    keys = _keys_plan(payload)
    made = _when(payload.get('made_at'))
    n = len(rows)
    out.write('The file holds %d account%s%s:\n' % (n, '' if n == 1 else 's', ' (made %s)' % made if made else ''))
    for item, aid, action in rows:
        out.write('  %s%s: %s\n' % (plain(item['email']), _saved_words(item),
                                    {ADD: 'add', REPLACE: 'replace the older login here'}.get(action) or
                                    SKIP_WORDS[action]))
    for k, action in keys:
        out.write('  key %s (ends in %s): %s\n' % (plain(k['name']), plain(k['key'][-4:]),
                                                 'add' if action == ADD else 'skip: a key of that name is here'
                                                 if action == 'same' else 'skip: a key is never named that way'))
    todo = [r for r in rows if r[2] in (ADD, REPLACE)] + [k for k in keys if k[1] == ADD]
    if not todo:
        out.write('Nothing to keep.\n')
        return 0
    if not opts.get('--yes'):
        if not _ask_yes('Keep %d of them? [y/N] ' % len(todo)):
            out.write('Nothing was kept.\n')
            return 1
    proved = _proved(ctx, rows)
    done = keep(ctx, rows, proved, bool(opts.get('--replace')))
    _rules_in(ctx, done)
    works = _check_works(ctx, done, proved)
    failed = False
    for item, aid, what in done:
        if what == ADD:
            out.write('Added %s: %s.\n' % (plain(item['email']), works.get(aid)))
        elif what == REPLACE:
            out.write('Replaced the older login of %s: %s.\n' % (plain(item['email']), works.get(aid)))
        elif what.startswith('failed:'):
            failed = True
            from .leftovers import keychain_said
            out.write('Could not keep %s: %s.\n' % (plain(item['email']), keychain_said(what[7:])))
        else:
            out.write('Did not keep %s (%s).\n' % (plain(item['email']), SKIP_WORDS[what].split(': ', 1)[-1]))
    for k, what in _keys_in(ctx, keys):
        if what == ADD:
            out.write('Added the key %s (ends in %s).\n' % (plain(k['name']), plain(k['key'][-4:])))
        elif what.startswith('failed:'):
            failed = True
            out.write('Could not add the key %s: %s\n' % (plain(k['name']), what[7:]))
    ctx.log('Took %d accounts from an accounts file' % len([d for d in done if d[2] in (ADD, REPLACE)]))
    return 1 if failed else 0


def main(args):
    """`switchboard export ...` and `switchboard import ...`."""
    from .accounts.api import AccountsError
    try:
        if args[0] == 'export':
            return export_main(args[1:])
        return import_main(args[1:])
    except AccountsError as e:
        raise core.SwitchError(str(e))
