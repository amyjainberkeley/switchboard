"""Which version is installed, which is the newest, and whether to say so.

- `switchboard update` ends with one line: the version it had and the version it has now (update_line), unless the
  installer it ran could not finish: then the installer's own last line, the way to finish, stays the last. Both are
  read from the installed copy (<data>/app, where the installer puts it for every way of installing), as the
  installer itself reads them: the VERSION line of switchboard/core.py, and the commit in switchboard/build.txt.
- `switchboard update --check` looks once whether a newer version exists (look). It asks only the place an update
  would install from (<data>/app/source): Homebrew's own list on this Mac for an install by Homebrew, the folder
  for an install from a clone, and for an install from the web the core.py beside the installer's address (the same
  repository and branch the installer downloads). A request to the web carries the agent name alone. It changes
  nothing: no file is written.
- A look by itself (tick, from the page's loop) only when the setting update_check is "daily", at most once a day.
  Its answer is kept in <data>/newer.json, and when a newer version is out, `switchboard status` and the page say so
  in one line (line). Nothing else: no notification, and nothing is ever installed by itself.
"""
import json
import os
import re
import subprocess
import threading
import time
from urllib.parse import urlparse

from . import core

HTTP = None                     # the network (net.Http()); tests put a stand-in here
TIMEOUT_S = 15.0                # one look at the web: the whole request
MOST = 1 << 20                  # a core.py bigger than this is not one
VERSION_MAX = 32                # characters: a version longer than this is not one
BREW_S = 60                     # Homebrew's answer, at most
CHECK_USAGE = 'switchboard update --check [--json]'
NEWER_EXIT = 10                 # `update --check`: a newer version exists
CHECK_KEY = 'update_check'      # in settings.json: whether the page looks by itself
CHECK_CHOICES = ('off', 'daily')
CHECK_DEFAULT = 'off'           # nothing leaves the Mac for this until a person turns it on
KEPT = 'newer.json'             # the last look by itself: when, and what it found
DAY = 24 * 3600                 # seconds between looks by itself, at least

_VERSION_LINE = re.compile(r"^VERSION = '([^']*)'", re.M)      # install.sh's version_in reads the same line


def version_of_text(text):
    """The version a core.py's text names, or None."""
    m = _VERSION_LINE.search(text or '')
    v = m.group(1).strip() if m else ''
    return v if v and len(v) <= VERSION_MAX else None


def version_in(folder):
    """(version, build) of the copy of Switchboard in folder (it holds switchboard/core.py), build None when not
    known; None when no version can be read there."""
    package = os.path.join(folder, 'switchboard')
    try:
        with open(os.path.join(package, 'core.py'), encoding='utf-8', errors='replace') as f:
            version = version_of_text(f.read(65536))
    except OSError:
        return None
    if not version:
        return None
    return version, core.build_of(package)


def installed():
    """(version, build) of the copy the installer put in the data folder, or None when it can't be read."""
    return version_in(core.data_path('app'))


def said(v):
    """'0.2.0 (d635e47)', or '0.2.0' when the build is not known."""
    return '%s (%s)' % v if v[1] else v[0]


def update_line(before, after, code, behind=None):
    """The last line of `switchboard update`. before and after are (version, build) or None when not known; code is
    the installer's exit code; behind is a newer version that is out and that Homebrew does not have yet (only for an
    install by Homebrew: homebrew_behind). It never guesses: a version it could not read is said to be unknown."""
    if after is None:
        if before is None:
            return 'Could not read which version is installed now.'
        return 'Could not read which version is installed now. Before the update it was %s.' % said(before)
    still = ' Homebrew does not have Switchboard %s yet; switchboard update takes it once it does.' % behind \
        if behind else ''
    if before is None:
        return 'Switchboard is now %s.%s' % (said(after), still)
    if before == after:
        if still and code == 0:
            return 'Switchboard %s is the newest Homebrew has.%s' % (said(after), still)
        return ('Switchboard %s was already the newest.' if code == 0 else 'Switchboard is still %s.') % said(after)
    return 'Updated Switchboard from %s to %s.%s' % (said(before), said(after), still)


# ---------- whether a newer version exists ----------

def compare(a, b):
    """1 when version a is newer than b, -1 when older, 0 when the same ('0.2' is '0.2.0'); None when either is not
    a version of one to four numbers."""
    def numbers(v):
        if not isinstance(v, str) or len(v.strip()) > VERSION_MAX or not re.match(r'^\d+(\.\d+){0,3}$', v.strip()):
            return None
        n = [int(x) for x in v.strip().split('.')]
        return tuple(n + [0] * (4 - len(n)))
    x, y = numbers(a), numbers(b)
    if x is None or y is None:
        return None
    return (x > y) - (x < y)


class NotKnown(Exception):
    """The newest version could not be read. The text is the reason, as a phrase."""


def source():
    """(how, where) Switchboard was installed from: ('homebrew', its copy), ('web', the installer's address),
    ('folder', the clone), or (None, None) when it was not recorded."""
    src = core.read_text(core.data_path(os.path.join('app', 'source'))).strip()
    if src.startswith('homebrew:'):
        return 'homebrew', src[len('homebrew:'):]
    if src.startswith('https://'):
        return 'web', src
    if src:
        return 'folder', src
    return None, None


def version_address(installer):
    """The address of core.py beside the installer's address (…/install.sh), or None when it is not one."""
    u = urlparse(installer)
    if u.scheme != 'https' or not u.hostname or u.query or u.fragment or not u.path.endswith('/install.sh'):
        return None
    return installer[:-len('install.sh')] + 'switchboard/core.py'


def _from_web(installer):
    from .accounts import net
    url = version_address(installer)
    if url is None:
        raise NotKnown('it was installed from %s, which is not an address it can read a version from' % installer)
    host = urlparse(url).hostname
    http = HTTP if HTTP is not None else net.Http(most=MOST)          # an answer past MOST is not read on
    try:
        status, _, body = http.request('GET', url, headers={'User-Agent': 'Switchboard/%s' % core.VERSION},
                                       timeout_s=TIMEOUT_S)
    except net.NetError as e:
        raise NotKnown(_net_said(e, host))
    if status != 200:
        raise NotKnown('%s answered %s' % (host, status))
    version = version_of_text(body[:MOST].decode('utf-8', 'replace')) if len(body or b'') <= MOST else None
    if compare(version, '0') is None:
        raise NotKnown('%s sent no version Switchboard could read' % host)
    return version


def _net_said(e, host):
    """A failed request, as a phrase. A certificate refused says what was not trusted (the error's own words)."""
    if e.kind == 'tls-cert':
        text = str(e)
        if text.startswith('tls-cert: '):
            return text[len('tls-cert: '):].rstrip('.')
        return 'the certificate of %s was not trusted' % host
    return {'dns': 'could not find %s' % host, 'connect': 'could not connect to %s' % host,
            'timeout': '%s did not answer in time' % host}.get(e.kind, 'the connection to %s failed' % host)


def _from_folder(folder):
    found = version_in(folder) if os.path.isabs(folder) else None
    if found is None:
        raise NotKnown('%s has no copy of Switchboard to read' % folder)
    return found[0]


def _from_homebrew(find_brew):
    """The version Homebrew lists for its cask, from Homebrew's own copy of its list on this Mac: `brew info` reads it
    and fetches nothing (HOMEBREW_NO_AUTO_UPDATE keeps it so, and HOMEBREW_NO_ANALYTICS sends nothing). It is as new
    as Homebrew's last `brew update`, which `brew upgrade` runs first."""
    brew = find_brew()
    if not brew:
        raise NotKnown('brew is not on this PATH')
    env = dict(os.environ, HOMEBREW_NO_AUTO_UPDATE='1', HOMEBREW_NO_ANALYTICS='1')
    try:
        r = subprocess.run([brew, 'info', '--cask', '--json=v2', 'switchboard'], capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=BREW_S, env=env)
        casks = json.loads(r.stdout.decode('utf-8', 'replace')).get('casks') if r.returncode == 0 else None
        version = str(casks[0].get('version') or '').split(',')[0].strip()
    except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError, IndexError, KeyError, TypeError):
        raise NotKnown('Homebrew did not answer')
    if compare(version, '0') is None:
        raise NotKnown('Homebrew lists no version Switchboard could read')
    return version


_REPO_LINE = re.compile(r'^REPO="\$\{SWITCHBOARD_REPO:-([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)\}"', re.M)
_URL_LINE = re.compile(r'^INSTALL_URL="(https://[^"$]*)\$REPO(/[^"$]*)"', re.M)


def installer_address(folder):
    """The address the one-line installer in folder (a copy of the repository: Homebrew's) is fetched from, as its
    install.sh sets it (INSTALL_URL with REPO); None when it can't be read or REPO still holds its placeholder."""
    text = core.read_text(os.path.join(folder, 'install.sh'))
    repo, url = _REPO_LINE.search(text), _URL_LINE.search(text)
    if not repo or not url or repo.group(1).startswith('OWNER/'):
        return None
    return url.group(1) + repo.group(1) + url.group(2)


def homebrew_behind(after):
    """For an install by Homebrew, after an update to after ((version, build) or None): the newest version the place
    an install from the web updates from names (_from_web, beside installer_address of Homebrew's copy), when it is
    newer than after; else None, also when it can't look. A release reaches Homebrew when its cask is updated, which
    comes after the release itself."""
    how, where = source()
    if how != 'homebrew' or after is None:
        return None
    installer = installer_address(where)
    if installer is None:
        return None
    try:
        newest = _from_web(installer)
    except NotKnown:
        return None
    return newest if compare(newest, after[0]) == 1 else None


def _brew_path():
    from .__main__ import brew_path
    return brew_path()


def look(find_brew=None):
    """One look: {'installed_from', 'this_version', 'newest_version', 'newer' (True, False, or None when not known),
    'problem' (None, or the sentence that says why it could not look)}. It writes nothing."""
    how, where = source()
    mine = installed() or (core.VERSION, None)
    out = {'installed_from': how, 'this_version': mine[0], 'newest_version': None, 'newer': None, 'problem': None}
    try:
        if how is None:
            raise NotKnown('Switchboard does not know where it was installed from')
        if how == 'homebrew':
            newest = _from_homebrew(find_brew or _brew_path)
        elif how == 'web':
            newest = _from_web(where)
        else:
            newest = _from_folder(where)
        order = compare(newest, mine[0])
        if order is None:
            raise NotKnown('the versions %s and %s can not be compared' % (newest, mine[0]))
    except NotKnown as e:
        out['problem'] = 'Could not look for a newer version: %s.' % e
        return out
    out.update(newest_version=newest, newer=order > 0)
    return out


def look_said(found):
    """The one line `switchboard update --check` prints for a look."""
    if found['problem']:
        return found['problem']
    if found['newer']:
        return 'Switchboard %s is out (this is %s). Install it with: switchboard update' % (
            found['newest_version'], found['this_version'])
    if compare(found['newest_version'], found['this_version']) == 0:
        return 'Switchboard %s is the newest.' % found['this_version']
    return 'This is Switchboard %s, ahead of the %s it can update from.' % (found['this_version'],
                                                                           found['newest_version'])


def when(t):
    """A time in seconds as '2026-11-25T06:10:00Z' (UTC, to the second)."""
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t))


def check_document(found, now):
    """`switchboard update --check --json`: the look as a document (the rules of report.py)."""
    return {'schema': 1, 'checked_at': when(now), 'installed_from': found['installed_from'],
            'this_version': found['this_version'], 'newest_version': found['newest_version'],
            'newer': found['newer'], 'problem': found['problem']}


def check_words(args):
    """(as JSON, the first word it does not take or None) for `switchboard update ARGS` with --check among ARGS."""
    seen = []
    for a in args:
        if a not in ('--check', '--json') or a in seen:
            return '--json' in args, a
        seen.append(a)
    return '--json' in args, None


def check_command(as_json, wrong=None, find_brew=None):
    """`switchboard update --check [--json]`. wrong: a word it does not take (exit 2). Exit 0 when this is the newest
    (or ahead), NEWER_EXIT when a newer one exists, 1 when it could not look."""
    if wrong is not None:
        if as_json:
            found = {'installed_from': None, 'this_version': (installed() or (core.VERSION, None))[0],
                     'newest_version': None, 'newer': None, 'problem': 'Unknown word: %s. %s' % (wrong, CHECK_USAGE)}
            print(json.dumps(check_document(found, time.time()), indent=1))
        else:
            print(CHECK_USAGE)
        return 2
    found = look(find_brew)
    if as_json:
        print(json.dumps(check_document(found, time.time()), indent=1))
    else:
        print(look_said(found))
    return 1 if found['problem'] else NEWER_EXIT if found['newer'] else 0


# ---------- a look by itself, once a day, only when it is turned on ----------

def check_setting():
    """'daily' or 'off' (the default, and what anything else in settings.json reads as)."""
    v = core.settings().get(CHECK_KEY)
    return v if v in CHECK_CHOICES else CHECK_DEFAULT


def set_check(value):
    """Set update_check to 'off' or 'daily'. Raises SwitchError for any other value. Returns the line to say."""
    if value not in CHECK_CHOICES:
        raise core.SwitchError('update_check is off or daily, not %s.' % value)
    core.set_setting(CHECK_KEY, value)
    if value == 'daily':
        return 'Switchboard looks for a newer version once a day.'
    return 'Switchboard does not look for a newer version by itself.'


def kept():
    """What the last look by itself found ({'looked_at', 'newest_version', 'problem'}), or {}."""
    return core.read_json(core.data_path(KEPT))


def due(now):
    """True when a look by itself is turned on and the last one was not in the day before now. A last look in the
    future (the clock was set back) does not hold the next one back."""
    if check_setting() != 'daily':
        return False
    t = kept().get('looked_at')
    if not isinstance(t, (int, float)) or isinstance(t, bool):
        return True
    return not 0 <= now - t < DAY


_RUNNING = threading.Lock()


def tick(now=None, find_brew=None):
    """The page's loop calls this at every pass. When a look is due, one look on a thread of its own (the thread is
    returned; None when nothing was started). A look that failed counts as a look: the next is a day later."""
    now = time.time() if now is None else now
    try:
        if not due(now) or not _RUNNING.acquire(False):
            return None
    except Exception as e:  # noqa: BLE001 - the loop goes on whatever this does
        core.log('looking for a newer version: %r' % e)
        return None

    def run():
        try:
            found = look(find_brew)
            core.write_json(core.data_path(KEPT), {'schema': 1, 'looked_at': now,
                                                   'newest_version': found['newest_version'],
                                                   'problem': found['problem']})
            if found['problem']:
                core.log(found['problem'])
        except Exception as e:  # noqa: BLE001
            core.log('looking for a newer version: %r' % e)
        finally:
            _RUNNING.release()
    t = threading.Thread(target=run, name='switchboard-newer', daemon=True)
    t.start()
    return t


def line():
    """The one quiet line for `switchboard status` and the page, or None: only while the look by itself is on, and
    only when the newest version it found is newer than this one."""
    if check_setting() != 'daily':
        return None
    newest = kept().get('newest_version')
    if not isinstance(newest, str) or compare(newest, core.VERSION) != 1:
        return None
    return look_said({'problem': None, 'newer': True, 'newest_version': newest, 'this_version': core.VERSION})
