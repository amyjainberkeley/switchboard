"""The page key: the page answers only its own person.

The page's server already refuses other websites (Host, Origin, the browser's Sec-Fetch-Site, and the X-Switchboard
header on a POST). This keeps out other programs on this Mac as well: a program run by another user of the Mac, or
an app that may use the network but may not read your files. Every POST, and every GET under /api/ except
/api/state, must carry the page key. A route added later is locked without anyone having to remember it. The two
POSTs a clicked link needs (GO_POSTS) are answered without it, and say less to a caller that has no key.

- Switchboard's own programs send, in the header X-Switchboard-Key, a proof made from the key for that one request
  (headers(), the one helper every caller uses; proof()), never the key itself.
- The page's own script sends the key in the same header. It keeps the key in the browser's storage for the page's own address,
  http://127.0.0.1:<port>, and nowhere else. Never a cookie: a browser keeps cookies by host, not by port, and sends
  them by itself, so a server on another port could be handed one. Storage is kept by the whole address, port included.
- The page gets the key from a ticket: short-lived, good once, signed with the page key (ticket()). To open the page, a
  small file only you can read is written in Switchboard's folder; it sends the browser on to
  http://127.0.0.1:<port>/?t=<ticket>. `open` (or the menu bar) is handed the file, never the ticket, so neither the
  key nor a ticket is ever in a program's arguments (`ps`). The page's script trades the ticket once at POST
  /api/ticket (redeem()) for the key and takes it out of the address. Only a spent ticket can be in the browser's
  History. The file goes when its ticket is taken, or once it has expired.
- The page itself (/, /index.html) holds no data and is answered to anyone, as before; without the key it shows only
  a line that says how to open it.

The page key is made from the install's own secret (core.key()) with HMAC-SHA256 under a fixed label. That secret is
carried by the "Open as its owner" bookmark (/go?k=), so it is never the page key itself, and the page key opens
nothing at /go.

The demo (core.DEMO) has nothing to keep: its accounts are made up, its secret is a fixed word, and it writes nothing
to disk, so it could hold no ticket file. The key does not apply there, and `switchboard demo` opens it as before.
"""
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import threading
import time

from . import core

LABEL = b'switchboard page key'             # the key is HMAC-SHA256(core.key(), LABEL), never core.key() itself
HEADER = 'X-Switchboard-Key'                # the key (the page's script) or a proof (Switchboard's programs)
OPEN = '/api/state'                         # the one route under /api/ any program may read (see SECURITY.md)
TICKET = '/api/ticket'                      # the open POST the page's script trades a ticket at, for the key
BOOKMARK = '/api/bookmark'                  # GET, with the key: the install's secret, for the page's bookmark alone
# The two POSTs a clicked link needs, answered without the key (still with the page's own header and every other
# check), each answer cut to the fields named here: the link app (a built program that is not rebuilt when the
# Python is updated) and web/go.html, which runs in whatever browser profile a link or the bookmark lands in, where
# there is no key. /api/go opens a claude.ai artifact link in its owner's profile; /api/setup-profile makes a new
# browser profile for an account and opens claude.ai's sign-in there (go.html's Set up). Nothing else: no reading,
# no switch, no removal, no other setting.
GO_POSTS = {'/api/go': ('checked', 'signin', 'trouble'), '/api/setup-profile': ('opened',)}
NO_PROFILE_CUT = ("No browser profile is signed in as this page's owner yet. Set one up on Switchboard's page (Set up), "
                  'then open it again.')
TICKET_FOR = 60                             # seconds a ticket in a file is good for
LINK_FOR = 300                              # seconds a printed link is good for: a person copies it by hand
SPENT_MOST = 4096                           # spent tickets remembered at most; when full of unexpired ones, refuse
PROOF_FOR = 60                              # seconds a proof from Switchboard's own programs is good for, either way
FOLDER = 'page-open'                        # the folder in Switchboard's own folder that holds the files
WAIT = 15.0                                 # seconds `switchboard page` waits for the browser to take its ticket
SWEEP_EVERY = 30.0                          # seconds (monotonic) between two looks for expired files by the page
SAY_EVERY = 60.0                            # seconds (monotonic) between two log lines for refusals of one route

REFUSED = ('The page needs to be opened again: type `switchboard page` in a terminal, or use Open Switchboard in your '
           'menu bar.')
OPEN_FAILED = 'Open %s in your browser. It works once, for five minutes.'
NOT_TAKEN = 'If the page did not open, open %s in your browser. It works once, for five minutes.'
DEMO_OPEN = 'Open %s in your browser.'

TICKET_RE = re.compile(r'^([0-9]{1,12})\.([A-Za-z0-9_-]{16})\.([0-9a-f]{64})$')
FILE_RE = re.compile(r'^([0-9]{1,12})-([A-Za-z0-9_-]{16})\.html$')
PART_RE = re.compile(r'^#[A-Za-z0-9/_-]{0,64}$')     # a place on the page to open at, such as #accounts/add

_SPENT = {}                                 # nonce -> expiry, for tickets already taken
_USED = {}                                  # nonce -> expiry, for proofs already taken
_SPENT_LOCK = threading.Lock()
_SWEPT = {'at': None}
_SAID = {}                                  # route -> when its refusal was last logged (monotonic)
_SAID_LOCK = threading.Lock()


# ---------- the key, and who must carry it ----------

def key():
    """The page key: HMAC-SHA256 of LABEL under this install's own secret, as 64 hex characters."""
    return hmac.new(core.key().encode('utf-8'), LABEL, hashlib.sha256).hexdigest()


def needs_key(method, path):
    """True for every POST and every GET under /api/ except /api/state (default closed)."""
    route = path.split('?', 1)[0].split('#', 1)[0]
    if method != 'GET':
        return True
    return route.startswith('/api/') and route != OPEN


def headers(path, method='POST', body=b''):
    """The headers Switchboard's own programs send to the page for `method` on `path` with `body` (bytes): for a
    POST, JSON and the page's own header (which no website can send without asking); wherever the page asks for the
    key (never on GET /api/state), a proof made from it for this one request (proof()), never the key itself: a
    program that holds the page's port while Switchboard is not running gets this one request, within a minute."""
    h = {'Content-Type': 'application/json', 'X-Switchboard': '1'} if method == 'POST' else {}
    if needs_key(method, path):
        h[HEADER] = proof(method, path, body)
    return h


def request(base, path, body):
    """The POST of `body` (as JSON) to the page at base + path, with headers(): how Switchboard's own programs ask."""
    import urllib.request
    data = json.dumps(body).encode('utf-8')
    return urllib.request.Request(base + path, data=data, headers=headers(path, 'POST', data))


def _proof_mac(method, path, at, nonce, body):
    digest = hashlib.sha256(body if isinstance(body, bytes) else str(body).encode('utf-8')).hexdigest()
    msg = '\0'.join(('switchboard proof', method, path, str(at), nonce, digest)).encode('utf-8', 'replace')
    return hmac.new(key().encode('utf-8'), msg, hashlib.sha256).hexdigest()


def proof(method, path, body=b'', now=None):
    """'<unix time>.<random>.<HMAC under the page key over the method, the path, the time, the random and the
    SHA-256 of the body>' (an empty body: the SHA-256 of nothing)."""
    at = int(time.time() if now is None else now)
    nonce = secrets.token_urlsafe(12)
    return '%d.%s.%s' % (at, nonce, _proof_mac(method, path, at, nonce, body))


def proved(given, method, path, body=b'', now=None):
    """True once for a proof made for this method, path (the whole path, its query too) and body, within PROOF_FOR
    seconds either way of now; a proof is never taken twice (remembered as tickets are)."""
    now = time.time() if now is None else now
    m = TICKET_RE.match(given) if isinstance(given, str) else None
    if not m or method is None or path is None:
        return False
    at, nonce, mac = int(m.group(1)), m.group(2), m.group(3)
    if not abs(now - at) <= PROOF_FOR:
        return False
    if not hmac.compare_digest(mac.encode('ascii'), _proof_mac(method, path, at, nonce, body).encode('ascii')):
        return False
    with _SPENT_LOCK:
        for n in [n for n, e in _USED.items() if e <= now]:
            del _USED[n]
        if nonce in _USED or len(_USED) >= SPENT_MOST:
            return False
        _USED[nonce] = at + PROOF_FOR + 1
    return True


def _same(given, want):
    return isinstance(given, str) and hmac.compare_digest(given.encode('utf-8', 'replace'), want.encode('utf-8'))


def carried(request_headers, method=None, path=None, body=b''):
    """'header' when X-Switchboard-Key holds the page key (the page's script sends it), 'proof' when it holds a good
    proof for this method, path and body (Switchboard's own programs send one), else None. A cookie never counts."""
    given = request_headers.get(HEADER)
    if _same(given, key()):
        return 'header'
    return 'proof' if proved(given, method, path, body) else None


def _body(handler, method):
    """The request's body, for a POST that carries a proof (b'' otherwise): read once, and put back for the server's
    own reading of it, as it was."""
    if method != 'POST' or not TICKET_RE.match(handler.headers.get(HEADER) or ''):
        return b''
    try:
        size = int(handler.headers.get('Content-Length') or 0)
    except ValueError:
        return b''
    if not 0 < size <= getattr(handler, 'MAX_BODY', 4 << 20):
        return b''
    data = handler.rfile.read(size)
    handler.rfile = io.BytesIO(data)
    return data


# ---------- tickets: how the page's script gets the key ----------

def _mac(exp, nonce):
    return hmac.new(key().encode('utf-8'), b'switchboard ticket\0%d\0%s' % (exp, nonce.encode('ascii')),
                    hashlib.sha256).hexdigest()


def _make(now=None, ttl=TICKET_FOR):
    now = time.time() if now is None else now
    exp = int(now) + int(ttl)
    nonce = secrets.token_urlsafe(12)
    return '%d.%s.%s' % (exp, nonce, _mac(exp, nonce)), exp, nonce


def ticket(now=None, ttl=TICKET_FOR):
    """A one-use ticket: '<expiry>.<random>.<HMAC under the page key>', good for `ttl` seconds."""
    return _make(now, ttl)[0]


def redeem(t, now=None):
    """True once for a good ticket: signed with this page key, not expired, never taken before. Spent tickets are
    remembered until they expire (in memory, at most SPENT_MOST: when that many unexpired ones are held, a new one is
    refused). A good ticket's file is removed."""
    now = time.time() if now is None else now
    m = TICKET_RE.match(t) if isinstance(t, str) else None
    if not m:
        return False
    exp, nonce, mac = int(m.group(1)), m.group(2), m.group(3)
    if not hmac.compare_digest(mac.encode('ascii'), _mac(exp, nonce).encode('ascii')):
        return False
    if not now < exp <= now + LINK_FOR + 5:
        return False
    with _SPENT_LOCK:
        for n in [n for n, e in _SPENT.items() if e <= now]:
            del _SPENT[n]
        if nonce in _SPENT or len(_SPENT) >= SPENT_MOST:
            return False
        _SPENT[nonce] = exp
    _remove(os.path.join(core.data_path(FOLDER), '%d-%s.html' % (exp, nonce)))
    return True


def page_url(path='/', port=None):
    """An address on the page that a browser opens: always the literal 127.0.0.1, the one address the page's server
    listens on. Never the name localhost, which may also lead to ::1, where another program could listen and so be
    handed the page's key (a browser keeps a page's storage by its whole address)."""
    return 'http://127.0.0.1:%d%s' % (core.port() if port is None else port, path)


def link(t, port=None):
    """The address that takes a ticket."""
    return page_url('/?t=%s' % t, port)


def _remove(path):
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        pass


def sweep(now=None):
    """Remove the files whose tickets have expired (their names carry the expiry). Returns how many went."""
    now = time.time() if now is None else now
    folder = core.data_path(FOLDER)
    try:
        names = os.listdir(folder)
    except OSError:
        return 0
    gone = 0
    for name in names:
        m = FILE_RE.match(name)
        if m and int(m.group(1)) <= now:
            _remove(os.path.join(folder, name))
            gone += 1
    return gone


def _part(part):
    return part if part and PART_RE.match(part) else ''


def opener(port=None, now=None, part=''):
    """Write the file that sends a browser to the page with a fresh ticket: in Switchboard's own folder, in a folder
    only you can open, readable only by you. `part`: a place on the page (#accounts/add), kept by the browser when
    the page sends it on to /. Returns its path."""
    port = core.port() if port is None else port
    now = time.time() if now is None else now
    sweep(now)
    t, exp, nonce = _make(now, TICKET_FOR)
    folder = core.data_path(FOLDER)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    to = link(t, port) + _part(part)
    page = ('<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer">'
            '<meta http-equiv="refresh" content="0;url=%s"><title>Switchboard</title>'
            '<script>location.replace(%s)</script><p><a href="%s">Open Switchboard</a></p>\n'
            % (to, '"%s"' % to, to))
    path = os.path.join(folder, '%d-%s.html' % (exp, nonce))
    core.write_bytes(path, page.encode('utf-8'), 0o600)
    return path


# ---------- a new key ----------

NEW_KEY = ('Made a new key for the page. Open it again with Open Switchboard in your menu bar, or type '
           '`switchboard page`. Add the Open as its owner bookmark again from the page.')


def new_key_command():
    """`switchboard page --new-key`: a new secret for this install, so a new page key. The key every browser kept and the
    "Open as its owner" bookmark stop working (the bookmark carries the secret itself); the page asks for the new key at once."""
    core.update_setting('key', lambda old: secrets.token_urlsafe(16))
    print(NEW_KEY)
    return 0


# ---------- opening the page ----------

def launcher(wait=True, quiet=False):
    """launch(target) -> True when macOS's `open` took it. wait: wait for `open` and read how it went (the demo only
    starts it, and serves on); quiet: its output to /dev/null, given up after 30 s (the menu bar)."""
    import subprocess

    def launch(target):
        try:
            if not wait:
                if quiet:
                    subprocess.Popen(['/usr/bin/open', target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    subprocess.Popen(['/usr/bin/open', target])
                return True
            if quiet:
                return subprocess.call(['/usr/bin/open', target], stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, timeout=30) == 0
            return subprocess.call(['/usr/bin/open', target]) == 0
        except (OSError, subprocess.SubprocessError):
            return False
    return launch


def print_line(sentence, url):
    """The terminal's way to say a fresh link: the sentence alone (it holds the link)."""
    print(sentence)


def open_page(launch, say=None, wait=True, port=None, patience=None, background=False, part=''):
    """Open the page through a ticket in a file: launch(path) hands the file to the browser. When launch fails (a
    sandboxed terminal), say(sentence, link) gets a fresh link good once for five minutes. When `wait`, and the
    browser has not taken the ticket within `patience` seconds (the file opened in something that is not a browser,
    say), say() gets one too. `background`: that wait runs on a thread of its own (the menu bar's main thread never
    waits). `part`: a place on the page to open at. The demo opens its plain address. True when launch took it."""
    port = core.port() if port is None else port
    part = _part(part)
    if core.DEMO:
        url = page_url('', port) + ('/' + part if part else '')
        if launch(url):
            return True
        if say:
            say(DEMO_OPEN % url, url)
        return False
    try:
        path = opener(port, part=part)
    except OSError:
        path = None
    if path and launch(path):
        if wait and say:
            if background:
                threading.Thread(target=watch_taken, args=(path, say, port, patience, part), daemon=True).start()
            else:
                watch_taken(path, say, port, patience, part)
        return True
    _remove(path)
    if say:
        to = link(ticket(ttl=LINK_FOR), port) + part
        say(OPEN_FAILED % to, to)
    return False


def open_in_workspace(workspace, nsurl, call_after, part=''):
    """The menu bar's way to open the page (open_page): NSWorkspace is handed the file, and when no browser took its
    ticket after a short wait, a fresh link opens in its place (on AppKit's main thread, through call_after)."""
    ws = workspace.sharedWorkspace()
    return open_page(lambda path: bool(ws.openURL_(nsurl.fileURLWithPath_(path))),
                     lambda said, url: call_after(lambda: ws.openURL_(nsurl.URLWithString_(url))),
                     background=True, part=part)


def watch_taken(path, say, port=None, patience=None, part='', sleep=time.sleep, clock=time.monotonic):
    """Wait until the file is gone (the page took its ticket). Not gone after `patience` seconds: say() gets a fresh
    link (the file stays until its ticket expires, for a browser that is only slow). True when it was taken."""
    patience = WAIT if patience is None else patience
    end = clock() + patience
    while os.path.exists(path):
        if clock() >= end:
            to = link(ticket(ttl=LINK_FOR), port) + _part(part)
            say(NOT_TAKEN % to, to)
            return False
        sleep(0.2)
    return True


# ---------- the page's server: the one check it calls ----------

def _say_refused(method, route, clock=time.monotonic):
    """One log line for a refused call, at most once a minute for one route (an old page tab asks every few
    seconds). The route only: never a query or a header."""
    now = clock()
    with _SAID_LOCK:
        last = _SAID.get((method, route))
        if last is not None and 0 <= now - last < SAY_EVERY:
            return
        if len(_SAID) > 256:
            _SAID.clear()
        _SAID[(method, route)] = now
    core.log('refused %s %s: it did not carry the page key' % (method, route[:80]))


def _sweep_soon(clock=time.monotonic):
    now = clock()
    if _SWEPT['at'] is not None and 0 <= now - _SWEPT['at'] < SWEEP_EVERY:
        return
    _SWEPT['at'] = now
    try:
        sweep()
    except Exception:  # noqa: BLE001 - a look for old files never stops an answer
        pass


def gate(handler, method):
    """The page key's check, called once per request by the server's handler after its own checks. True when it
    answered the request itself: 401 with one plain sentence for a call that does not carry the key, and POST
    /api/ticket (the key once for a good ticket). False when the request goes on as before: the demo, the page itself,
    a route that needs no key (GET /api/state, with the install's secret taken out for a caller without the key), the
    old Link Router's /api/go, a request another website started (the server refuses it in its own words), one that
    carries the key, or a link route (GO_POSTS), whose answer is then cut."""
    route = handler.path.partition('?')[0]
    if method == 'POST' and handler.path == TICKET:
        return _trade(handler)
    if core.DEMO:
        return False
    _sweep_soon()
    if method == 'GET' and route == OPEN:
        if not carried(handler.headers, method, handler.path):
            handler._send = _without_secret(handler._send)      # the bookmark's secret is for the page alone
        return False
    if not needs_key(method, route):
        return False
    if method == 'POST' and handler._old_router():
        return False
    if method == 'GET' and handler.headers.get('Sec-Fetch-Site') not in (None, 'same-origin', 'none'):
        return False
    if carried(handler.headers, method, handler.path, _body(handler, method)):     # once: a proof is good once
        if method == 'GET' and route == BOOKMARK:
            handler._send(200, {'key': core.key()})
            return True
        return False
    if method == 'POST' and handler.path in GO_POSTS:       # the whole path, exactly
        handler._send = _cut(handler._send, GO_POSTS[handler.path])     # answered, with less said
        return False
    _say_refused(method, route)
    handler._send(401, {'error': REFUSED})
    return True


def _trade(handler):
    """POST /api/ticket {t}: the page key, once, for a good ticket; 401 and the one sentence for anything else. The
    server has already checked the host, the origin and the page's own header. The demo has no key: {}."""
    if core.DEMO:
        handler._send(200, {})
        return True
    try:
        size = int(handler.headers.get('Content-Length') or 0)
    except ValueError:
        size = -1
    t = None
    if 0 < size <= 4096:
        try:
            body = json.loads(handler.rfile.read(size).decode('utf-8'))
            t = body.get('t') if isinstance(body, dict) else None
        except (ValueError, UnicodeDecodeError):
            t = None
    if redeem(t):
        handler._send(200, {'key': key()})
    else:
        core.log('refused a link to the page: its ticket was spent, expired or changed')
        handler._send(401, {'error': REFUSED})
    return True


def _for_anyone(state):
    """The state as a caller without the key gets it: no install secret (links.key: the bookmark carries it, and the
    page key is made from it), no first name of this Mac's account, no title of a chat in another provider's row."""
    if not isinstance(state, dict):
        return state
    links = state.get('links')
    if isinstance(links, dict) and 'key' in links:
        links['key'] = ''
    if 'first_name' in state:
        state['first_name'] = None
    for row in state.get('provider_rows') or ():
        chat = row.get('chat') if isinstance(row, dict) else None
        if isinstance(chat, dict) and 'name' in chat:
            chat['name'] = ''
    return state


def _without_secret(send):
    """The handler's answer to GET /api/state for a caller without the key: read as JSON and cut (_for_anyone), and
    the install's own secret taken out wherever else it might be, as an empty string."""
    secret = json.dumps(core.key()).encode('utf-8')

    def answer(code, body, *args, **kwargs):
        from . import server
        if code == 200:
            try:
                body = server.to_json(_for_anyone(json.loads(body if isinstance(body, bytes) else server.to_json(body))))
            except ValueError:
                body = b'{}'
        elif not isinstance(body, bytes):
            body = server.to_json(body)
        return send(code, body.replace(secret, b'""'), *args, **kwargs)
    return answer


def _cut(send, keep):
    """The handler's answer for a link route asked without the key: a 200 keeps only `keep` (and ok), so no address,
    profile name or page title reaches such a caller; a refusal keeps its fix, and its words with no address in them."""
    def answer(code, body, *args, **kwargs):
        if isinstance(body, dict) and code == 200:
            body = dict((k, v) for k, v in body.items() if k in keep or k == 'ok')
        elif isinstance(body, dict) and isinstance(body.get('error'), str):
            said = NO_PROFILE_CUT if body.get('fix') == 'no_profile' else core.redact(body['error'], emails=True)
            body = dict(body, error=said)
        return send(code, body, *args, **kwargs)
    return answer
