"""Adding an account: one sign-in that sets up both halves.

Claude Code's own sign-in opens a page on claude.ai. Switchboard opens that page
inside the browser profile chosen for the account. Signing in there does two
things at once: Claude Code gets its login (the engine keeps it, api.add_login), and
that browser profile is now signed in to claude.ai as the same account. From then
on, links for that account open in that profile and never ask again. On a Mac
with no Chrome, Brave or Edge (installed and opened once), the page opens in the
default browser instead: Claude Code still gets its login, with no profile to
link. That browser may already be signed in to claude.ai as an account that is
here; the page says so while it waits, and a sign-in that comes back as another
account than the one asked for is said as a warning.

The sign-in runs in a throwaway config folder, so the login Claude Code is using
right now is never touched. Claude Code keeps the new login in a temporary
Keychain item for that folder; the engine reads it from there itself, so no
login passes through this file. Switchboard deletes the item (through the engine,
api.remove_sign_in) and the folder as soon as the engine has the login, and again
at every start in case a restart cut a sign-in short (a folder whose item the
Keychain would not remove yet stays for that). When the engine could not
keep it for a reason that passes (no network, a slow Keychain), the folder and its
item stay and the engine is asked again every 30 seconds for 10 minutes: the
person never has to sign in twice for a hiccup.

Pressing Sign in a second time never throws a sign-in away. The page goes on saying "Sign in" until the engine has kept
the new login, and a person who has just approved it in the browser presses again. So a sign-in that has finished well
stays as it is (the engine is asked now), and one still waiting gets its own link opened again: the same link and the
same listener, so whichever tab is finished counts. Only a sign-in that failed is replaced. The moment a sign-in's
process ends, the page's loop is woken (ON_END), so the login is kept within seconds, not at the next reading.

A restart does not throw one away either. A sign-in that is over (handed to the engine, cancelled or timed out) is
marked in its folder (OVER) before anything is removed. At a start, a fresh folder without that mark whose process has
ended may hold a login the browser approved just before the restart, or during it: it is taken up again and handed to
the engine like any other. The engine never puts an older login over a newer one, and finds a login there or none.
"""
import glob
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

from . import browser, core, engine
from .core import (SHIM_DIR, data_path, home, SwitchError, ensure_account, event, log, name_of, read_json, read_text, run,
                   signed_in_here)

# The engine's refusals of a finished sign-in that pass by themselves: its login is kept for another try.
RETRY_KINDS = ('network', 'rate_limited', 'keychain_slow', 'keychain_locked', 'claude_busy')
RETRY_EVERY = 30        # seconds between two tries to keep a finished sign-in
RETRY_FOR = 600         # ... for this long after it finished; then it is said as not finished, and cleared
# Never handed to a sign-in: CLAUDE_SECURESTORAGE_CONFIG_DIR would make Claude Code write the new login into the item
# that folder names (the person's live login, for ~/.claude) instead of the sign-in folder's own; the rest give Claude
# Code a login of its own (the engine's facts.AUTH_OVERRIDES).
NOT_FOR_SIGN_IN = ('CLAUDE_SECURESTORAGE_CONFIG_DIR', 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN',
                   'CLAUDE_CODE_OAUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR',
                   'CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR')
# key -> {'proc', 'dir', 'started', 'email', 'profile', 'opened': 'profile' or 'default', 'url': its link, 'pressed':
# when its link was last opened}
RUNNING = {}
STARTING = {}                   # key -> a lock: one start at a time for one account, so two presses at the same
STARTING_LOCK = threading.Lock()    # moment are one sign-in, never two listeners with one of them forgotten
AGAIN_AFTER = 2                 # seconds: a second press sooner than this after the link opened is the same click
TIMES_OUT = 900                 # seconds a sign-in waits for the browser, counted from the last press
WATCH_EVERY = 0.25              # seconds between two looks at whether a sign-in's process has ended
KEPT_FROM_CANCEL = 120          # seconds after a sign-in finished in which a Cancel leaves it to be kept
OVER = 'over'                   # a file in a sign-in's folder: it was handed over, cancelled or timed out
ASKED = 'asked.json'            # a file in a sign-in's folder: the account asked for and the browser profile
TAKEN_UP_FOR = 1800             # seconds: a folder a restart left is handed to the engine only when this fresh
# What the server gives to be called when a sign-in's process ends, or when a second press finds one already finished:
# it wakes the page's loop, which hands the sign-in to the engine (collect). Without it a finished sign-in waited for
# the next reading, a minute away and up to four on a busy Mac, with the page still saying "Sign in".
ON_END = None
# A sign-in that came back as another account than the one asked for: said on the page, where its waiting row was,
# until hidden or for ten minutes. key -> {'at', 'email' (asked), 'got', 'line'}
NOTES = {}
NOTE_FRESH = 600
# How the last sign-in under each key ended, for a terminal that started it through the page and follows it (look):
# key -> {'started', 'at', 'result'}. Kept for ENDED_FRESH seconds; the page itself never reads it.
ENDED = {}
ENDED_FRESH = 600


CLAUDE_PATH = 'claude_path'     # settings.json: the claude a terminal's PATH found, for the page's job to find too


def claude_bin():
    """Claude Code's command: on the PATH; else the one a command in a terminal last found on the shell's PATH (the
    page's job runs with a short PATH of its own, without a version manager's folders); else the usual places."""
    found = shutil.which('claude')
    if found:
        _remember(found)
        return found
    kept = core.settings().get(CLAUDE_PATH)
    if isinstance(kept, str) and _only_yours(kept) and os.access(kept, os.X_OK):
        return kept
    h = home()
    for p in (os.path.join(h, '.local/bin/claude'), os.path.join(h, '.npm-global/bin/claude'),
              '/opt/homebrew/bin/claude', '/usr/local/bin/claude', os.path.join(h, '.claude/local/claude'),
              os.path.join(h, '.bun/bin/claude'), os.path.join(h, '.volta/bin/claude'),
              os.path.join(h, '.asdf/shims/claude'), os.path.join(h, '.local/share/mise/shims/claude'), _newest_nvm(h)):
        if p and os.path.exists(p):
            return p
    return None


def _newest_nvm(h):
    """claude in the newest Node that nvm keeps and that has it (~/.nvm/versions/node/v22.11.0/bin/claude), or None."""
    def version(path):
        name = os.path.basename(os.path.dirname(os.path.dirname(path))).lstrip('v')
        return tuple(int(x) if x.isdigit() else -1 for x in name.split('.'))
    found = glob.glob(os.path.join(h, '.nvm/versions/node/*/bin/claude'))
    return max(found, key=version) if found else None


def _only_yours(path):
    """Is path a file the page's job may run later as claude: named whole (a PATH entry of "." or "" finds whatever is
    in the folder one stands in), and, like its folder, yours or the system's and not open to others to replace."""
    if not os.path.isabs(path):
        return False
    try:
        for p in (path, os.path.dirname(path)):
            st = os.stat(p)
            if st.st_uid not in (0, os.getuid()) or st.st_mode & 0o022:
                return False
        return os.path.isfile(path)
    except OSError:
        return False


def _remember(path):
    """Keep where a terminal's PATH found claude (one setting, written only when it changes). Only a command run at a
    terminal: the page's job and the menu bar have no terminal, and their short PATH is not the person's. And only a
    file that passes _only_yours: the job runs what is remembered."""
    try:
        if core.DEMO or not sys.stdin.isatty() or not _only_yours(path) or core.settings().get(CLAUDE_PATH) == path:
            return
        core.update_setting(CLAUDE_PATH, lambda _: path)
    except Exception:  # noqa: BLE001 - remembering never stops Claude Code being found
        pass


def open_sign_in(profile, url):
    """The sign-in link: in the account's browser profile, or, when no profile can be used (no Chrome, Brave or Edge
    here), in the default browser. Claude Code gets its login the same way either way. Returns 'profile' or
    'default': the default browser may already be signed in to claude.ai as another account, so the page says so."""
    if profile and browser.present():
        browser.open_url(profile, url)
        return 'profile'
    subprocess.Popen(['/usr/bin/open', url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 'default'


class Key(str):
    """The name of one sign-in (what start returns), with what that start did: 'opened' (a new sign-in, its link
    opened), 'again' (one was still waiting: its own link, opened again), 'same' (the second half of a double click:
    nothing more opened) or 'kept' (one had already finished: nothing was opened, the engine is asked to keep it
    now); and the browser profile that sign-in is in (None: the default browser)."""
    did = 'opened'
    profile = None


def _key(key, did, profile=None):
    k = Key(key)
    k.did, k.profile = did, profile
    return k


def _wake():
    """Wake the page's loop (the server's ON_END); never fails the caller."""
    try:
        if ON_END:
            ON_END()
    except Exception as e:  # noqa: BLE001
        log('waking the loop for a sign-in failed: %r' % e)


def _watch(c):
    """Wake the page's loop when this sign-in's process ends, however it ends, and note when ('ended_at'). It looks
    every WATCH_EVERY seconds and never waits inside the process's own wait(): a thread waiting there makes poll() in
    every other thread answer "still going" until that thread has run again, and a Cancel in that gap would throw away
    a sign-in that had just finished. It stops looking once the sign-in is over."""
    def run():
        try:
            while c['proc'].poll() is None:
                if c.get('over'):
                    return
                time.sleep(WATCH_EVERY)
        except Exception:  # noqa: BLE001 - nothing to look at: the next reading finds it
            return
        c.setdefault('ended_at', time.time())
        if not c.get('over'):                   # (one that was cancelled has nothing to hand over)
            _wake()
    threading.Thread(target=run, name='sign-in-ended', daemon=True).start()


def under_way(c):
    """Where a sign-in stands: 'finished' (its process ended well: the new login waits in its folder's Keychain item
    for the engine), 'waiting' (still open in the browser), 'failed', or None for none."""
    if not c:
        return None
    rc = c['proc'].poll()
    return 'waiting' if rc is None else 'finished' if rc == 0 else 'failed'


def profile_of(key):
    """The browser profile of a sign-in for this key that is still waiting or has just finished, or None: a second
    press that asks for "a new profile" means the one this sign-in is already in, not one more."""
    c = RUNNING.get(key)
    return c.get('profile') if under_way(c) in ('waiting', 'finished') else None


def start(email, profile):
    """Begin a sign-in for email (may be '') in a profile folder (None: the default browser). Returns the key that
    names this attempt (a Key: its .did says what this call did).

    A second start for the same key never throws a sign-in away: one that finished well is left to be kept (the engine
    is asked now), one still waiting has its own link opened again. Only one that failed is stopped and replaced."""
    exe = claude_bin()
    if not exe:
        raise SwitchError('Claude Code is not installed on this Mac.')
    key = email or 'new'
    with STARTING_LOCK:
        one_at_a_time = STARTING.setdefault(key, threading.Lock())
    with one_at_a_time:
        return _start(exe, key, email, profile)


def _start(exe, key, email, profile):
    c = RUNNING.get(key)
    state = under_way(c)
    if state == 'finished':
        if c.get('retry_at'):
            c['retry_at'] = time.time()             # kept for another try: that try is due now
        _wake()
        return _key(key, 'kept', c.get('profile'))
    if state == 'waiting' and c.get('url'):
        now = time.time()
        if now - c.get('pressed', 0) < AGAIN_AFTER:
            return _key(key, 'same', c.get('profile'))      # the second half of a double click: one link is enough
        # In the profile it is in, whatever this press named: the first tab may be the one that gets approved, and
        # the account's profile is the one its sign-in was approved in.
        c['pressed'] = now
        c['opened'] = open_sign_in(c.get('profile'), c['url'])
        return _key(key, 'again', c.get('profile'))
    stop(key)
    d = os.path.join(data_path('logins'), hashlib.sha256(('%s %f' % (key, time.time())).encode()).hexdigest()[:10])
    os.makedirs(d, mode=0o700)
    url_file = os.path.join(d, 'url.txt')
    env = dict((k, v) for k, v in os.environ.items() if k not in NOT_FOR_SIGN_IN)
    # A separate config folder gives this sign-in its own Keychain item, so the
    # login Claude Code is using stays put. The `open` stand-in hands us the link. claude's own folder goes last: a
    # claude from npm under nvm starts with `env node`, and its node sits beside it, not on the page's short PATH.
    own = os.path.dirname(exe)
    own = own if os.path.isabs(own) else ''     # (a bare or relative name: "" or "." on a PATH would mean "here")
    env.update(PATH=':'.join(p for p in (SHIM_DIR, env.get('PATH') or '/usr/bin:/bin', own) if p), CLAUDE_CONFIG_DIR=d,
               SWITCHBOARD_URL_FILE=url_file)
    with open(os.path.join(d, 'out.txt'), 'w') as out:
        proc = subprocess.Popen([exe, 'auth', 'login', '--claudeai'] + (['--email', email] if email else []),
                                stdin=subprocess.PIPE, stdout=out, stderr=subprocess.STDOUT, env=env,
                                start_new_session=True)
    with open(os.path.join(d, 'pid'), 'w') as f:      # so a later start can find and stop it
        f.write(str(proc.pid))
    with open(os.path.join(d, ASKED), 'w') as f:      # so a later start knows whose it was, and in which profile
        json.dump({'email': email, 'profile': profile}, f)
    c = {'proc': proc, 'dir': d, 'started': time.time(), 'email': email, 'profile': profile}
    _watch(c)
    url = None
    for _ in range(80):
        if os.path.exists(url_file):
            url = read_text(url_file) or None
            if url:
                break
        if proc.poll() is not None:
            break
        time.sleep(0.25)
    if not url:
        _end(c)
        raise SwitchError('Claude Code did not hand over a sign-in link. Try again in a minute.')
    c.update(url=url, pressed=time.time())
    RUNNING[key] = c
    c['opened'] = open_sign_in(profile, url)
    return _key(key, 'opened', profile)


def stop(key):
    """Cancel a sign-in (and hide what was said about the last one with this key). One that has just finished well is
    not thrown away by a Cancel that came a moment late: for KEPT_FROM_CANCEL seconds it is left for the engine, which
    is asked now. After that a Cancel cancels it (the way out if the engine never comes), and one kept for another
    try is cancelled at once: that Cancel is the way out of the wait."""
    NOTES.pop(key, None)
    c = RUNNING.get(key)
    if c and not c.get('retry_at') and under_way(c) == 'finished':
        now = time.time()
        if now - (c.get('ended_at') or c.get('ended') or now) < KEPT_FROM_CANCEL:
            _wake()
            return None
    c = RUNNING.pop(key, None)
    if c:
        c.setdefault('result', {'ok': False, 'why': 'stopped'})
        _ended(key, c)
        _end(c)
    return c


def _ended(key, c):
    """Note how a sign-in ended (its result), under its key and start time, for look()."""
    now = time.time()
    for k, e in list(ENDED.items()):
        if not 0 <= now - e['at'] < ENDED_FRESH:
            ENDED.pop(k, None)
    ENDED[key] = {'started': c.get('started'), 'at': now, 'result': dict(c.get('result') or {})}


def look(key, started=None):
    """Where one sign-in stands, for a terminal that started it through the page (POST /api/connect-look):
    {'state': 'waiting' (open in the browser), 'keeping' (the browser approved it; the engine is keeping its login),
    'over' (with 'result', as _result says it), or None (no such sign-in: never started here, or long over)},
    with 'started', and 'default_browser' while it is under way. `started` (what /api/connect answered) tells this
    sign-in from a later one under the same key; None takes whichever is there."""
    c = RUNNING.get(key)
    if c and (started is None or c.get('started') == started):
        state = under_way(c)
        if state == 'failed':                   # its process ended badly: over now, before collect() sweeps it
            return {'state': 'over', 'started': c.get('started'),
                    'result': {'ok': False, 'why': 'the sign-in did not finish'}}
        return {'state': 'waiting' if state == 'waiting' else 'keeping', 'started': c.get('started'),
                'default_browser': c.get('opened') == 'default'}
    e = ENDED.get(key)
    if e and (started is None or e['started'] == started) and 0 <= time.time() - e['at'] < ENDED_FRESH:
        return {'state': 'over', 'started': e['started'], 'result': dict(e['result'])}
    return {'state': None}


def _result(out, c):
    """How a finished sign-in ended, written on it (c['result']) before it is marked over, for whoever waits on it
    (Sign in to all): {'ok': True, 'got': email} when the engine kept a login of the account asked for, else
    {'ok': False, 'why': short words, 'got': the account it came back as, if another}."""
    real, asked = out.get('real'), (c.get('email') or '').lower()
    if out.get('error'):
        return {'ok': False, 'why': _first(out['error'])}
    if out.get('rc') != 0 or not real:
        return {'ok': False, 'why': 'the sign-in did not finish'}
    if asked and real != asked:
        return {'ok': False, 'got': real, 'why': 'your browser signed in as %s' % real}
    return {'ok': True, 'got': real}


def _end(c):
    """Stop a sign-in that is still going, then clear what it left."""
    c['over'] = True
    proc = c.get('proc')
    if proc is not None and proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            pass
    _clean(c['dir'])


def _clean(d):
    """Delete the temporary Keychain item and folder of one sign-in. The engine removes the item (Claude Code's own name
    for it, through its Keychain wrapper), then the folder goes. When the item could not be removed now (a busy or
    locked Keychain, a dialog), the folder stays, so the sweep at the next start tries again. True when both went.
    The folder is marked first (OVER): whatever stays of it, a later start clears it and never hands it to the engine
    again."""
    try:
        if os.path.isdir(d):
            open(os.path.join(d, OVER), 'w').close()
    except OSError:
        pass
    if not engine.remove_sign_in(d):
        log("a sign-in's Keychain item could not be removed yet; the next start tries again")
        return False
    shutil.rmtree(d, ignore_errors=True)
    return True


class Left(object):
    """The process of a sign-in from before a restart: it has ended; how is not known. It counts as ended well, and
    the engine decides: it finds a login in the folder, or none."""
    pid = 0

    def poll(self):
        return 0


def _left_finished(d, now):
    """Is this folder, whose process has ended, one to hand to the engine: not over (handed over, cancelled, timed
    out), and touched within TAKEN_UP_FOR seconds?"""
    try:
        if os.path.exists(os.path.join(d, OVER)):
            return False
        newest = max([os.path.getmtime(d)] + [os.path.getmtime(os.path.join(d, n)) for n in os.listdir(d)])
    except OSError:
        return False
    return -60 <= now - newest < TAKEN_UP_FOR


def _take_up(d, now):
    """A sign-in from before a restart, as one that has finished and waits for the engine. Nothing is said until the
    engine has kept its login; with no login in it, it is cleared without a word (collect)."""
    asked = read_json(os.path.join(d, ASKED))
    email = asked.get('email') if isinstance(asked.get('email'), str) else ''
    profile = asked.get('profile') if isinstance(asked.get('profile'), str) else None
    if profile not in browser.profiles():
        profile = None
    key = email if email and email not in RUNNING else 'left:' + os.path.basename(d)
    RUNNING[key] = {'proc': Left(), 'dir': d, 'started': now, 'ended_at': now, 'email': email, 'profile': profile,
                    'left': True, 'said': True}
    log('a sign-in from before this start is handed to the engine')


def sweep(take_up=True):
    """At a start: what the sign-ins of the run before left. One whose process is still going is stopped and cleared.
    One whose process has ended and that is neither over nor old is taken up again as finished (_take_up), and the
    loop is woken to hand it to the engine. Anything else is cleared (its Keychain item and folder).
    With take_up false nothing is taken up and every sign-in is cleared: for the uninstaller's tidy, whose process
    ends at once and has no loop that would ever keep a login. A sign-in taken up there would leave its Keychain
    item behind when the folder goes with the rest.
    Returns the folders it could not clear now (the Keychain would not give the login up): they stay, marked over.
    A start tries them again at the next one; tidy names them as left, so the uninstaller stops before they go."""
    live = {c['dir'] for c in list(RUNNING.values())}
    now, taken, left = time.time(), False, []
    for d in glob.glob(os.path.join(data_path('logins'), '*')):
        # A link is not a sign-in's own folder (each is made here, as a folder): it is left alone. Nothing is written
        # through it, it is never taken up, and the engine, which refuses such a folder every time, is never asked.
        if d in live or os.path.islink(d) or not os.path.isdir(d):
            continue
        try:
            pid = int(read_text(os.path.join(d, 'pid')) or 0)
        except ValueError:
            pid = 0
        going = False
        if pid > 1:
            said = run(['/bin/ps', '-p', str(pid), '-o', 'command=']).stdout
            going = 'auth' in said and 'login' in said      # still the sign-in we started, not a reused number
            if going:
                try:
                    os.killpg(pid, signal.SIGTERM)
                except OSError:
                    pass
        if take_up and not going and _left_finished(d, now):
            _take_up(d, now)
            taken = True
        elif _clean(d):
            log('cleared a sign-in that never finished')
        else:
            left.append(d)
    if taken:
        _wake()
    return left


def _engine_part(c):
    """The engine's half of a finished sign-in: the engine keeping its login (engine.add_login reads the folder's
    Keychain item and asks Anthropic whose it is, so this never runs under the page's state lock), and the account it
    kept. {'real': email or None, 'got': the Account}. Raises what the engine refused with. The folder's settings may
    name no account yet: the engine names it from Anthropic's answer."""
    block = read_json(os.path.join(c['dir'], '.claude.json')).get('oauthAccount') or {}
    got = engine.add_login(c['dir'])            # the account Claude Code is on gets the new login too
    real = (getattr(got, 'email', None) or block.get('emailAddress') or '').strip().lower()
    return {'real': real or None, 'got': got}


def _first(text):
    """The first sentence of a refusal, without its full stop."""
    text = (text or '').strip()
    i = text.find('. ')
    return (text[:i] if i > 0 else text).rstrip('.')


def _again(e, c, now):
    """Is the engine's refusal of a finished sign-in one that passes by itself, with time left to try again?"""
    return getattr(e, 'detail', None) in RETRY_KINDS and now - c.get('ended', now) < RETRY_FOR


def _hand_over(st, c):
    """Give a finished sign-in to the engine (it reads the folder's login itself) and link its profile. Returns True
    when the account is in."""
    part = _engine_part(c)
    return bool(part['real']) and _apply(st, c, part['real'], part['got'])


def _apply(st, c, real, got):
    """The state's half: the account and its browser profile, and what is said. Returns True."""
    have = st['accounts'].get(real) or {}
    slot = have.get('slot') if have.get('connected') else None
    number = slot or max([a.get('slot') or 0 for a in st['accounts'].values()] + [0]) + 1
    a = ensure_account(st, real)
    a.update(connected=True, needs_login=False, slot=number, id=getattr(got, 'id', None) or a.get('id'))
    a.pop('signed_out', None)
    if c['profile']:                                   # signed in in a browser profile: it is this account's now
        a.update(profile=c['profile'], profile_src='checked')
        signed_in_here(st, real, c['profile'])
        for other in st['accounts'].values():          # one profile, one account
            if other is not a and other.get('profile') == c['profile'] and other.get('profile_src') != 'you':
                other.update(profile=None, profile_src=None)
    asked = (c['email'] or '').lower()
    if not asked or asked == real:
        event(st, 'Added %s' % real, 'switch')
        return True
    # The browser approved the account it was already signed in to, not the one asked for: a warning, said where the
    # waiting row was, never a plain "Added".
    if slot:
        event(st, 'Signed in as %s, not %s. It was already here, so nothing new was added.' % (real, asked), 'warn')
        again = (st['accounts'].get(asked) or {}).get('connected')        # "Sign in again" for an account here
        line = 'Your browser signed in as %s, which is already here. To %s, sign out of claude.ai there first.' % (
            real, ('sign in as %s again' if again else 'add %s') % asked)
    else:
        event(st, 'Added %s, not %s: the browser was signed in as %s.' % (real, asked, real), 'warn')
        line = 'Added %s, not %s: your browser was signed in as %s.' % (real, asked, real)
    c['note'] = {'at': time.time(), 'email': asked, 'got': real, 'line': line}
    return True


def collect():
    """The tick's first half, outside the page's state lock: each sign-in that ended is handed to the engine (it reads
    the folder's login: the Keychain and Anthropic, each with its deadline). Its folder and Keychain item then go,
    unless the engine's refusal passes by itself (no network, a slow Keychain): then they stay and the engine is asked
    again every RETRY_EVERY seconds, for RETRY_FOR seconds. One still waiting TIMES_OUT seconds after its last press
    is stopped. Returns what finish(st, done) writes into the state."""
    done = []
    now = time.time()
    for key, c in list(RUNNING.items()):
        rc = c['proc'].poll()
        if rc is None:
            if now - max(c['started'], c.get('pressed', 0)) > TIMES_OUT:
                c['result'] = {'ok': False, 'why': 'it was not approved in time'}   # (kept only once it is over)
                if stop(key) is not None:
                    done.append({'key': key, 'c': c, 'timed_out': True})    # (None: it finished just now: kept)
            continue
        c.setdefault('ended', now)
        if now < c.get('retry_at', 0):
            continue                                # kept for another try, not due yet
        out = {'key': key, 'c': c, 'rc': rc}
        again = False
        try:
            if rc == 0:
                out.update(_engine_part(c))
        except SwitchError as e:
            out['error'] = str(e)
            again = rc == 0 and _again(e, c, now)
        except Exception as e:  # noqa: BLE001 - the temporary login must be cleared no matter what
            log('finishing a sign-in failed: %r' % e)
        finally:
            if again:                               # its login exists only in that item: kept for the next try
                c['retry_at'] = now + RETRY_EVERY
                c['waiting'] = 'Keeping the new sign-in%s waits: %s. Switchboard tries again by itself.' % (
                    ' as %s' % c['email'] if c.get('email') else '', _first(out['error']))
            elif RUNNING.get(key) is c:             # (not one a Cancel took meanwhile, nor a newer one in its place)
                RUNNING.pop(key, None)
                c['result'] = _result(out, c)
                _ended(key, c)
                c['over'] = True
                _clean(c['dir'])
        if again:
            if not c.get('said'):
                c['said'] = True
                done.append({'key': key, 'c': c, 'waiting': c['waiting']})
            continue
        if c.get('left') and not out.get('real'):   # from before a restart, and no login in it: nothing to say
            log('cleared a sign-in from before this start: it held no login')
            continue
        done.append(out)
    return done


def finish(st, done=None):
    """Called on every tick, inside the state lock: what collect() found goes into the state (`done`; None: collect
    first, for a caller that has not)."""
    for d in collect() if done is None else done:
        if d.get('timed_out'):
            event(st, 'The sign-in timed out. Press Add account to try again.', 'warn')
            continue
        if d.get('waiting'):                        # kept for another try: said once, nothing to do
            event(st, d['waiting'], 'warn')
            continue
        if d.get('error'):
            event(st, d['error'], 'warn')
        ok = False
        try:
            ok = d.get('rc') == 0 and bool(d.get('real')) and _apply(st, d['c'], d['real'], d.get('got'))
        except Exception as e:  # noqa: BLE001 - said as not finished, the details in the log
            log('finishing a sign-in failed: %r' % e)
        if not ok:
            event(st, 'The sign-in did not finish. Press Add account to try again.', 'warn')
        elif d['c'].get('note'):
            NOTES[d['key']] = d['c']['note']


def view(st):
    """What the page draws under the accounts: each sign-in still waiting (and whether it opened in the default
    browser, and whether its process has ended well: then its login is being kept), then each one that came back as
    another account (done, with its line), until hidden or ten minutes."""
    names = browser.profiles()
    now = time.time()
    for k, n in list(NOTES.items()):
        if not 0 <= now - n['at'] < NOTE_FRESH:
            NOTES.pop(k, None)
    out = [{'key': k, 'email': c['email'], 'name': name_of(st, c['email']) if c['email'] else '',
            'profile_name': names.get(c['profile'], c['profile']), 'started': c['started'],
            'default_browser': c.get('opened') == 'default', 'keeping': bool(c.get('retry_at')),
            'ended': under_way(c) == 'finished',        # the browser approved it: its login is being kept
            'line': c.get('waiting') if c.get('retry_at') else None}
           for k, c in list(RUNNING.items())]
    return out + [{'key': k, 'email': n['email'], 'name': n['email'], 'profile_name': '', 'started': n['at'],
                   'done': True, 'got': n['got'], 'line': n['line']}
                  for k, n in sorted(NOTES.items(), key=lambda x: x[1]['at']) if k not in RUNNING]
