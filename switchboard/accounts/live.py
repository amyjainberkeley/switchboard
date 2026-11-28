"""Claude Code's live login: read (Keychain first, file as degraded), parse, write with the identity block.

Reading, in Claude Code's own order: the Keychain item for this environment (facts.keychain_service), then the
plaintext .credentials.json. The file only counts as "degraded" when the Keychain could not be read: it may hold a
generation Claude Code has since replaced, so it may be shown but is never spent or kept. When the Keychain says the
item is not there (an authoritative answer), the file is what Claude Code itself uses, so it is the live login.

What a login can be (Login.kind): 'oauth' (an account login with at least one token), 'wiped' (the login object with
both tokens emptied: what Claude Code leaves after the server refused its refresh), 'no_login' (an object with no
login in it, e.g. only MCP logins), 'sealed' (no login in the item, and Claude Code's overflow pointer says the
login sits encrypted in one of its overflow items: a login Switchboard cannot read), 'api_key' (a bare console key),
'empty', 'unparseable'. Only an 'oauth' login with both tokens may ever be kept in a slot.

Writing: the target's saved login, with the live item's machine-wide siblings (facts.SHARED_SIBLINGS) kept from the
live item as they are (present or absent): everything else travels with the account. Then the plaintext file gets
the same bytes when it exists, so a running Claude Code notices at once. Both are made under Claude Code's save lock
on its item (ccfiles.storage_lock), so they never land beside one of its own saves.
"""
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Optional, Tuple

from . import ccfiles, facts, keychain
from .api import ABSENT, PRESENT, UNREADABLE, Read


def fingerprint(token):
    """SHA-256 of a token, first 16 hex (never the token itself)."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()[:16] if isinstance(token, str) and token else None


def _ms(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if not math.isfinite(v) or v <= 0:
        return None
    return int(v)


@dataclass(frozen=True)
class Login:
    """One login as bytes and as facts about it. Times are epoch milliseconds (exact, for comparing)."""
    text: str
    kind: str
    blob: Optional[dict] = field(default=None, compare=False, hash=False)
    fingerprint: Optional[str] = None           # of the refresh token
    access_fp: Optional[str] = None
    expires_ms: Optional[int] = None
    refresh_expires_ms: Optional[int] = None
    scopes: Tuple[str, ...] = ()
    plan: Optional[str] = None
    has_refresh: bool = False
    has_access: bool = False

    @property
    def full_pair(self):
        return self.kind == 'oauth' and self.has_refresh and self.has_access

    @property
    def text_fp(self):
        """A fingerprint of the whole login text (for safety copies of logins without a refresh token)."""
        return hashlib.sha256(self.text.encode('utf-8')).hexdigest()[:16]

    def expired(self, now_s):
        """Claude Code's rule: expired from 5 minutes before expiresAt. Not known (no expiresAt) is not expired."""
        return self.expires_ms is not None and (now_s + facts.EXPIRY_BUFFER_S) * 1000 >= self.expires_ms

    @property
    def access_token(self):
        o = (self.blob or {}).get(facts.OAUTH_KEY)
        t = o.get('accessToken') if isinstance(o, dict) else None
        return t if isinstance(t, str) and t else None

    @property
    def refresh_token(self):
        o = (self.blob or {}).get(facts.OAUTH_KEY)
        t = o.get('refreshToken') if isinstance(o, dict) else None
        return t if isinstance(t, str) and t else None


def parse(text):
    """A Login from the exact text a store holds."""
    if text is None or not text.strip():
        return Login(text=text or '', kind='empty')
    stripped = text.strip()
    if not stripped.startswith('{'):
        if stripped.startswith('sk-ant-') and not stripped.startswith(('sk-ant-oat', 'sk-ant-ort')):
            return Login(text=text, kind='api_key')
        return Login(text=text, kind='unparseable')
    try:
        blob = ccfiles.loads_strict(text)
    except ValueError:
        return Login(text=text, kind='unparseable')
    if not isinstance(blob, dict):
        return Login(text=text, kind='unparseable')
    oauth = blob.get(facts.OAUTH_KEY)
    if not isinstance(oauth, dict):
        pointer = blob.get(facts.OVERFLOW_KEY)
        if isinstance(pointer, dict) and 'login' in pointer:
            # Claude Code 2.1.331 moved the login itself into an overflow item (it does so only for a sign-in that
            # also holds organization entries): signed in, in a form Switchboard does not read
            return Login(text=text, kind='sealed', blob=blob)
        return Login(text=text, kind='no_login', blob=blob)
    access, refresh = oauth.get('accessToken'), oauth.get('refreshToken')
    has_access = isinstance(access, str) and bool(access)
    has_refresh = isinstance(refresh, str) and bool(refresh)
    scopes = oauth.get('scopes')
    scopes = tuple(s for s in scopes if isinstance(s, str)) if isinstance(scopes, list) else ()
    plan = oauth.get('subscriptionType')
    return Login(text=text, kind='oauth' if (has_access or has_refresh) else 'wiped', blob=blob,
                 fingerprint=fingerprint(refresh) if has_refresh else None,
                 access_fp=fingerprint(access) if has_access else None,
                 expires_ms=_ms(oauth.get('expiresAt')), refresh_expires_ms=_ms(oauth.get('refreshTokenExpiresAt')),
                 scopes=scopes, plan=plan if isinstance(plan, str) and plan else None,
                 has_refresh=has_refresh, has_access=has_access)


# Anthropic's secrets start sk-ant-: sk-ant-ort... is a refresh token; any other (sk-ant-oat... an access token or a
# one-year token, sk-ant-api... an API key) can be used as it is but never renewed.
_HELD_VALUE = re.compile(r'sk-ant-(?!ort)[a-z0-9]', re.IGNORECASE)
_REFRESH_VALUE = re.compile(r'sk-ant-ort[a-z0-9]', re.IGNORECASE)
_HELD_FIELDS = ('accesstoken', 'apikey', 'primaryapikey')     # top-level names, lower case, without _ or -
_REFRESH_FIELDS = ('refreshtoken',)
_ESCAPE = re.compile(r'\\u([0-9a-fA-F]{4})')


def unrenewable(login):
    """A login that can be used but never renewed: its text holds an access token or an API key and no refresh token
    (a one-year token, an API key, a sign-in stripped of its refresh token, an item written by hand). Decided by the
    text's content, whatever shape parse() gave it. Switchboard never copies such a login aside, never sends it to
    Anthropic to ask whose it is, and never reads usage with it. A sign-in (a refresh token is there) and a text with
    no token value in it (an emptied login, MCP logins only, anything else) are not this."""
    if login is None or login.kind == 'empty':
        return False
    if login.kind == 'oauth':                       # Claude Code's own login object says it
        return login.has_access and not login.has_refresh
    held = renews = False
    # The text as it is, and with its \u escapes read as their letters. Both, always: an object may name one key
    # twice, and the value a reader drops (every one but the last) is still in the text a copy would hold.
    raw = login.text or ''
    texts, todo = [raw, _ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), raw)], [login.blob]
    while todo:                                     # every string in the object (a \u escape is read as its letter)
        x = todo.pop()
        if isinstance(x, dict):
            todo.extend(x.keys())
            todo.extend(x.values())
        elif isinstance(x, list):
            todo.extend(x)
        elif isinstance(x, str):
            texts.append(x)
    for t in texts:
        held = held or bool(_HELD_VALUE.search(t))
        renews = renews or bool(_REFRESH_VALUE.search(t))
    if isinstance(login.blob, dict):
        for k, v in login.blob.items():
            name = re.sub(r'[_-]', '', k.lower()) if isinstance(k, str) else ''
            if isinstance(v, str) and v.strip():
                held = held or name in _HELD_FIELDS
                renews = renews or name in _REFRESH_FIELDS
    return held and not renews


def dump(blob):
    """A login object as the compact ASCII JSON we store (non-ASCII as \\u escapes: security prints only text)."""
    return json.dumps(blob, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def compose(target_blob, live_blob):
    """What a switch writes: the target's login and everything that travels with it, plus the live item's machine-wide
    siblings as they are now (a sibling the live item no longer has is not brought back from the saved copy). Claude
    Code's overflow pointer is never taken from the saved copy, also when there is no live item at all."""
    if not isinstance(live_blob, dict):
        return dict((k, v) for k, v in target_blob.items() if k != facts.OVERFLOW_KEY)
    out = dict((k, v) for k, v in target_blob.items() if k not in facts.SHARED_SIBLINGS)
    for k in facts.SHARED_SIBLINGS:
        if k in live_blob:
            out[k] = live_blob[k]
    return out


def account_bound(blob):
    """A saved login without the machine-wide siblings (for a run-as: its own Claude Code must not renew the Mac's
    MCP logins in a second copy)."""
    return dict((k, v) for k, v in blob.items() if k not in facts.SHARED_SIBLINGS)


def compare(a, b):
    """Which of two logins is newer: 'same' (one generation), 'newer' (a is), 'older', or None when it can't be told.
    Refresh-expiry stamps further apart than facts.SAME_SIGN_IN_MS are two sign-ins, and the later stamp is the later
    sign-in. Closer stamps (or one not known) are one sign-in, renewed: the later access expiry is the newer
    generation. A renewal can lower the stamp a little, so a lower stamp alone never makes a login older."""
    if a is None or b is None:
        return None
    if a.fingerprint and a.fingerprint == b.fingerprint:
        if a.expires_ms is not None and b.expires_ms is not None and a.expires_ms != b.expires_ms:
            return 'newer' if a.expires_ms > b.expires_ms else 'older'
        return 'same'
    ra, rb = a.refresh_expires_ms, b.refresh_expires_ms
    if ra is not None and rb is not None and not facts.same_sign_in(ra, rb):
        return 'newer' if ra > rb else 'older'
    if a.expires_ms is not None and b.expires_ms is not None and a.expires_ms != b.expires_ms:
        return 'newer' if a.expires_ms > b.expires_ms else 'older'
    return None


def older_sign_in(a, rx_ms):
    """Is login a an earlier sign-in than one whose refresh expiry is rx_ms? Each sign-in has its own end date
    (refreshTokenExpiresAt). A renewal can lower it a little (facts.SAME_SIGN_IN_MS), so stamps that close are one
    sign-in, never an older and a newer one, whichever is lower: a held copy of that sign-in can be an older generation
    than Claude Code's. Only a stamp earlier by more than that is an earlier sign-in. False when either is not known."""
    ra = getattr(a, 'refresh_expires_ms', None)
    return isinstance(ra, int) and isinstance(rx_ms, int) and not isinstance(rx_ms, bool) and ra < rx_ms and \
        not facts.same_sign_in(ra, rx_ms)


@dataclass(frozen=True)
class LiveRead:
    """read: the typed result (provenance 'keychain', 'file' or 'cache'; degraded when the Keychain failed and a copy
    stood in). login: the parsed login when present. service: the Keychain item asked."""
    read: Read
    login: Optional[Login] = None
    service: str = ''

    @property
    def usable(self):
        """Present, not degraded: bytes we may compare, keep or write over."""
        return self.read.kind == PRESENT and not self.read.degraded


def read_live(ctx, fresh=True, interactive=False, env=None):
    """Claude Code's live login as a LiveRead. fresh: never from a cached value."""
    env = ctx.env if env is None else env
    service = facts.keychain_service(env)
    account = facts.keychain_account(env)
    kc = ctx.keychain.read(service, account, fresh=fresh, interactive=interactive)
    if kc.kind == UNREADABLE and fresh and kc.why not in ('timeout', 'busy', 'no_tool', 'test_run', 'no_keychain',
                                                          'dialog'):
        ctx.sleep(0.3)                                      # one more try for a passing failure
        kc = ctx.keychain.read(service, account, fresh=True, interactive=True)
    if kc.kind == PRESENT and (kc.value or '').strip():
        return LiveRead(kc, parse(kc.value), service)
    keychain_failed = kc.kind == UNREADABLE
    path = facts.credentials_file(env, ctx.home)
    fr, raw = ccfiles.read_bytes(path)
    if fr.kind == PRESENT:
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            text = None
        if text is not None and text.strip():
            return LiveRead(Read(PRESENT, value=text, provenance='file', degraded=keychain_failed), parse(text),
                            service)
    elif fr.kind == UNREADABLE:
        return LiveRead(Read(UNREADABLE, why=kc.why if keychain_failed else fr.why), None, service)
    if keychain_failed:
        return LiveRead(Read(UNREADABLE, why=kc.why), None, service)
    if not facts.default_profile(env, ctx.home):
        # a custom folder that is ~/.claude itself: a login made without the variable sits in the plain item
        import os
        folder = facts.secure_dir(env, ctx.home)
        default_folder = os.path.join(ctx.home, '.claude')
        try:
            same = os.path.exists(folder) and os.path.samefile(folder, default_folder)
        except OSError:
            same = False
        if same:
            plain = dict(env)
            plain.pop('CLAUDE_CONFIG_DIR', None)
            plain.pop('CLAUDE_SECURESTORAGE_CONFIG_DIR', None)
            return read_live(ctx, fresh=fresh, interactive=interactive, env=plain)
    if kc.kind == PRESENT:
        return LiveRead(Read(PRESENT, value=kc.value or '', provenance='keychain'), parse(kc.value or ''), service)
    return LiveRead(Read(ABSENT), None, service)


NOT_KNOWN = object()        # write_live's `before` when the caller does not say what the item held


def ended_write(back, text, before=NOT_KNOWN):
    """What a write that was ended at its deadline did, told by a read of the item itself right after (a Read):
    'landed' (it holds text), 'unchanged' (it holds what it held before the write: the text `before`, or no item when
    before is None), or None (anything else, no answer, or `before` not known)."""
    if back.kind == PRESENT and back.value == text:
        return 'landed'
    if before is NOT_KNOWN:
        return None
    if (back.kind == ABSENT and before is None) or (back.kind == PRESENT and before is not None and
                                                    back.value == before):
        return 'unchanged'
    return None


def write_live(ctx, text, interactive=True, env=None, create_only=False, held=None, before=NOT_KNOWN, wrote=None,
               keep_file=False):
    """Put a login in Claude Code's live item (and its plaintext file when one exists). keychain.Done.

    Made under Claude Code's save lock on the item. held: that lock, when the caller already holds it (a caller that
    reads, decides and writes under it); else it is taken here, and ccfiles.LockBusy is raised when Claude Code keeps
    it past its budget, or when the lock given is no longer this process's (taken over as stale, or let go by
    ccfiles.release_all on the way out): nothing was written. create_only: only create the item (Done(False,
    'exists') when it is there). A write ended at its deadline (Done.ended: it can no longer land) is read back at
    once, in the same hold: holding `text`, it landed (Done(True)); holding `before` (the item's text before the
    write, None for no item), it did not (Done(False, 'unchanged')); anything else is not known, and the Keychain's
    answer is returned as it was. wrote (a dict, when given) gets 'file' once the write landed: the stamp of the
    plaintext file this write put there (ccfiles.bump_credentials_file), None when it rewrote none. Claude Code's plain
    login file, when the sign-in hold put it there (putback.hold_drop), is removed instead: the item holds the login.
    keep_file (a promote's write, and a put-back's with the sign-in hold on: the processes told "no such item" read
    their login from that file for a while yet): that file is never removed here. When it holds text byte for byte it
    is left exactly as it is; else it is rewritten with text (an older login in it must not be what a process on its
    way to the file finds). wrote['file'] is the stamp of the file as it stands after that; the caller says what
    becomes of it."""
    env = ctx.env if env is None else env
    if held is None:
        with storage_lock(ctx, env) as lock:
            return write_live(ctx, text, interactive=interactive, env=env, create_only=create_only, held=lock,
                              before=before, wrote=wrote, keep_file=keep_file)
    if not held.still_held():
        raise ccfiles.LockBusy(held.path)
    more = {'create_only': True} if create_only else {}
    service, account = facts.keychain_service(env), facts.keychain_account(env)
    done = ctx.keychain.write(service, account, text, interactive=interactive, **more)
    if not done.ok and getattr(done, 'ended', False):
        did = ended_write(ctx.keychain.read(service, account, fresh=True, interactive=True), text, before)
        if did == 'landed':
            done = keychain.Done(True)
        elif did == 'unchanged':
            done = keychain.Done(False, 'unchanged')
    if done.ok:
        from . import putback               # a plain login file the sign-in hold put there goes; it is never rewritten
        if keep_file:
            path = facts.credentials_file(env, ctx.home)
            stamp = putback.file_stamp(path)
            fr, raw = ccfiles.read_bytes(path)
            if stamp is None or fr.kind != PRESENT or raw != text.encode('utf-8') or \
                    putback.file_stamp(path) != stamp:
                stamp = ccfiles.bump_credentials_file(env, ctx.home, text, log=ctx.log,
                                                      announce=lambda st: putback.hold_own(ctx, st))
        else:
            stamp = None if putback.hold_drop(ctx, env) else ccfiles.bump_credentials_file(
                env, ctx.home, text, log=ctx.log, announce=lambda st: putback.hold_own(ctx, st))
        if wrote is not None:
            wrote['file'] = stamp
    return done


def storage_lock(ctx, env=None, budget_s=facts.STORAGE_LOCK_BUDGET_S):
    """Claude Code's save lock on the live item for this environment (ccfiles.storage_lock), waiting on ctx's clock."""
    env = ctx.env if env is None else env
    return ccfiles.storage_lock(env, ctx.home, budget_s=budget_s, mono=ctx.mono, sleep=ctx.sleep, log=ctx.log)


_CONFIG_CACHE = {}


def config_identity(ctx, env=None, cached=False):
    """(Read, oauthAccount dict or None, path) of the global config Claude Code reads. cached: answer from the last
    parse while the file's size, time and inode are unchanged (Claude Code's config can be megabytes; the page asks
    often). Anything that writes or decides reads it fresh."""
    import os
    env = ctx.env if env is None else env
    if cached:
        from . import facts
        path = facts.global_config(env, ctx.home)
        try:
            st = os.stat(path)
            stamp = (st.st_mtime_ns, st.st_size, st.st_ino)
        except OSError:
            stamp = None
        hit = _CONFIG_CACHE.get(path)
        if hit is not None and stamp is not None and hit[0] == stamp:
            return hit[1], hit[2], path
    path, r, obj = ccfiles.read_config(env, ctx.home)
    block = ccfiles.identity_of(obj)
    if cached and r.kind == PRESENT:
        try:
            st = os.stat(path)
            _CONFIG_CACHE[path] = ((st.st_mtime_ns, st.st_size, st.st_ino), Read(PRESENT, provenance='file'),
                                   dict(block) if block else None)
        except OSError:
            pass
    return r, block, path


def label(block):
    """(account uuid, email, organization uuid) from an oauthAccount block; '' for what it lacks."""
    if not isinstance(block, dict):
        return '', '', ''

    def s(k):
        v = block.get(k)
        return v.strip() if isinstance(v, str) else ''
    return s('accountUuid'), s('emailAddress'), s('organizationUuid')
