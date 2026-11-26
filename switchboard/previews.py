"""Previews: a small picture of each artifact's own page, made on this Mac from the file Claude Code published.

A publish answer names the local file the page was published from (the session hooks' record, and for pages known
from before, Claude Code's history: artifacts.backfill_files). While that file is still there (a session's temporary
folder is emptied within days) and unchanged since that publish (a session often writes the same file again for
another page), the page's background worker opens it once in headless Chrome and keeps a picture:

  - headless Chrome with a fresh temporary profile (--user-data-dir), removed after: never one of your profiles, and
    never the Keychain (--use-mock-keychain);
  - the file:// page at a 1200 x 750 viewport, drawn at half scale and saved as a JPEG about 480 px wide (macOS's
    sips makes it that size);
  - the page may load what a published artifact may load (the code and font sites claude.ai allows); no other site
    is reached, nor anything on this Mac (a permission the page asks for is refused);
  - one at a time, at background priority, with a 30 s deadline; while the Mac crawls or the disk is almost full,
    later; a picture that failed is tried again an hour later, a day later after three failures (one cut off by the
    deadline on a busy Mac never counts as one);
  - no browser, no picture.

Pictures live in ~/.switchboard/previews/<id>.jpg: only you can read them, and each is written whole or not at all. At
most about 500 files or 50 MB, the oldest going first. A picture goes when its artifact is no longer listed (an
account removed keeps its pictures while its artifacts are), and `uninstall.sh --all` removes the folder.

The page reads them through GET /api/preview/<id> (picture()): the id must be one of the artifacts the page lists,
and it is never used as a path as it came.
"""
import os
import pathlib
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time

from . import browser, core, linkapp
from .core import log

FOLDER = 'previews'
VIEW = (1200, 750)          # the page's viewport, in CSS pixels
SCALE = 0.5                 # Chrome draws it at half scale (600 px wide: it goes no lower); sips takes it to WIDTH
WIDTH = 480
QUALITY = 70
BUDGET_MS = 5000            # the page's own (virtual) time to run its scripts and load its fonts before the picture
DEADLINE = 30.0             # seconds Chrome may take for one page; past that it and its helpers are ended
SIPS_DEADLINE = 15.0
MAX_FILES = 500
MAX_BYTES = 50 << 20
MAX_PICTURE = 2 << 20       # a picture larger than this is not kept, nor served
MAX_SOURCE = 20 << 20       # a page's file larger than this is not drawn
EDITED = 5.0                # a file changed more than this long after its publish no longer holds what that publish sent
TRIES = 3                   # a page whose picture failed this often is tried again only a day after its last failure
RETRY_AFTER = 3600          # nor within an hour of a failure
AGAIN_AFTER = 86400
CALM = 1.0                  # load per core under this: a draw past its deadline was the page's doing. At or over it,
                            # the Mac's (Chrome runs at background priority): tried again in an hour, never counted
EVERY = 600                 # the worker looks for work at least this often; a new publish starts it at once
BUSY = 6.0                  # load per core at or over this (a Mac that crawls): drawing waits. Chrome runs at
                            # background priority, so an ordinary busy Mac still gets its pictures before the files go
EXTS = ('.html', '.htm')
TEMP_PREFIX = 'switchboard-preview-'   # each picture's temporary profile, in the temporary folder
SITES = ('cdnjs.cloudflare.com', 'cdn.jsdelivr.net', 'unpkg.com', 'cdn.tailwindcss.com', 'code.jquery.com',
         'fonts.googleapis.com', 'fonts.gstatic.com')      # what a published artifact may load (the Artifact rules)
ID_RE = re.compile(r'[A-Za-z0-9_-]{16,128}')    # whole: fullmatch, never a trailing newline
SOF = frozenset((0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF))

# The outside world, by name, so tests stand in for it.
CHROME = None               # tests: a stand-in browser's program; None: found in Applications (chrome())
SIPS = '/usr/bin/sips'
TASKPOLICY = '/usr/sbin/taskpolicy'

WORK = {'running': False, 'again': False, 'at': -1e9, 'forced': None, 'tried': {}}
_LISTED = {'key': None, 'ids': frozenset()}


def folder():
    return core.data_path(FOLDER)


def valid_id(aid):
    """True for an artifact's id: 16 to 128 letters, digits, - and _ (so never a path)."""
    return isinstance(aid, str) and bool(ID_RE.fullmatch(aid))


def path_of(aid):
    """Where an artifact's picture is kept. Only for a valid id."""
    if not valid_id(aid):
        raise ValueError('not an artifact id')
    return os.path.join(folder(), aid + '.jpg')


def _pictures():
    """{id: (time it was made, bytes)} of every picture kept."""
    out = {}
    try:
        with os.scandir(folder()) as it:
            for e in it:
                aid = e.name[:-4] if e.name.endswith('.jpg') else None
                if valid_id(aid) and e.is_file(follow_symlinks=False):
                    s = e.stat(follow_symlinks=False)
                    out[aid] = (s.st_mtime, s.st_size)
    except OSError:
        pass
    return out


def known():
    """The ids of the artifacts with a picture (the page's `preview: true`). Nothing in the demo, whose artifacts
    are made up."""
    return set() if core.DEMO else set(_pictures())


# ---------- the page asks for one ----------

def _listed():
    """The ids of the artifacts the page lists (the saved state's), read again only when the state file changed."""
    p = core.data_path('state.json')
    try:
        s = os.stat(p)
        key = (s.st_ino, s.st_size, s.st_mtime_ns)
    except OSError:
        key = None
    if key is None or key != _LISTED['key']:
        ids = frozenset(x['id'] for x in core.load().get('artifacts') or []
                        if isinstance(x, dict) and isinstance(x.get('id'), str))
        _LISTED.update(key=key, ids=ids)
    return _LISTED['ids']


def picture(aid, listed=None):
    """The JPEG of one artifact's page, for GET /api/preview/<id>, or None. The id must look like an artifact's and be
    one the page lists (`listed`: their ids; the saved state's when None). The file's name is made from that checked
    id, a link put in the folder is not followed, and only a whole JPEG of at most MAX_PICTURE bytes is answered."""
    if not valid_id(aid) or aid not in (_listed() if listed is None else listed):
        return None
    try:
        fd = os.open(path_of(aid), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PICTURE:
            return None
        chunks, left = [], info.st_size
        while left > 0:
            got = os.read(fd, left)
            if not got:
                break
            chunks.append(got)
            left -= len(got)
        data = b''.join(chunks)
    except OSError:
        return None
    finally:
        os.close(fd)
    return data if jpeg_size(data) else None


def jpeg_size(data):
    """(width, height) of a whole JPEG, or None when `data` is not one: its start and end, and a frame header before
    its first scan."""
    if not isinstance(data, (bytes, bytearray)) or len(data) < 12 or data[:3] != b'\xff\xd8\xff' \
            or data[-2:] != b'\xff\xd9':
        return None
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:                  # a fill byte
            i += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = (data[i + 2] << 8) | data[i + 3]
        if length < 2 or marker == 0xDA:    # a scan before any frame header
            return None
        if marker in SOF:
            if i + 9 > len(data):
                return None
            h, w = (data[i + 5] << 8) | data[i + 6], (data[i + 7] << 8) | data[i + 8]
            return (w, h) if w and h else None
        i += 2 + length
    return None


# ---------- drawing one ----------

def chrome():
    """The browser program to draw with: a stand-in in tests (CHROME), else the program inside Google Chrome, Brave
    or Edge in /Applications or ~/Applications (browser.app_bundle), or None: then no picture."""
    if CHROME is not None:
        return CHROME if os.path.isfile(CHROME) and os.access(CHROME, os.X_OK) else None
    for k in ('chrome', 'brave', 'edge'):
        bundle = browser.app_bundle(k)
        if bundle:
            p = os.path.join(bundle, 'Contents', 'MacOS', browser.BROWSERS[k]['app'])
            if os.path.isfile(p) and os.access(p, os.X_OK):
                return p
    return None


def _background(cmd):
    """A command run at background priority (macOS's taskpolicy -b), when that tool is here."""
    return [TASKPOLICY, '-b'] + cmd if os.path.isfile(TASKPOLICY) else cmd


def command(program, src, profile, shot):
    """Headless Chrome drawing one local page into `shot`: a profile of its own (`profile`, a fresh folder), no
    Keychain, no extensions, no first-run pages, no background traffic, only the sites a published page may load. A
    permission the page asks for is refused, not left waiting: a page that asks for this Mac's own addresses would
    otherwise wait on Chrome's local-network question until its deadline, and gets nothing from them either way."""
    rules = 'MAP * ~NOTFOUND, ' + ', '.join('EXCLUDE ' + h for h in SITES)
    return _background([program, '--headless=new', '--disable-gpu', '--hide-scrollbars', '--mute-audio',
                        '--no-first-run', '--no-default-browser-check', '--disable-extensions', '--disable-sync',
                        '--disable-background-networking', '--disable-component-update', '--use-mock-keychain',
                        '--password-store=basic', '--deny-permission-prompts', '--user-data-dir=' + profile,
                        '--window-size=%d,%d' % VIEW, '--force-device-scale-factor=%s' % SCALE,
                        '--virtual-time-budget=%d' % BUDGET_MS, '--host-resolver-rules=' + rules,
                        '--screenshot=' + shot, pathlib.Path(src).as_uri()])


def _end(proc):
    """Chrome and the helpers it started (its own process group, started for it), ended; then waited for."""
    import signal
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _read(path, limit):
    try:
        with open(path, 'rb') as f:
            data = f.read(limit + 1)
    except OSError:
        return None
    return data if data and len(data) <= limit else None


def _small(data, tmp):
    """A JPEG about WIDTH px wide: Chrome's own when it is that already, else made by macOS's sips (which also turns
    a PNG from an older Chrome into a JPEG). Chrome's own, as it is, when sips can't."""
    size = jpeg_size(data)
    png = data[:8] == b'\x89PNG\r\n\x1a\n'
    if size and size[0] <= WIDTH * 1.1:
        return data
    if not (size or png) or not os.path.isfile(SIPS):
        return data if size else None
    big, out = os.path.join(tmp, 'big.png' if png else 'big.jpg'), os.path.join(tmp, 'small.jpg')
    with open(big, 'wb') as f:
        f.write(data)
    try:
        subprocess.run(_background([SIPS, '-s', 'format', 'jpeg', '-s', 'formatOptions', str(QUALITY),
                                    '--resampleWidth', str(WIDTH), big, '--out', out]),
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=SIPS_DEADLINE)
    except (OSError, subprocess.SubprocessError):
        pass
    small = _read(out, MAX_PICTURE)
    if small and jpeg_size(small):
        return small
    return data if size else None


def render(src, program=None, deadline=None, why=None):
    """The JPEG of one local page, drawn in headless Chrome, or None (and why, in the log; `why`, a dict, gets 'late':
    True when the deadline ended it). A fresh temporary profile, removed after; past the deadline Chrome and its
    helpers are ended."""
    program = program or chrome()
    if not program or not os.path.isfile(src) or not os.access(src, os.R_OK):
        return None                         # never a picture of the browser's own "file not found" page
    deadline = DEADLINE if deadline is None else deadline
    tmp = tempfile.mkdtemp(prefix=TEMP_PREFIX)
    try:
        shot = os.path.join(tmp, 'shot.jpg')
        try:
            proc = subprocess.Popen(command(program, src, os.path.join(tmp, 'profile'), shot),
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    close_fds=True, start_new_session=True)
        except OSError as e:
            log('a page picture could not start the browser: %s' % (e.strerror or e))
            return None
        try:
            proc.wait(timeout=deadline)
        except subprocess.TimeoutExpired:
            _end(proc)
            log('a page picture took over %d s, so it was stopped' % deadline)
            if why is not None:
                why['late'] = True
            return None
        if not os.path.isfile(src):
            log('a page file went while it was being drawn, so it has no picture')
            return None
        data = _read(shot, 4 * MAX_PICTURE)
        if data is None:
            log('the browser made no page picture (exit %s)' % proc.returncode)
            return None
        data = _small(data, tmp)
        if not data or len(data) > MAX_PICTURE:
            log('the browser made no usable page picture')
            return None
        return data
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- what to draw, and the worker that draws it ----------

def todo(st, now=None):
    """[(id, file, its publish's time)] of the pages to draw, newest first: a listed artifact whose publish named a
    local .html file that is still here (a plain file, not too large), unchanged since that publish, and whose picture
    is missing or older than that publish; not within an hour of a failure, nor within a day of the last of TRIES
    failures since that publish. A file changed since (a session wrote the same file again for another page, or edited
    it after publishing) holds another page now: that artifact keeps its title tile until it is published again."""
    now = time.time() if now is None else now
    have = _pictures()
    out = []
    for x in st.get('artifacts') or []:
        aid, src, ft = x.get('id'), x.get('file'), x.get('ft') or 0
        if not valid_id(aid) or not isinstance(src, str) or not os.path.isabs(src) or not src.lower().endswith(EXTS):
            continue
        if aid in have and have[aid][0] >= ft:
            continue
        tried = WORK['tried'].get(aid)
        if tried and tried[2] == ft and 0 <= now - tried[1] < (AGAIN_AFTER if tried[0] >= TRIES else RETRY_AFTER):
            continue
        try:
            info = os.stat(src)
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode) and info.st_size <= MAX_SOURCE and info.st_mtime <= ft + EDITED:
            out.append((aid, src, ft))
    return sorted(out, key=lambda x: -x[2])


def keep(aid, data):
    """Keep one picture: whole or not at all, only you can read it."""
    core.write_bytes(path_of(aid), data, 0o600)


def tidy(st=None, now=None):
    """Pictures of artifacts no longer listed go (never all of them for a list that can't be read), then the oldest
    while there are more than MAX_FILES or they hold more than MAX_BYTES, and what a write cut short left. How many
    went."""
    st = core.load() if st is None else st
    listed = set(x['id'] for x in st.get('artifacts') or [] if isinstance(x, dict) and isinstance(x.get('id'), str))
    gone, kept = 0, []
    for aid, (made, size) in _pictures().items():
        if listed and aid not in listed:
            try:
                os.unlink(path_of(aid))
                gone += 1
            except OSError:
                pass
            continue
        kept.append((made, size, aid))
    kept.sort()
    count, total = len(kept), sum(k[1] for k in kept)
    while kept and (count > MAX_FILES or total > MAX_BYTES):
        made, size, aid = kept.pop(0)
        try:
            os.unlink(path_of(aid))
            gone += 1
        except OSError:
            pass
        count, total = count - 1, total - size
    if os.path.isdir(folder()):
        core.sweep_temporary(folder(), now=now)
    return gone


def _draw(st, program, now=None):
    """Each page to draw (todo), newest first, one at a time, unless the Mac crawls or the disk is almost full:
    (made, failed). A draw the deadline ended while the Mac was busy (CALM) waits an hour and does not count."""
    made = failed = 0
    for aid, src, ft in todo(st, now) if program else []:
        if browser.load_per_core() >= BUSY or core.disk_low():
            break
        why = {}
        data = render(src, program, why=why)
        if data:
            keep(aid, data)
            WORK['tried'].pop(aid, None)
            made += 1
        else:
            n, _, was = WORK['tried'].get(aid) or (0, 0, None)
            n = n if was == ft else 0
            busy = why.get('late') and browser.load_per_core() >= CALM
            WORK['tried'][aid] = (n if busy else n + 1, time.time() if now is None else now, ft)
            failed += 1
    return made, failed


def sweep_profiles(older_than=3600):
    """The temporary profiles a page stopped in the middle of a picture left behind (this program's own folders in
    the temporary folder, an hour old or more, by the clock now): removed. How many."""
    base, now, gone = tempfile.gettempdir(), time.time(), 0
    try:
        names = [n for n in os.listdir(base) if n.startswith(TEMP_PREFIX)]
    except OSError:
        return 0
    for name in names:
        p = os.path.join(base, name)
        try:
            info = os.lstat(p)
        except OSError:
            continue
        if stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and now - info.st_mtime > older_than:
            shutil.rmtree(p, ignore_errors=True)
            gone += 1
    return gone


def run_once(now=None, program=None):
    """One round of the worker, in the calling thread: the pages to draw (todo), then, once, the files looked for in
    all history (artifacts.backfill_files) and the pages that gives; then tidy. A page published a moment ago never
    waits for that look through history. Returns {'made', 'failed', 'gone'}."""
    from . import artifacts
    sweep_profiles()
    program = program or chrome()
    made, failed = _draw(core.load(), program, now)
    if core.load().get('files_v') != artifacts.FILES_V:
        artifacts.backfill_files()
        more = _draw(core.load(), program, now)
        made, failed = made + more[0], failed + more[1]
    return {'made': made, 'failed': failed, 'gone': tidy(core.load(), now)}


def enabled():
    """Pictures are made only in the page's own process (never by a command in a terminal), and in tests only when a
    test turns them on with its stand-ins (WORK['forced'])."""
    if WORK['forced'] is not None:
        return bool(WORK['forced'])
    return bool(linkapp.PAGE['up']) and not core._in_tests()


def _lower_priority():
    """This thread at macOS's background priority (its CPU and its disk reads), when the system allows it."""
    try:
        os.setpriority(getattr(os, 'PRIO_DARWIN_THREAD', 3), 0, getattr(os, 'PRIO_DARWIN_BG', 0x1000))
    except (AttributeError, OSError, ValueError):
        pass


def _work():
    try:
        _lower_priority()
        while True:
            WORK['again'] = False
            got = run_once()
            if got['made'] or got['failed']:
                log('page pictures: %d made, %d not' % (got['made'], got['failed']))
            if not WORK['again']:
                break
    except Exception as e:  # noqa: BLE001 - pictures never stop the page
        log('page pictures failed: %r' % e)
    finally:
        WORK['running'] = False
        if WORK['again']:                   # a publish came in as this round ended: the next pass starts again
            WORK['at'] = -1e9


def kick(new=False):
    """From the page's loop: start the worker (one thread, one picture at a time) when it may run, is not running,
    and there may be work: a publish just came in (`new`), or EVERY seconds passed. A publish that comes in while it
    runs gets one more round. No browser: nothing starts. True when it started."""
    if not enabled():
        return False
    if WORK['running']:
        WORK['again'] = WORK['again'] or bool(new)
        return False
    mono = time.monotonic()
    if not new and 0 <= mono - WORK['at'] < EVERY:
        return False
    WORK['at'] = mono
    if chrome() is None:
        return False
    WORK['running'] = True
    threading.Thread(target=_work, name='switchboard-previews', daemon=True).start()
    return True
