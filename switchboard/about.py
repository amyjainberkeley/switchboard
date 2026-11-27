"""`switchboard about`: what this install of Switchboard is, where it put things, what runs, what leaves the Mac and how
to take it out again. Every fact is checked at the moment it is asked for; nothing in the block is a fixed claim.

  Switchboard 0.2.0 (d3da1a4)  MIT  github.com/<owner>/switchboard

    Verified    Homebrew checked the release file against its SHA-256 before anything ran
    Signed      Switchboard Links: Developer ID (A1B2C3D4E5), notarized by Apple
    Runs as     you: no administrator password was asked for or used; every file it added is yours
    Installed   ~/.switchboard   /opt/homebrew/bin/switchboard and sb   /Applications/Switchboard.app
                ~/Applications/Switchboard Links.app
    Running     the menu bar item, and the page on 127.0.0.1:7799 (this Mac only); both start at login
    Program     214 files, unchanged since install (checked against ~/.switchboard/app/SHA256SUMS)
    Sign-ins    2 accounts, each login kept in your macOS Keychain
    Privacy     no telemetry; sends nothing of yours off this Mac except to Anthropic
                and to providers you add
                the look for a newer version is off
    Downloaded  the release from github.com (by Homebrew), PyObjC for the menu bar from pypi.org
    Remove      brew uninstall switchboard   (--zap also removes your accounts)

  Type switchboard to see your accounts.

Where each line comes from:
- The head: the version and commit of the program folder, read as `switchboard version` and `switchboard update` read
  them (newer.version_in); the licence from the LICENSE the installer copies beside the program; the repository only
  when the install knows it: from the installer's address in app/source (the address `switchboard update` fetches
  again), or, for Homebrew, from the REPO line of the install.sh in Homebrew's copy of the release.
- Verified, or Source when nothing was checked: app/source (newer.source). Homebrew downloads the release's tag and
  checks it against the cask's SHA-256 before its installer step runs (packaging/README.md); the cask Homebrew kept
  for this install (<prefix>/Caskroom/switchboard/.metadata) is read for that checksum, and a cask that says
  `sha256 :no_check` is said as no checksum. The one-line install takes the newest code on main with no checksum.
- Signed: macOS itself, asked about each app this install has: `/usr/bin/codesign -dv --verbose=2 APP` (it answers
  on standard error: Authority=, TeamIdentifier=, Signature=adhoc) and, for an app signed with an identity,
  `/usr/sbin/spctl --assess --type execute -vv APP` (source=Notarized Developer ID, source=Developer ID, or
  rejected). TOOL_S seconds each at most. The program that runs them is a parameter (run), so tests never start them.
- Runs as: install.sh refuses to run as root, and puts Switchboard.app in /Applications only when it can be written
  without a password (menubar_wrap.place); checked here by the owner of every file the install added.
- Installed: what is there now, from the paths the installer uses (linkapp, menubar_wrap, the command's links).
- Running: the page answers on its port as this data folder's page (server.py listens on 127.0.0.1 only), the menu
  bar item holds its lock (menubar.running_pid), and each background job's property list is there and starts at load.
- Program: SHA256SUMS, written by install.sh beside the program from the files it has just put in place
  (write_manifest), in the format of `shasum -a 256`, so `cd ~/.switchboard/app && shasum -a 256 -c SHA256SUMS` checks
  it without Switchboard. It tells a file changed, missing or added since install from one that is not; it is not a
  signature, and says nothing of a change made together with the list.
- Sign-ins, Privacy: the README's Privacy section, with what this install has turned on: the accounts in
  accounts.json, recovery copies in files (the Keychain refused them), providers added, keys for other hosts, and
  whether the look for a newer version is on.
- Downloaded: what the installer fetched for this install, from the hosts install.sh names: the program (ARCHIVE_URL
  on github.com, or Homebrew's download of the release's tag from GitHub), Switchboard's own Python (PBS_URL on
  github.com), PyObjC for the menu bar (pip from pypi.org); each only when app/source and runtime/python.json say this
  install has it and its folder is there.
- Remove: the uninstaller for the way this copy was installed. With --all (Homebrew's --zap runs the same), every
  Keychain item of Switchboard's own goes: by the program (`tidy --all`), or by uninstall.sh's own loop over the same
  services when the program can't start.

The installer ends with this block: `switchboard about --done SECONDS [--as COMMAND] [--setup-now] [--update]`, the
options it alone gives (not in the help, as `tidy` is not). --done ends the block with "Done in N s." and the next
step ("Next: switchboard", since `switchboard` alone runs the first run while no account is kept); --as is the
command to type (sb when the installer added it); --setup-now leaves the next step out, since the first run starts
right after; --update prints the short form an update ends with (the head, Program, Running). With --done it waits
up to MENUBAR_WAIT_S for a menu bar item whose job was just started, and it always exits 0 unless its words are
wrong: the block never fails an install.
"""
import hashlib
import json
import os
import plistlib
import re
import subprocess
import sys
import textwrap
import time
from urllib.parse import urlparse

from . import core

USAGE = 'switchboard about [--json]'
MANIFEST = 'SHA256SUMS'         # in the program folder (<data>/app), written by install.sh (write_manifest)
NOT_PROGRAM = ('__pycache__',)  # folders never part of the program (bytecode; every launcher runs Python with -B)
NOT_FILES = ('.DS_Store',)      # Finder writes this into a folder it shows; never part of the program
CODESIGN = '/usr/bin/codesign'  # by full path: a program of that name earlier on the PATH never answers for macOS
SPCTL = '/usr/sbin/spctl'
TOOL_S = 3.0                    # each call to codesign or spctl, at most
MENUBAR_WAIT_S = 5.0            # after an install, how long the menu bar item whose job just started is waited for
WIDTH = 100                     # characters a line of the block takes at most
LABEL_W = 12                    # the labels' column, after two spaces
SHOW = 3                        # changed, missing or added files named, at most; the rest are counted
SCHEMA = 1                      # of --json


def _short(path):
    """A path under the home folder as ~/..., as the installer says it."""
    h, p = os.path.normpath(core.home()), os.path.normpath(path)
    return '~' + p[len(h):] if p == h or p.startswith(h + os.sep) else p


def _plural(n, word, many=None):
    return '%d %s' % (n, word if n == 1 else many or word + 's')


def _names(names, total=None):
    """'a, b, c, +2 more'."""
    shown = list(names[:SHOW])
    more = (total if total is not None else len(names)) - len(shown)
    return ', '.join(shown) + (', +%d more' % more if more > 0 else '')


# ---------- the program's files: the list install.sh writes, and the files now ----------

def program_folder():
    """The folder of the program that runs (<data>/app for an install; a clone run from where it is otherwise)."""
    return os.path.dirname(core.PACKAGE)


def program_files(folder):
    """Every file of the program under folder, as sorted relative paths with '/', SHA256SUMS itself left out, and
    bytecode and Finder's notes too. A name with a line break can't be one line of the list: it is never a program
    file, so it is counted as added."""
    out = []
    for top, dirs, names in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d not in NOT_PROGRAM)
        for name in names:
            if name in NOT_FILES:
                continue
            rel = os.path.relpath(os.path.join(top, name), folder).replace(os.sep, '/')
            if rel != MANIFEST:
                out.append(rel)
    return sorted(out)


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 16), b''):
            h.update(block)
    return h.hexdigest()


def write_manifest(folder):
    """SHA256SUMS in folder: one line per program file, `<sha256>  <path>`, as `shasum -a 256` writes it, so `shasum
    -a 256 -c SHA256SUMS` in that folder checks it too. Written whole or not at all. Returns how many files it lists.
    install.sh runs this on the new copy of the program, before it takes the place of the old one."""
    rows = ['%s  %s\n' % (digest(os.path.join(folder, rel)), rel) for rel in program_files(folder) if '\n' not in rel]
    tmp = os.path.join(folder, '.%s.%d' % (MANIFEST, os.getpid()))
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(''.join(rows))
    os.chmod(tmp, 0o644)        # it holds no secret: names and checksums of the program's own files
    os.replace(tmp, os.path.join(folder, MANIFEST))
    return len(rows)


def read_manifest(folder):
    """{path: sha256} from folder's SHA256SUMS; None when there is none; 'unreadable' when it is not such a list."""
    try:
        with open(os.path.join(folder, MANIFEST), encoding='utf-8') as f:
            text = f.read()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        return 'unreadable'
    out = {}
    for line in text.splitlines():
        m = re.match(r'^([0-9a-f]{64}) [ *](.+)$', line)
        if not m:
            return 'unreadable'
        out[m.group(2)] = m.group(1)
    return out


def check_program(folder):
    """The program's files now, against the list made at install: {'state': 'unchanged', 'changed' (some file
    differs, is missing or was added), 'none' (no list: an install made before the list was written, or a copy the
    installer did not put in place) or 'unreadable'; 'manifest': its path; 'files': how many the list names;
    'changed', 'missing', 'added': paths}."""
    out = {'state': 'none', 'manifest': os.path.join(folder, MANIFEST), 'files': 0, 'changed': [], 'missing': [],
           'added': []}
    listed = read_manifest(folder)
    if listed is None or listed == 'unreadable':
        out['state'] = listed or 'none'
        return out
    now = program_files(folder)
    out['files'] = len(listed)
    for rel in sorted(listed):
        path = os.path.join(folder, rel)
        if not os.path.isfile(path):
            out['missing'].append(rel)
            continue
        try:
            same = digest(path) == listed[rel]
        except OSError:
            same = False
        if not same:
            out['changed'].append(rel)
    out['added'] = [rel for rel in now if rel not in listed]
    out['state'] = 'changed' if out['changed'] or out['missing'] or out['added'] else 'unchanged'
    return out


def program_words(p, folder, fix):
    """The Program line's words: [lines]."""
    where = _short(p['manifest'])
    if p['state'] == 'unchanged':
        return ['%s, unchanged since install (checked against %s)' % (_plural(p['files'], 'file'), where)]
    if p['state'] == 'none':
        if os.path.realpath(folder) == os.path.realpath(core.data_path('app')):
            return ['no list of its files to check against: installed by a version before this one']
        return ['no list of its files to check against: this copy was not put in place by the installer']
    if p['state'] == 'unreadable':
        return ['%s is not a list of checksums, so nothing could be checked' % where, fix]
    out = []
    if p['changed']:
        out.append('%s of %d changed since install: %s' % (_plural(len(p['changed']), 'file'), p['files'],
                                                          _names(p['changed'])))
    if p['missing']:
        out.append('%s missing: %s' % (_plural(len(p['missing']), 'file'), _names(p['missing'])))
    if p['added']:
        out.append('%s added since install: %s' % (_plural(len(p['added']), 'file'), _names(p['added'])))
    return out + [fix]


def repair_line(how):
    """How to put the program's files back as they were installed."""
    if how == 'homebrew':
        return 'Put them back with: brew reinstall switchboard'
    return 'Put them back with: switchboard update'


def doctor_line(line, folder=None):
    """One line for `switchboard doctor`: ok when every file is as installed, NO with what changed, -- with no list.
    Never raises: a line that can't be made is left out."""
    try:
        folder = folder or program_folder()
        p = check_program(folder)
        how = _source()[0]
        if p['state'] == 'unchanged':
            line(True, 'Program files unchanged since install (%s, %s)' % (p['files'], MANIFEST))
        elif p['state'] == 'none':
            line(None, 'Program files: ' + program_words(p, folder, '')[0], 'Run switchboard update to add the list.')
        else:
            said = program_words(p, folder, repair_line(how))
            line(False, 'Program files: ' + '; '.join(said[:-1]), said[-1])
    except Exception as e:  # noqa: BLE001 - a line for doctor: it never stops doctor
        core.log('checking the program files: %r' % e)


# ---------- where it came from ----------

def _source():
    """(how, where) from <data>/app/source, as `switchboard update` reads it."""
    from . import newer
    return newer.source()


REPO_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$')


def _repository_ok(repo):
    return bool(repo and REPO_RE.match(repo) and not repo.startswith('OWNER/'))


def repository(how, where):
    """'github.com/<owner>/<name>' when this install knows where it came from, else None. Never a placeholder."""
    if how == 'web':
        # the installer's address: https://raw.githubusercontent.com/<owner>/<name>/<branch>/install.sh (INSTALL_URL)
        u = urlparse(where)
        parts = u.path.strip('/').split('/')
        if u.hostname == 'raw.githubusercontent.com' and len(parts) == 4 and parts[3] == 'install.sh':
            repo = '%s/%s' % (parts[0], parts[1])
            return 'github.com/' + repo if _repository_ok(repo) else None
        return None
    if how == 'homebrew':
        # Homebrew's copy is the release's tag; its install.sh names the repository: REPO="${SWITCHBOARD_REPO:-x/y}"
        try:
            with open(os.path.join(where, 'install.sh'), encoding='utf-8', errors='replace') as f:
                m = re.search(r'^REPO="\$\{SWITCHBOARD_REPO:-([^}"]*)\}"', f.read(1 << 16), re.M)
        except OSError:
            return None
        repo = m.group(1) if m else ''
        return 'github.com/' + repo if _repository_ok(repo) else None
    return None


def branch(where):
    """The branch the one-line installer's address names (main), or None."""
    parts = urlparse(where).path.strip('/').split('/')
    return parts[2] if len(parts) == 4 else None


def in_caskroom(where):
    """<prefix>/Caskroom/<token>/<version>, or a folder in it (Homebrew unpacks the release's archive there)."""
    return bool(re.match(r'^.*/Caskroom/[^/]+/[^/.][^/]*(/|$)', where or ''))


def cask_checksum(where):
    """What the cask Homebrew kept for this install says of the release file: ('sha256', hex), ('no_check', None), or
    (None, None) when no cask is found. Homebrew keeps the cask it installed from under
    <prefix>/Caskroom/<token>/.metadata/<version>/<time>/Casks/<token>.rb (or .json): the newest one is read."""
    m = re.match(r'^(.*/Caskroom/[^/]+)/', where or '')
    if not m:
        return None, None
    meta = os.path.join(m.group(1), '.metadata')
    found = []
    try:
        for version in os.listdir(meta):
            vdir = os.path.join(meta, version)
            if not os.path.isdir(vdir):
                continue
            for stamp in os.listdir(vdir):
                for name in ('switchboard.rb', 'switchboard.json'):
                    path = os.path.join(vdir, stamp, 'Casks', name)
                    if os.path.isfile(path):
                        found.append((os.path.getmtime(path), path))
    except OSError:
        return None, None
    for _, path in sorted(found, reverse=True):
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                text = f.read(1 << 20)
        except OSError:
            continue
        if re.search(r'^\s*sha256\s+:no_check\b', text, re.M) or re.search(r'"sha256"\s*:\s*"no_check"', text):
            return 'no_check', None
        got = re.search(r'^\s*sha256\s+"([0-9a-f]{64})"', text, re.M) or \
            re.search(r'"sha256"\s*:\s*"([0-9a-f]{64})"', text)
        if got:
            return 'sha256', got.group(1)
    return None, None


def source_facts():
    """{'how': 'homebrew' | 'web' | 'folder' | None, 'where', 'checked': True when a checksum was checked before
    anything ran, 'sha256', 'branch'}."""
    how, where = _source()
    out = {'how': how, 'where': where, 'checked': False, 'sha256': None, 'branch': None}
    if how == 'homebrew':
        # Homebrew checks a cask's sha256 before its installer step runs, unless the cask says :no_check. A copy that
        # is not in a Caskroom was not put there by Homebrew (install.sh --homebrew run by hand): nothing was checked.
        kind, value = cask_checksum(where)
        out['checked'] = in_caskroom(where) and kind != 'no_check'
        out['sha256'] = value
    elif how == 'web':
        out['branch'] = branch(where)
    return out


def source_words(s):
    """(label, [lines]) for where it came from."""
    if s['how'] == 'homebrew' and s['checked']:
        return 'Verified', ['Homebrew checked the release file against its SHA-256 before anything ran'] + \
            (['sha256 %s' % s['sha256']] if s['sha256'] else [])
    if s['how'] == 'homebrew' and not in_caskroom(s['where']):
        return 'Source', ['%s, run as Homebrew runs it but not from its Caskroom, so no checksum is known'
                          % _short(s['where'])]
    if s['how'] == 'homebrew':
        return 'Source', ['Homebrew, with no checksum: the cask it installed from says sha256 :no_check']
    if s['how'] == 'web':
        return 'Source', ['the newest code on %s, with no checksum (an install with Homebrew checks one)'
                          % (s['branch'] or 'main')]
    if s['how'] == 'folder':
        return 'Source', ['copied from %s; nothing was downloaded' % _short(s['where'])]
    return 'Source', ['not recorded: installed by a version before this was kept']


def licence(folder):
    """'MIT' when the LICENSE beside the program is the MIT licence, else None."""
    try:
        with open(os.path.join(folder, 'LICENSE'), encoding='utf-8', errors='replace') as f:
            first = f.readline().strip()
    except OSError:
        return None
    return 'MIT' if first == 'MIT License' else None


# ---------- signatures, as macOS says them ----------

def run_tool(argv, timeout):
    """(exit status, standard output and standard error together). Raises OSError when the program is not there and
    subprocess.TimeoutExpired after timeout seconds."""
    r = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=timeout)
    return r.returncode, r.stdout.decode('utf-8', 'replace')


def _ask(run, argv):
    """(status, text, problem): problem 'missing' or 'timeout', else None."""
    try:
        status, text = run(argv, TOOL_S)
    except subprocess.TimeoutExpired:
        return None, '', 'timeout'
    except OSError:
        return None, '', 'missing'
    return status, text or '', None


def signature(name, app, run):
    """What macOS says of app's signature: {'app', 'path', 'kind': 'developer-id' | 'adhoc' | 'unsigned' | 'other' |
    'unknown', 'team', 'authority', 'notarized' (True, False, or None when not asked or not known), 'gatekeeper'
    ('accepted', 'rejected' or None), 'problem' (why it could not be told: 'missing app', 'codesign missing',
    'codesign timeout', 'codesign said', 'spctl missing', 'spctl timeout'), 'said': codesign's first line when it
    refused}."""
    out = {'app': name, 'path': app, 'kind': 'unknown', 'team': None, 'authority': None, 'notarized': None,
           'gatekeeper': None, 'problem': None, 'said': None}
    if not os.path.isdir(app):
        out['problem'] = 'missing app'
        return out
    status, text, problem = _ask(run, [CODESIGN, '-dv', '--verbose=2', app])
    if problem:
        out['problem'] = 'codesign ' + problem
        return out
    pairs = [line.split('=', 1) for line in text.splitlines() if '=' in line]
    fields = dict((k.strip(), v.strip()) for k, v in reversed(pairs))      # the first of a repeated key wins
    authorities = [v.strip() for k, v in pairs if k.strip() == 'Authority']
    team = fields.get('TeamIdentifier')
    out['team'] = team if team and team != 'not set' else None
    if status != 0:
        if 'not signed at all' in text:
            out['kind'] = 'unsigned'
        else:
            out['problem'] = 'codesign said'
            out['said'] = next((line.strip() for line in text.splitlines() if line.strip()), '')[:80]
        return out
    if fields.get('Signature') == 'adhoc':
        out['kind'] = 'adhoc'
        return out
    if not authorities:
        out['problem'] = 'codesign said'
        out['said'] = next((line.strip() for line in text.splitlines() if line.strip()), '')[:80]
        return out
    out['authority'] = authorities[0].split(':', 1)[0].strip()      # "Developer ID Application", never a name
    out['kind'] = 'developer-id' if out['authority'] == 'Developer ID Application' else 'other'
    if not out['team']:
        m = re.search(r'\(([A-Z0-9]{10})\)$', authorities[0])
        out['team'] = m.group(1) if m else None
    status, text, problem = _ask(run, [SPCTL, '--assess', '--type', 'execute', '-vv', app])
    if problem:
        out['problem'] = 'spctl ' + problem
        return out
    m = re.search(r'^source=(.*)$', text, re.M)
    source = m.group(1).strip() if m else ''
    out['gatekeeper'] = 'accepted' if status == 0 and ': accepted' in text else 'rejected'
    out['notarized'] = out['gatekeeper'] == 'accepted' and source == 'Notarized Developer ID'
    return out


def signature_words(s):
    """One line for an app's signature, saying exactly what macOS said."""
    team = ' (%s)' % s['team'] if s['team'] else ''
    p = s['problem']
    if p == 'missing app':
        return '%s: not found at %s' % (s['app'], _short(s['path']))
    if p == 'codesign missing':
        return '%s: could not be checked: %s is not on this Mac' % (s['app'], CODESIGN)
    if p == 'codesign timeout':
        return '%s: could not be checked: codesign did not answer in %d s' % (s['app'], TOOL_S)
    if p == 'codesign said':
        return '%s: could not be checked (codesign: %s)' % (s['app'], s['said'] or 'no answer')
    if s['kind'] == 'unsigned':
        return '%s: not signed' % s['app']
    if s['kind'] == 'adhoc':
        return '%s: made and signed on this Mac (ad-hoc)' % s['app']
    who = 'Developer ID%s' % team if s['kind'] == 'developer-id' else '%s%s' % (s['authority'], team)
    if p == 'spctl missing':
        return '%s: %s; whether Apple notarized it could not be checked: %s is not on this Mac' % (s['app'], who, SPCTL)
    if p == 'spctl timeout':
        return '%s: %s; whether Apple notarized it could not be checked in %d s' % (s['app'], who, TOOL_S)
    if s['gatekeeper'] == 'rejected':
        return '%s: %s; macOS does not accept it (spctl: rejected)' % (s['app'], who)
    if s['notarized']:
        return '%s: %s, notarized by Apple' % (s['app'], who)
    return '%s: %s, not notarized' % (s['app'], who)


def apps():
    """[(name, path)] of the apps this install has: the link app, then the menu bar's app."""
    from . import linkapp, menubar_wrap
    out = []
    if linkapp.installed():
        out.append((os.path.basename(linkapp.app_path())[:-len('.app')], linkapp.app_path()))
    menu = menubar_wrap.present()
    if menu:
        out.append((os.path.basename(menu), menu))
    return out


# ---------- where it put things, and whose they are ----------

def command_places(how, where):
    """Where the command is linked, as [path, ...] said with ~ (the short name after it when it is beside it), or
    the launcher itself when no link is ours. One-line install: ~/.local/bin or /usr/local/bin, a link to
    <data>/bin/switchboard. Homebrew: its bin folder, the folder over its Caskroom (the cask's `binary` line), with sb
    a link to `switchboard` beside it (install.sh's short_ours)."""
    sb = core.data_path(os.path.join('bin', 'switchboard'))
    folders = [os.path.join(core.home(), '.local', 'bin'), '/usr/local/bin']
    if how == 'homebrew':
        m = re.match(r'^(.*)/Caskroom/', where or '')
        folders = [m.group(1) + '/bin'] if m else []
    for folder in folders:
        cmd = os.path.join(folder, 'switchboard')
        if not os.path.islink(cmd):
            continue
        target = os.path.realpath(cmd)
        ours = target == os.path.realpath(sb) or (how == 'homebrew' and '/Caskroom/' in target
                                                  and os.path.basename(target) == 'launcher')
        if not ours:
            continue
        short = os.path.join(folder, core.SHORT_NAME)
        beside = os.path.islink(short) and os.readlink(short) in (sb, 'switchboard')
        return [_short(cmd) + (' and %s' % core.SHORT_NAME if beside else '')]
    return [_short(sb)] if os.path.exists(sb) else []


def installed_places(how, where, app_list):
    """Everything this install put on the Mac that is there now: the data folder, the command, each app."""
    out = [_short(core.data_dir())] if os.path.isdir(core.data_dir()) else []
    out += command_places(how, where)
    from . import linkapp
    menu = [p for n, p in app_list if p != linkapp.app_path()]
    links = [p for n, p in app_list if p == linkapp.app_path()]
    return out + [_short(p) for p in menu + links]


def not_mine(paths, most=1000):
    """Of these files and folders and everything in them, the paths that belong to another user (at most `most`)."""
    me = os.getuid()
    out = []
    for top in paths:
        if not os.path.lexists(top):
            continue
        found = [top]
        if os.path.isdir(top) and not os.path.islink(top):
            for folder, dirs, names in os.walk(top):
                found += [os.path.join(folder, n) for n in dirs + names]
        for p in found:
            try:
                if os.lstat(p).st_uid != me:
                    out.append(p)
            except OSError:
                continue
            if len(out) >= most:
                return out
    return out


def runs_as(program, app_list):
    """{'root': running as root, 'others': paths it added that are another user's, 'open': the data folder can be
    read by other users of this Mac}."""
    data = core.data_dir()
    added = [program, core.data_path('bin')] + [p for _, p in app_list] + \
        [os.path.join(core.home(), 'Library', 'LaunchAgents', label + '.plist') for label in _agents()]
    try:
        open_to_others = bool(os.stat(data).st_mode & 0o077)
    except OSError:
        open_to_others = False
    return {'root': os.getuid() == 0, 'others': [_short(p) for p in not_mine(added)], 'open': open_to_others}


def runs_as_words(r):
    if r['root']:
        lines = ['root: run it as yourself, without sudo, so that what it keeps is yours']
    elif r['others']:
        lines = ['you, but %s it added belong to another user: %s' % (
            'files' if len(r['others']) > 1 else 'a file', _names(r['others'], len(r['others'])))]
    else:
        # install.sh refuses root, and uses /Applications only when it is writable without a password
        lines = ['you: no administrator password was asked for or used; every file it added is yours']
    if r['open']:
        lines.append('%s can be read by other users of this Mac: chmod 700 %s' % ((_short(core.data_dir()),) * 2))
    return lines


# ---------- what runs ----------

def _agents():
    from . import menubar
    return (menubar.PAGE_AGENT, menubar.AGENT)


def job(label):
    """This install's background job of that label: its property list is there, starts its program at load, and that
    program is there."""
    plist = os.path.join(core.home(), 'Library', 'LaunchAgents', label + '.plist')
    try:
        with open(plist, 'rb') as f:
            got = plistlib.load(f)
    except Exception:  # noqa: BLE001 - missing, unreadable or not a property list: no job
        return False
    args = got.get('ProgramArguments') if isinstance(got, dict) else None
    return bool(got.get('RunAtLoad') is True and isinstance(args, list) and args and isinstance(args[0], str)
                and os.path.exists(args[0]))


def page_fact(cli):
    """'running', 'busy', 'other' (something on the port that is not this data folder's page: another install's, or
    the demo) or 'down'."""
    from . import engine
    said = cli.page_status()
    if said == 'down':
        return 'down'
    try:
        cli.same_folder()
    except engine.Refused:
        return 'other'
    except OSError:
        return 'down'
    return said


def running(cli, wait=0.0, sleep=time.sleep, clock=time.monotonic):
    """{'page': page_fact, 'port', 'page_job', 'menubar': None (not installed) or True/False, 'menubar_job'}. wait:
    seconds to wait for a menu bar item whose job is in place to take its lock (it has just been started)."""
    from . import menubar
    out = {'port': core.port(), 'page': page_fact(cli), 'page_job': job(menubar.PAGE_AGENT),
           'menubar': None, 'menubar_job': False}
    if os.path.exists(menubar.launcher()):
        out['menubar_job'] = job(menubar.AGENT) and menubar.our_job() is not None
        up = menubar.running_pid() is not None
        give_up = clock() + wait
        while not up and out['menubar_job'] and clock() < give_up:
            sleep(0.25)
            up = menubar.running_pid() is not None
        out['menubar'] = up
    return out


def running_words(r):
    page = 'the page on 127.0.0.1:%d (this Mac only)' % r['port']      # server.py listens on 127.0.0.1 only
    up = (['the menu bar item'] if r['menubar'] else []) + ([page] if r['page'] in ('running', 'busy') else [])
    jobs = [r['page_job']] + ([r['menubar_job']] if r['menubar'] is not None else [])
    lines = []
    if up:
        if all(jobs) and len(jobs) == 2:
            login = 'both start at login'
        elif all(jobs):
            login = 'it starts at login'
        elif not any(jobs):
            login = 'no background job starts %s at login' % ('them' if len(up) > 1 else 'it')
        else:
            login = ('the page starts at login' if r['page_job'] else 'the menu bar item starts at login')
        lines.append('%s; %s' % (', and '.join(up), login))
    if r['page'] == 'other':
        lines.append('another page answers on 127.0.0.1:%d, not this install\'s: switchboard doctor says more'
                     % r['port'])
    elif r['page'] == 'down':
        lines.append('the page is not running: start it with switchboard serve'
                     if not r['page_job'] else 'the page is not running yet: switchboard doctor says why')
    if r['menubar'] is False:
        lines.append('the menu bar item is not running: start it with switchboard menubar')
    return lines


# ---------- sign-ins, privacy, removing it, the next step ----------

def accounts_kept():
    """How many accounts the engine keeps, from accounts.json read leniently (a list, or one keyed by id, each with an
    email), as the installer counts them. No Keychain is read."""
    a = core.read_json(core.data_path('accounts.json')).get('accounts')
    rows = a.values() if isinstance(a, dict) else a if isinstance(a, list) else []
    return len([r for r in rows if isinstance(r, dict) and r.get('email')])


def recovery_files():
    """How many recovery copies are kept in owner-only files because the Keychain refused them (vault.stash): the
    files in <data>/recovery other than its list. Only names are looked at."""
    try:
        names = os.listdir(core.data_path('recovery'))
    except OSError:
        return 0
    return len([n for n in names if n.endswith('.json') and n != 'manifest.json'])


def signin_words(n, files):
    if n:
        said = '%s, each login kept in your macOS Keychain' % _plural(n, 'account')
    else:
        said = 'none kept yet; each one you add is kept in your macOS Keychain'
    lines = [said]
    if files:
        lines.append('%s in a file only you can read, since the Keychain refused %s (switchboard leftovers)'
                     % (_plural(files, 'recovery copy', 'recovery copies'), 'it' if files == 1 else 'them'))
    return lines


def privacy():
    """{'others': [who else this install sends to, in words], 'update_check': 'off' | 'daily'}. From the README's
    Privacy section: Anthropic always; another assistant's maker once that provider is added, only after a yes for a
    folder; the host of each key added for another provider."""
    from . import newer
    others = []
    try:
        from .providers import adapter_class, choices
        for pid in sorted(choices.added()):
            if pid == choices.first():
                continue
            cls = adapter_class(pid)
            name, vendor = (getattr(cls, 'name', '') or pid, getattr(cls, 'vendor', '')) if cls else (pid, '')
            others.append('to %s through %s after your yes for a folder' % (vendor, name) if vendor
                          else 'to %s after your yes for a folder' % name)
    except Exception as e:  # noqa: BLE001 - the line is made without them
        core.log('reading the providers for about: %r' % e)
    lanes = core.read_json(core.data_path('gateway-lanes.json')).get('lanes')
    hosts = []
    for lane in lanes if isinstance(lanes, list) else []:
        host = urlparse(lane.get('base_url') or '').hostname if isinstance(lane, dict) else None
        if host and host not in ('api.anthropic.com', '127.0.0.1', 'localhost', '::1') and host not in hosts:
            hosts.append(host)
    if hosts:
        others.append('to %s, for the key%s you added' % (', '.join(hosts), 's' if len(hosts) > 1 else ''))
    return {'others': others, 'update_check': newer.check_setting()}


def privacy_words(p):
    lines = ['no telemetry; sends nothing of yours off this Mac except to Anthropic', 'and to providers you add']
    lines += ['and %s' % o for o in p['others']]
    lines.append('the look for a newer version is off' if p['update_check'] != 'daily' else
                 'a look for a newer version once a day is on (switchboard config set update_check off)')
    return lines


def downloads(how, where):
    """[what, from where] this install downloaded, in words. From install.sh: the one-line install fetches the program
    from github.com (ARCHIVE_URL; app/source holds the installer's address on raw.githubusercontent.com), Homebrew
    fetches the release's tag from GitHub (packaging/README.md), Switchboard's own Python comes from
    python-build-standalone's releases on github.com (PBS_URL), and PyObjC for the menu bar from pypi.org (pip
    --index-url https://pypi.org/simple). runtime/python.json, which the installer writes, names the Python each part
    runs on; a piece is said only when it names one of these and its folder is there."""
    got = []
    if how == 'homebrew' and in_caskroom(where):
        got.append('the release from github.com (by Homebrew)')
    elif how == 'web':
        host = urlparse(where or '').hostname
        if host:
            got.append('the program from %s' % ('github.com' if host == 'raw.githubusercontent.com' else host))
    rec = core.read_json(core.data_path(os.path.join('runtime', 'python.json')))
    own = core.data_path(os.path.join('runtime', 'python', 'bin', 'python3'))
    if 'own' in (rec.get('python_source'), rec.get('menu_python_source')) and os.path.isfile(own):
        got.append("Switchboard's own Python from github.com")
    if rec.get('menu_python') and os.path.isdir(core.data_path(os.path.join('runtime', 'env'))):
        got.append('PyObjC for the menu bar from pypi.org')
    return got


def download_words(got):
    return [', '.join(got)] if got else ['nothing: every piece was already on this Mac']


def remove_words(how):
    if how == 'homebrew':
        return ['brew uninstall switchboard   (--zap also removes your accounts)']
    uninstaller = core.data_path(os.path.join('app', 'uninstall.sh'))
    if not os.path.isfile(uninstaller):
        return ['the uninstaller is not in %s: run the installer again to put it back'
                % _short(os.path.dirname(uninstaller))]
    return ['sh %s   (--all also removes your accounts)' % _short(uninstaller)]


def next_words(n, cmd='switchboard'):
    """[(text, bold)]: the next step. While no account is kept, `switchboard` alone at a terminal runs the first run
    (home() in __main__.py), so it is the one word to type."""
    if n:
        return [('Type ', False), (cmd, True), (' to see your accounts.', False)]
    return [('Next: ', False), (cmd, True)]


# ---------- all of it ----------

def gather(cli, run=None, folder=None, wait=0.0, sleep=time.sleep, clock=time.monotonic):
    """Every fact, checked now, as one JSON-ready document. cli: the command line's module (page_status,
    same_folder). run: what runs codesign and spctl (run_tool)."""
    from . import newer
    folder = folder or program_folder()
    version, build = newer.version_in(folder) or (core.VERSION, core.build())
    src = source_facts()
    app_list = apps()
    return {
        'schema': SCHEMA, 'version': version, 'build': build, 'licence': licence(folder),
        'repository': repository(src['how'], src['where']), 'source': src,
        'signed': [signature(name, path, run or run_tool) for name, path in app_list],
        'runs_as': runs_as(folder, app_list),
        'installed': installed_places(src['how'], src['where'], app_list),
        'running': running(cli, wait, sleep, clock),
        'program': check_program(folder),
        'folder': folder,
        'sign_ins': {'accounts': accounts_kept(), 'recovery_files': recovery_files()},
        'privacy': privacy(),
        'downloaded': downloads(src['how'], src['where']),
    }


def rows(facts, short=False):
    """[(label, [value lines])] in the block's order. short: an update's form (Program, Running)."""
    how = facts['source']['how']
    program = ('Program', program_words(facts['program'], facts['folder'], repair_line(how)))
    run = ('Running', running_words(facts['running']))
    if short:
        return [program, run]
    out = [source_words(facts['source'])]
    if facts['signed']:
        out.append(('Signed', [signature_words(s) for s in facts['signed']]))
    out += [('Runs as', runs_as_words(facts['runs_as'])), ('Installed', facts['installed'] or ['nothing found']),
            run, program,
            ('Sign-ins', signin_words(facts['sign_ins']['accounts'], facts['sign_ins']['recovery_files'])),
            ('Privacy', privacy_words(facts['privacy'])), ('Downloaded', download_words(facts['downloaded'])),
            ('Remove', remove_words(how))]
    return out


def _fit(label, values):
    """A row's lines: the label in its column, each value after it, wrapped to WIDTH under the value column. The
    Installed row's places stay whole, three spaces apart."""
    room = WIDTH - 2 - LABEL_W
    lines = []
    if label == 'Installed':
        line = ''
        for place in values:
            if line and len(line) + 3 + len(place) > room:
                lines.append(line)
                line = place
            else:
                line = line + '   ' + place if line else place
        lines.append(line)
    else:
        for v in values:
            lines += textwrap.wrap(v, room, break_long_words=False, break_on_hyphens=False) or ['']
    return [(label if i == 0 else '', text) for i, text in enumerate(lines)]


def head(facts):
    """[(text, style)] of the first line: 'Switchboard 0.2.0 (d3da1a4)  MIT  github.com/<owner>/switchboard'."""
    name = 'Switchboard %s' % ('%s (%s)' % (facts['version'], facts['build']) if facts['build'] else facts['version'])
    rest = ''.join('  ' + x for x in (facts['licence'], facts['repository']) if x)
    return [(name, 'bold'), (rest, 'dim')]


def block(facts, short=False, done=None, cmd='switchboard', setup_now=False, colour=False):
    """The block's lines, painted for a terminal when colour is True."""
    from . import term
    style = {'bold': term.BOLD, 'dim': term.DIM, None: None}
    out = [term.paint([(t, style[s]) for t, s in head(facts)], colour), '']
    for label, values in rows(facts, short):
        for lab, text in _fit(label, values):
            out.append(term.paint([('  %-*s' % (LABEL_W, lab), term.DIM), (text, None)], colour).rstrip())
    out.append('')
    lead = [('Done in %d s.' % done, None)] if done is not None else []
    if done is not None:
        out.insert(0, '')               # after the installer's own lines, a blank line before the block
    if setup_now:
        out.append(term.paint(lead, colour) if lead else '')
    else:
        steps = [(t, term.BOLD if b else None) for t, b in next_words(facts['sign_ins']['accounts'], cmd)]
        out.append(term.paint(lead + ([(' ', None)] if lead else []) + steps, colour))
    while out and not out[-1]:
        out.pop()
    return out


def document(facts, short=False, done=None, cmd='switchboard', setup_now=False):
    """--json: the facts, and the block as plain lines."""
    doc = dict(facts)
    doc['block'] = block(facts, short, done, cmd, setup_now)
    return doc


def main(args, cli, run=None, out=None, wait_s=MENUBAR_WAIT_S):
    """`switchboard about [--json]`, and the installer's own options (above). Exit 0, or 2 for words it does not take."""
    out = out or sys.stdout
    as_json, short, setup_now, done, cmd = False, False, False, None, 'switchboard'
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--json' and not as_json:
            as_json = True
        elif a == '--update' and not short:
            short = True
        elif a == '--setup-now' and not setup_now:
            setup_now = True
        elif a == '--done' and done is None and i + 1 < len(args) and args[i + 1].isdigit():
            i += 1
            done = int(args[i])
        elif a == '--as' and i + 1 < len(args) and args[i + 1] and not args[i + 1].startswith('-'):
            i += 1
            cmd = args[i]
        else:
            out.write(USAGE + '\n')
            return 2
        i += 1
    from . import term
    facts = gather(cli, run=run, wait=wait_s if done is not None else 0.0)
    if as_json:
        out.write(json.dumps(document(facts, short, done, cmd, setup_now), indent=1) + '\n')
        return 0
    lines = block(facts, short, done, cmd, setup_now, colour=term.colour_ok(out, os.environ))
    out.write('\n'.join(lines) + '\n')
    return 0
