"""The browser: profiles, opening a link in one of them, and reading what loaded.

Each Claude account lives in its own browser profile, signed in to claude.ai
once. Switchboard never signs anything in or out. It only chooses which profile
a link opens in, then reads the new tab's address and title to see what happened.

Google Chrome is the tested browser. Brave and Edge are built on the same code
and use the same paths here, but have not been tested.

Reading tabs needs macOS's Automation permission (Switchboard controlling the browser). Whether it is there is asked
of macOS itself (consent(): AEDeterminePermissionToAutomateTarget, with prompting off), never guessed from how long
a call took: a slow answer is "slow", and only the consent check can say "macOS is waiting for your OK". The
permission is asked for only by ask_consent(), which only a button the person presses calls.

Every outside program and clock goes through a name here (OPEN, OSASCRIPT, PGREP, _run, _spawn, _mono, _sleep,
CONSENT_CALL, LOAD), so tests put fakes in their place and nothing reaches the real browser.
"""
import glob
import json
import os
import re
import subprocess
import threading
import time

from .core import home, log, run, settings


def support():
    """~/Library/Application Support, from $HOME each time."""
    return os.path.join(home(), 'Library/Application Support')


BROWSERS = {
    'chrome': {'app': 'Google Chrome', 'bundle': 'com.google.Chrome', 'dir': 'Google/Chrome'},
    'brave': {'app': 'Brave Browser', 'bundle': 'com.brave.Browser', 'dir': 'BraveSoftware/Brave-Browser'},
    'edge': {'app': 'Microsoft Edge', 'bundle': 'com.microsoft.edgemac', 'dir': 'Microsoft Edge'},
}
# Titles of pages that stand in front of the real one (a security check, an outage).
HOLDING = ('just a moment', 'attention required', 'something went wrong', 'access denied', 'checking your browser')
# Of those, the ones that mean claude.ai itself is having trouble (said as that, never as "the wrong account").
TROUBLE = ('something went wrong', 'claude.ai is currently unavailable', 'service unavailable', 'bad gateway',
           'internal server error', 'gateway timeout', 'claude will return soon')
PROFILE_RE = re.compile(r'^[A-Za-z0-9 _-]{1,40}$')
BLIND = {'until': 0}    # (wall clock) while tab reading is paused; the page shows it as "tabs blocked"
HINTS = {'at': 0, 'key': None, 'value': {}}
APPS = {}               # (app, folders) -> (when it was looked for, where it is or None)
APP_FRESH = 60          # an app is looked for at most once a minute

# The outside world, by name, so tests can stand in for it.
OPEN = '/usr/bin/open'
OSASCRIPT = '/usr/bin/osascript'
PGREP = '/usr/bin/pgrep'
CONSENT_CALL = None     # tests: f(bundle_id, ask) -> an OSStatus number, or raises; None: macOS's own (ctypes)
# The consent check speaks for the process that makes it. It is the page's (linkapp.announce_port turns this on):
# a command in a terminal is a person asking, and its own answer from macOS is the terminal's, not the page's.
PAGE_PROCESS = {'on': False}
LOAD = None             # tests: f() -> load per core; None: os.getloadavg() / cores


def _run(cmd, timeout):
    """A program that answers: CompletedProcess (text), or subprocess.TimeoutExpired. Through core.run, by the name
    this module keeps for it (`run`): what stands in for `browser.run` stands in here too."""
    return run(cmd, timeout=timeout)


def _spawn(args):
    """A program that is started and left to run (open hands the link to the browser and leaves). A thread of its own
    waits for it, so it never lingers as a finished child."""
    p = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    threading.Thread(target=p.wait, name='switchboard-open', daemon=True).start()


def _mono():
    return time.monotonic()


def _sleep(s):
    time.sleep(s)


def load_per_core():
    """The 1-minute load divided by the cores (0.0 when it can't be read)."""
    if LOAD is not None:
        return float(LOAD())
    try:
        return os.getloadavg()[0] / max(1, os.cpu_count() or 1)
    except (OSError, AttributeError):
        return 0.0


def app_dirs():
    """Where a browser's app is installed: /Applications, or the person's own ~/Applications."""
    return ['/Applications', os.path.join(home(), 'Applications')]


def app_bundle(k):
    """The browser's app, e.g. /Applications/Google Chrome.app, or None when it is in neither Applications folder.
    Looked for by its path, and remembered for a minute."""
    name, dirs, now = BROWSERS[k]['app'] + '.app', tuple(app_dirs()), time.time()
    seen = APPS.get((name, dirs))
    if seen and 0 <= now - seen[0] < APP_FRESH:
        return seen[1]
    found = next((p for p in (os.path.join(d, name) for d in dirs) if os.path.isdir(p)), None)
    APPS[(name, dirs)] = (now, found)
    return found


def usable(k):
    """Both halves of a browser are here: its data folder (it has been opened once) and its app. A data folder alone
    is what a removed browser leaves behind, and a link sent to one of its profiles would never open."""
    return os.path.exists(os.path.join(support(), BROWSERS[k]['dir'], 'Local State')) and app_bundle(k) is not None


def kind():
    """Which browser holds the profiles: the one chosen in settings while it is here, else the first one found, else
    the chosen one, else Chrome. A browser that was chosen and then removed does not hide one that is here."""
    chosen = settings().get('browser')
    chosen = chosen if chosen in BROWSERS else None
    if chosen and usable(chosen):
        return chosen
    for k in BROWSERS:
        if usable(k):
            return k
    return chosen or 'chrome'


def info():
    return BROWSERS[kind()]


def app():
    return info()['app']


def data_dir():
    return os.path.join(support(), info()['dir'])


def present():
    """A browser to keep profiles in is here: opened once (its data folder) and still installed (its app)."""
    return usable(kind())


def profiles():
    """Every profile as {folder: name people see}. A profile Chrome never named shows its folder name. {} when Chrome's
    Local State can't be read: then nothing is known about any profile (it is never taken as "every profile gone")."""
    try:
        with open(os.path.join(data_dir(), 'Local State')) as f:
            cache = json.load(f).get('profile', {}).get('info_cache', {})
    except (OSError, ValueError, AttributeError):
        return {}
    out = {}
    for folder, v in cache.items() if isinstance(cache, dict) else []:
        if not isinstance(v, dict):
            continue
        name = v.get('name') or folder
        if v.get('is_using_default_name') and not folder.startswith('Profile ') and folder != 'Default':
            name = folder
        out[folder] = name
    return out


def new_profile_name(label):
    """A folder name for a fresh profile kept for one account, e.g. 'Claude work'."""
    base = 'Claude ' + (re.sub(r'[^A-Za-z0-9 _-]', '', label).strip() or 'account')[:28]
    have, name, n = profiles(), base, 2
    while name in have or os.path.exists(os.path.join(data_dir(), name)):
        name, n = '%s %d' % (base, n), n + 1
    return name


def open_url(profile, url, background=False):
    """Open a link in one profile. A profile that doesn't exist yet is created by the browser. A profile name that
    isn't a plain folder name is never passed on (the link then opens where the browser decides)."""
    args = [OPEN, '-gna' if background else '-na', app(), '--args']
    if profile and PROFILE_RE.match(profile):
        args.append('--profile-directory=' + profile)
    _spawn(args + [url])


def running():
    """The browser is running now (pgrep by its exact process name; a pgrep that can't run says no)."""
    try:
        return _run([PGREP, '-x', app()], timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


# ---------- may Switchboard read the browser's tabs? macOS says, through its consent API ----------

ALLOWED, DENIED, WOULD_ASK, NOT_RUNNING, UNKNOWN = 'allowed', 'denied', 'would_ask', 'not_running', 'unknown'
CODES = {0: ALLOWED, -1743: DENIED, -1744: WOULD_ASK, -600: NOT_RUNNING}   # noErr, errAEEventNotPermitted, ...
CONSENT_DEADLINE = 5        # seconds a consent check may take before it counts as "not known"
CONSENT_FRESH = {ALLOWED: 30, DENIED: 30, WOULD_ASK: 10, NOT_RUNNING: 5, UNKNOWN: 15}
CONSENT = {'state': None, 'code': None, 'at': -1e9, 'asking': False}
_CONSENT_LOCK = threading.Lock()


def _fourcc(s):
    return int.from_bytes(s.encode('ascii'), 'big')


def _consent_ctypes(bundle_id, ask):
    """macOS's own answer: AEDeterminePermissionToAutomateTarget for the app with this bundle identifier, any Apple
    event (typeWildCard). ask=False never shows a question; ask=True may show macOS's question and waits for it."""
    import ctypes

    class AEDesc(ctypes.Structure):
        _fields_ = [('descriptorType', ctypes.c_uint32), ('dataHandle', ctypes.c_void_p)]
    cs = ctypes.CDLL('/System/Library/Frameworks/CoreServices.framework/CoreServices')
    create, determine, dispose = cs.AECreateDesc, cs.AEDeterminePermissionToAutomateTarget, cs.AEDisposeDesc
    create.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_long, ctypes.POINTER(AEDesc)]
    create.restype = ctypes.c_int16
    determine.argtypes = [ctypes.POINTER(AEDesc), ctypes.c_uint32, ctypes.c_uint32, ctypes.c_ubyte]
    determine.restype = ctypes.c_int32
    dispose.argtypes = [ctypes.POINTER(AEDesc)]
    dispose.restype = ctypes.c_int16
    raw = bundle_id.encode('utf-8')
    buf = ctypes.create_string_buffer(raw, len(raw))
    desc = AEDesc()
    err = create(_fourcc('bund'), ctypes.cast(buf, ctypes.c_void_p), len(raw), ctypes.byref(desc))
    if err != 0:
        return int(err)
    try:
        return int(determine(ctypes.byref(desc), _fourcc('****'), _fourcc('****'), 1 if ask else 0))
    finally:
        dispose(ctypes.byref(desc))


def _consent_code(bundle_id, ask, deadline):
    """The consent call's number, or None when it failed or took longer than the deadline (it runs in a thread of its
    own, so a slow macOS can never hold up the caller past the deadline)."""
    box = {}

    def call():
        try:
            box['code'] = (CONSENT_CALL or _consent_ctypes)(bundle_id, ask)
        except Exception as e:  # noqa: BLE001 - no consent API here (an old macOS, no ctypes): not known
            box['error'] = e
    t = threading.Thread(target=call, name='switchboard-consent', daemon=True)
    t.start()
    t.join(deadline)
    if t.is_alive() or 'code' not in box:
        return None
    try:
        return int(box['code'])
    except (TypeError, ValueError):
        return None


def consent(fresh=False):
    """May Switchboard send the browser Apple events (read its tabs)? 'allowed', 'denied', 'would_ask' (macOS has
    not asked yet: it would ask on the first try), 'not_running' (the browser isn't open, so macOS can't say) or
    'unknown' (the check failed or was slow). Never shows a question. Remembered for a few seconds."""
    with _CONSENT_LOCK:
        now = _mono()
        if not fresh and CONSENT['state'] and now - CONSENT['at'] < CONSENT_FRESH.get(CONSENT['state'], 10):
            return CONSENT['state']
    code = _consent_code(info()['bundle'], False, CONSENT_DEADLINE)
    state = CODES.get(code, UNKNOWN)
    with _CONSENT_LOCK:
        CONSENT.update(state=state, code=code, at=_mono())
    return state


def ask_consent(deadline=120):
    """Ask macOS for the permission (it shows its own question). Only for a button the person pressed: it waits up to
    `deadline` seconds for their answer, so call it off the request path. Returns the state after."""
    with _CONSENT_LOCK:
        if CONSENT['asking']:
            return 'asking'
        CONSENT['asking'] = True
    try:
        code = _consent_code(info()['bundle'], True, deadline)
        state = CODES.get(code, UNKNOWN)
        with _CONSENT_LOCK:
            CONSENT.update(state=state, code=code, at=_mono())
        if state == ALLOWED:
            _clear_pause()
        return state
    finally:
        with _CONSENT_LOCK:
            CONSENT['asking'] = False


# ---------- reading tabs ----------

# Inside the browser's "tell" block the word "tab" means a browser tab, so the separator character gets its own name
# first. The whole script runs only while the browser is running: a script that names an app that isn't running
# would start it (a race between a pgrep and the script could launch the browser). At most MAX_TABS claude.ai tabs,
# the front window first, where a tab just opened lands.
MAX_TABS = 200
TABS_OSA = '''set sep to character id 9
set out to ""
if application "%s" is running then
  tell application "%s"
    set n to 0
    repeat with w in windows
      repeat with t in tabs of w
        if (URL of t) contains "claude.ai" then
          set out to out & (id of w) & sep & (id of t) & sep & (URL of t) & sep & (title of t) & linefeed
          set n to n + 1
          if n is greater than or equal to %d then return out
        end if
      end repeat
    end repeat
  end tell
end if
return out'''

TABS = {'state': None, 'why': None, 'until': 0.0, 'backoff': 0.0, 'said': None}
BACKOFF_FIRST, BACKOFF_MAX = 30, 300     # a slow browser: wait 30 s, doubling, at most 5 minutes
TAB_WORDS = {
    'ok': "Switchboard can read %(app)s's tab titles.",
    'slow': '%(app)s was slow to answer (the Mac is busy). Switchboard opens links without checking them for now '
            'and looks again in %(wait)d s.',
    DENIED: "macOS has not allowed Switchboard to control %(app)s. Allow it in System Settings > Privacy & "
            "Security > Automation.",
    WOULD_ASK: "Switchboard needs your OK to read %(app)s's tab titles. Press Let Switchboard check links, then "
               "answer macOS.",
    NOT_RUNNING: "%(app)s isn't open.",
    UNKNOWN: "Switchboard couldn't tell whether it may read %(app)s's tabs.",
}


def tab_timeout():
    """How long one look at the tabs may take: 8 s on a quiet Mac, longer under load (never a verdict from time)."""
    lpc = load_per_core()
    return 8 if lpc < 1.5 else 12 if lpc < 3 else 20


_LOOK = object()


def tab_access(said=_LOOK):
    """{'state', 'text'} for the page: whether Switchboard may read the browser's tabs, and what to do about it:
    'ok', 'slow' (waiting out a slow browser), 'denied', 'would_ask', 'not_running' or 'unknown'. macOS's own answer
    (consent) comes first in the page's process; then what the last look found. `said`: macOS's answer as the caller
    already has it (the page's background look), so building the page's state never waits on macOS itself."""
    if said is _LOOK:
        said = consent() if PAGE_PROCESS['on'] else None
    if said in (DENIED, WOULD_ASK, NOT_RUNNING):
        state = said
    elif TABS['state'] == 'slow':                   # the last look was slow; the next one says again
        state = 'slow'
    elif said == ALLOWED:
        state = 'ok'
    else:
        state = TABS['state'] if TABS['state'] in ('ok', DENIED, WOULD_ASK, NOT_RUNNING) else UNKNOWN
    wait = max(0, int(TABS['until'] - _mono())) if state == 'slow' else 0
    return {'state': state, 'text': TAB_WORDS.get(state, TAB_WORDS[UNKNOWN]) % {'app': app(), 'wait': wait}}


def _pause(state, seconds, why):
    TABS.update(state=state, until=_mono() + seconds, why=why)
    BLIND['until'] = time.time() + seconds
    if TABS.get('said') != (state, why):
        TABS['said'] = (state, why)
        log(why)


def _clear_pause():
    TABS.update(until=0.0, backoff=0.0)
    BLIND['until'] = 0


def _slow(why):
    """The browser didn't answer in time: wait 30 s, doubling each time, at most 5 minutes. Slow is never "blocked"."""
    TABS['backoff'] = min(BACKOFF_MAX, max(BACKOFF_FIRST, TABS['backoff'] * 2))
    _pause('slow', TABS['backoff'], '%s; links open without the check for %d s' % (why, TABS['backoff']))
    return None


def tabs(tries=6, asked=False):
    """claude.ai tabs as [(window id, tab id, address, title)], [] when the browser isn't running, or None when they
    can't be read now (macOS said no, hasn't asked yet, or the browser was slow: TABS['state'] says which).
    asked: for a button the person pressed. In the page's process, when macOS's answer can't be told and no read has
    worked yet, only a press reads the tabs: the read itself could make macOS ask."""
    if _mono() < TABS['until']:
        return None
    state = consent() if PAGE_PROCESS['on'] else UNKNOWN
    if state == UNKNOWN and PAGE_PROCESS['on'] and not asked and TABS.get('state') != 'ok':
        TABS.update(state=UNKNOWN, why=None)
        return None
    if state == DENIED:
        TABS.update(state=DENIED, why=None)
        return None
    if state == WOULD_ASK:
        TABS.update(state=WOULD_ASK, why=None)
        return None
    if state == NOT_RUNNING:
        TABS.update(state=NOT_RUNNING, why=None)
        return []
    timeout = tab_timeout()
    for _ in range(tries):
        try:
            r = _run([OSASCRIPT, '-e', TABS_OSA % (app(), app(), MAX_TABS)], timeout=timeout)
        except subprocess.TimeoutExpired:
            return _slow('%s did not answer within %d s' % (app(), timeout))
        if r.returncode == 0:
            TABS.update(state='ok', why=None, backoff=0.0)
            return [tuple(p) for p in (line.split('\t', 3) for line in r.stdout.splitlines()) if len(p) == 4]
        if '(-1743)' in (r.stderr or ''):
            with _CONSENT_LOCK:
                CONSENT.update(state=DENIED, code=-1743, at=_mono())
            TABS.update(state=DENIED, why=None)
            return None
        if '(-1744)' in (r.stderr or ''):
            with _CONSENT_LOCK:
                CONSENT.update(state=WOULD_ASK, code=-1744, at=_mono())
            TABS.update(state=WOULD_ASK, why=None)
            return None
        _sleep(0.5)                          # the browser is busy taking a new link (-609 / -600); ask again
    TABS.update(state=UNKNOWN, why='the browser kept refusing')
    return None


def _blind(why):
    """Kept for older callers: a pause in reading tabs, always the "slow" back-off."""
    return _slow(why)


def can_see_tabs(ask=False):
    """True (the tabs can be read), False (macOS said no, or hasn't been asked yet) or None (the browser is closed,
    so macOS can't say). ask=True is for a button the person pressed: macOS's question is shown when it hasn't been
    asked yet."""
    if not running():
        return None
    _clear_pause()
    state = consent(fresh=True) if PAGE_PROCESS['on'] else UNKNOWN
    if state == WOULD_ASK and ask:
        state = ask_consent()
    if state in (DENIED, WOULD_ASK):
        TABS.update(state=state)
        return False
    if state == NOT_RUNNING:
        return None
    return tabs(tries=2, asked=ask) is not None


def close_tab(win, tab):
    try:
        _run([OSASCRIPT, '-e', 'if application "%s" is running then tell application "%s" to close '
              '(every tab of window id %d whose id is %d)' % (app(), app(), int(win), int(tab))], timeout=8)
    except (subprocess.TimeoutExpired, ValueError, TypeError, OSError):
        pass


def page_verdict(url, title):
    """What a claude.ai tab turned out to be: 'ok', 'wrong' account, needs 'signin', or None while loading."""
    path = url.split('claude.ai', 1)[-1]
    if path.startswith(('/login', '/logout', '/magic-link', '/onboarding')):
        return 'signin'
    t = title.strip()
    low = t.lower()
    if low.startswith('page not found'):
        return 'wrong'
    if '/artifact/' not in path:
        return None                          # it wandered off to another page, which proves nothing
    if not t or t in ('Claude', 'Untitled') or low == 'error' or low.startswith(('claude.ai', 'http') + HOLDING):
        return None
    return 'ok'


def trouble(title):
    """True when a tab's title says claude.ai itself is having trouble (its own outage page: the title is the outage
    words, give or take a full stop or " – Claude"), never for a page whose own title merely starts with them."""
    low = clean_title(title or '').lower().rstrip('.! ')
    return any(low == t or (low.startswith(t) and len(low) <= len(t) + 3) for t in TROUBLE)


def clean_title(title):
    return re.sub(r'\s+[–|-]\s+Claude$', '', title.strip())


def watch_tab(needle, before, timeout=15):
    """Follow the tab the browser just opened (its address contains needle) until the page settles. Returns
    (verdict, tab): 'ok', 'wrong', 'signin', 'trouble' (claude.ai's own outage page stayed up to the deadline) or None
    (nothing settled in time). Only a tab that was not there before is ever looked at."""
    end, tab, last = _mono() + timeout, None, None
    while _mono() < end:
        _sleep(0.45)
        for row in tabs() or []:
            if row[1] in before or needle not in row[2]:
                continue
            tab = row
            verdict = page_verdict(row[2], row[3])
            # "Page not found" and the sign-in page are unmistakable. A real page has
            # to give the same title twice in a row, so a half-loaded one can't fool us.
            if verdict in ('wrong', 'signin') or (verdict and (verdict, row[3]) == last):
                return verdict, tab
            last = (verdict, row[3])
            break
    if tab is not None and trouble(tab[3]):
        return 'trouble', tab
    return None, tab


# ---------- the Claude extension, in each profile ----------

CLAUDE_EXTENSION = 'fcoeoabgfenejglbffodgkkbkcdhcgfn'        # Claude, in the Chrome Web Store
CLAUDE_EXTENSION_PAGE = 'https://chromewebstore.google.com/detail/claude/' + CLAUDE_EXTENSION
# Claude Code's own native messaging host: the browser reaches Claude Code through it (one per browser, not per
# profile). Claude Code 2.1.325 (2026-11): NativeMessagingHosts/<this>.json, whose "path" is a
# program Claude Code keeps up to date and whose "allowed_origins" names the extension.
NATIVE_HOST = 'com.anthropic.claude_code_browser_extension'


def extensions(ext=CLAUDE_EXTENSION):
    """{profile folder: True when the extension is installed there (its folder under Extensions), else False}, for
    every profile. Only looked up by path; nothing in a profile is read."""
    base = data_dir()
    return dict((folder, os.path.isdir(os.path.join(base, folder, 'Extensions', ext))) for folder in profiles()
                if PROFILE_RE.match(folder))


def extension_versions(ext=CLAUDE_EXTENSION):
    """{profile folder: the installed version's folder name, or None when the extension isn't there}. By path only."""
    base, out = data_dir(), {}
    for folder in profiles():
        if not PROFILE_RE.match(folder):
            continue
        d = os.path.join(base, folder, 'Extensions', ext)
        try:
            versions = sorted(v for v in os.listdir(d) if not v.startswith('.'))
        except OSError:
            versions = []
        out[folder] = versions[-1] if versions else None
    return out


def native_host():
    """Claude Code's way into the browser: {'manifest': its file or None, 'program': the program it names or None,
    'ok': the program is there and can run, 'names_extension': its allowed origins include the extension}. Read-only:
    the manifest is a small JSON file the browser reads; nothing is run."""
    path = os.path.join(data_dir(), 'NativeMessagingHosts', NATIVE_HOST + '.json')
    try:
        with open(path, 'rb') as f:
            doc = json.loads(f.read(65536).decode('utf-8'))
    except (OSError, ValueError, UnicodeDecodeError):
        return {'manifest': None, 'program': None, 'ok': False, 'names_extension': False}
    program = doc.get('path') if isinstance(doc, dict) and isinstance(doc.get('path'), str) else None
    origins = doc.get('allowed_origins') if isinstance(doc, dict) and isinstance(doc.get('allowed_origins'), list) else []
    return {'manifest': path, 'program': program,
            'ok': bool(program) and os.path.isfile(program) and os.access(program, os.X_OK),
            'names_extension': 'chrome-extension://%s/' % CLAUDE_EXTENSION in origins}


# ---------- which account is each profile signed in to? ----------

def hints(org_ids, fresh=False):
    """{profile folder: set of the given organization ids found in that profile's own claude.ai data}.

    claude.ai keeps small notes in the browser (which sidebar is open, and so on),
    filed under the organization id of whoever is signed in. Reading those files
    is a quick, silent first guess at which profile belongs to which account. A
    profile used for one account names one id. A profile that has been signed in
    to several names several, and then only opening a real page settles it.
    The notes outlive a sign-out, so they are only ever a hint, never a proof.
    """
    wanted = sorted(o for o in org_ids if o and len(o) >= 16)
    if not fresh and HINTS['key'] == wanted and time.time() - HINTS['at'] < 600:
        return HINTS['value']
    out = {}
    for folder in profiles():
        base = os.path.join(data_dir(), folder)
        files = glob.glob(os.path.join(base, 'Local Storage', 'leveldb', '*')) + \
            glob.glob(os.path.join(base, 'IndexedDB', 'https_claude.ai_0.indexeddb.leveldb', '*'))
        found = set()
        for path in files:
            try:
                if not os.path.isfile(path) or os.path.getsize(path) > 64 * 1024 * 1024:
                    continue
                with open(path, 'rb') as f:
                    blob = f.read()
            except OSError:
                continue
            if b'claude.ai' not in blob:
                continue
            found.update(o for o in wanted if o.encode() in blob)
        if found:
            out[folder] = found
    HINTS.update(at=time.time(), key=wanted, value=out)
    return out


def guess_links(org_of, taken):
    """Match accounts to profiles from the hints.

    org_of: {email: organization id} for accounts that have no profile yet.
    taken: profile folders already linked to some account.
    Returns {email: profile folder}. A profile that names exactly one of the
    person's accounts goes to that account. After that, an account left over
    takes a shared profile only when it is the single one that fits.
    """
    by_profile = hints(set(org_of.values()))
    email_of = {}
    for email, org in org_of.items():
        email_of.setdefault(org, []).append(email)
    links, used = {}, set(taken)
    for folder, orgs in sorted(by_profile.items()):
        if folder in used or len(orgs) != 1:
            continue
        emails = [e for e in email_of.get(next(iter(orgs)), []) if e not in links]
        if len(emails) == 1:
            links[emails[0]] = folder
            used.add(folder)
    for email, org in org_of.items():
        if email in links:
            continue
        fits = [f for f, orgs in by_profile.items() if org in orgs and f not in used]
        if len(fits) == 1:
            links[email] = fits[0]
            used.add(fits[0])
    return links
