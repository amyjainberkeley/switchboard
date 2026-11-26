"""Refresh as a transaction: one consume lock per lineage, a marker, a whole deadline, the successor saved first.

A refresh token works once: the server retires it the moment it answers, even when the answer never arrives. So a
saved login is refreshed only like this:
1. Its consume lock (one refresher per lineage across Switchboard's processes), then, under the account lock, a fresh
   read of the saved login. A successor left by an earlier refresh that was cut off (in the recovery stash, or in
   memory) is saved instead of spending anything.
2. Nobody else may hold the lineage: the live login (read fresh) must not have the same refresh token, nor another
   generation of the same sign-in; a run-as must not have it. Claude Code owns the active lineage; we never spend it.
   Nor any sign-in of the account Claude Code is on: after its newer sign-in was put live, a Claude Code session may
   hold it while another writes the older one back, and two renewers of one sign-in end it.
3. After a wake, nothing until the network answers and 30 s have passed. The token host's name is resolved in a worker
   first; the POST goes out only with at least 25 s of the caller's budget left.
4. A marker (account, fingerprint, time) is saved before the POST. The POST runs in a worker that owns the consume
   lock until it is done: if the caller's deadline passes first, the worker still saves a late answer.
5. On success the successor is saved before anything else (current, the replaced one kept as an earlier
   generation, read back); if that fails it goes to the recovery stash, then a recovery file, then memory, and the
   marker stays so the next run saves it instead of spending the dead token. Then the marker goes.
6. Verdicts: HTTP 400/401/403 with a JSON body whose "error" is "invalid_grant" is permanent: one strike, bound to the
   fingerprint of the bytes posted, shown as "needs sign-in". "invalid_client" strikes nobody. Everything else
   (5xx, 429, timeouts, a body that can't be read) is transient and never strikes.
Every start, success, failure and unanswered POST is logged with the fingerprint, never the token.
"""
import atexit
import contextlib
import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional

from . import facts, live as live_mod, vault
from .api import ABSENT, PRESENT, UNREADABLE
from .net import NetError

POST_MIN_LEFT_S = 25.0
RESPONSE_TIMEOUT_S = 30.0
DNS_DEADLINE_S = 10.0
PERSIST_RESERVE_S = 10.0
UPKEEP_EVERY_S = 6 * 3600.0
UPKEEP_JITTER_S = 30 * 60.0
DUE_BEFORE_EXPIRY_S = 2 * 3600.0
WAKE_GAP_S = 60.0
WAKE_SETTLE_S = 30.0
BACKOFF_BASE_S = 15 * 60.0
BACKOFF_CAP_S = 6 * 3600.0
SKIP_WAIT_S = 30 * 60.0         # an account whose lineage Claude Code holds is looked at again after this long
RETRY_AFTER_CAP_S = 24 * 3600.0
EXIT_WAIT_S = RESPONSE_TIMEOUT_S + PERSIST_RESERVE_S    # a process that ends waits this long for a refresh already
                                                        # sent (its answer, then the save); whoever stops the loop
                                                        # waits longer than this before a kill (engine.AutoHost)
HELD_ELSEWHERE = ('live', 'live_same_login', 'live_same_account', 'live_unsure', 'live_missing')
TOKEN_SETTLED_S = 1800.0        # after a switch onto a one-year token, how long the account it is for stays held
CONFLICT = 'identity_conflict'  # a recovery entry holding another account's login (a token answer named it)


@dataclass(frozen=True)
class Result:
    """outcome: 'refreshed', 'adopted' (a successor or newer login was already there: nothing spent), 'skipped' (why),
    'transient', 'permanent', 'systemic', 'unanswered', 'stashed' (spent; successor in recovery), 'unpersisted'.
    consumed_fp: the fingerprint of the generation posted. spent_maybe: the posted token may be used up, so it must
    not be written anywhere as a working login."""
    outcome: str
    account_id: str
    why: str = ''
    consumed_fp: Optional[str] = None
    new_fp: Optional[str] = None
    spent_maybe: bool = False
    text: Optional[str] = field(default=None, repr=False)
    retry_after_s: Optional[float] = None       # the wait a refused POST's Retry-After asked for


_INFLIGHT = {}
_INFLIGHT_LOCK = threading.Lock()
STOPPING = threading.Event()            # this process was asked to stop (the loop's SIGTERM): no new refresh starts;
                                        # one already sent still saves its answer (and exit waits for it, below)
_UNPERSISTED = {}                       # account id -> (fingerprint it replaces, successor text)


def classify(status, body):
    """('ok', answer dict) | ('permanent', 'invalid_grant') | ('systemic', 'invalid_client') | ('spent', why) for a 200
    we could not read | ('transient', why)."""
    try:
        obj = json.loads(body.decode('utf-8')) if body else None
    except (ValueError, UnicodeDecodeError, AttributeError):
        obj = None
    if status == 200:
        if isinstance(obj, dict) and isinstance(obj.get('access_token'), str) and obj['access_token'] and \
                isinstance(obj.get('expires_in'), (int, float)) and not isinstance(obj.get('expires_in'), bool):
            return 'ok', obj
        return 'spent', 'bad-response'
    if status in (400, 401, 403) and isinstance(obj, dict):
        if obj.get('error') == 'invalid_grant':
            return 'permanent', 'invalid_grant'
        if obj.get('error') == 'invalid_client':
            return 'systemic', 'invalid_client'
    if status == 429:
        return 'transient', 'rate_limited'
    return 'transient', 'http-%d' % status


def build_successor(old_text, answer, now_ms):
    """The saved login after a refresh: new tokens and expiry, Claude Code's refresh-expiry rule, everything else as
    it was."""
    blob = json.loads(old_text)
    oauth = dict(blob[facts.OAUTH_KEY])
    oauth['accessToken'] = answer['access_token']
    rt = answer.get('refresh_token')
    if isinstance(rt, str) and rt:
        oauth['refreshToken'] = rt
    oauth['expiresAt'] = int(now_ms + round(float(answer['expires_in']) * 1000))
    old_rx = live_mod._ms(oauth.get('refreshTokenExpiresAt'))
    rin = answer.get('refresh_token_expires_in')
    new_rx = int(now_ms + round(float(rin) * 1000)) if isinstance(rin, (int, float)) and \
        not isinstance(rin, bool) and rin > 0 else None
    if old_rx is not None:
        oauth['refreshTokenExpiresAt'] = min(old_rx, new_rx) if new_rx else old_rx
    else:
        oauth.pop('refreshTokenExpiresAt', None)
    scope = answer.get('scope')
    if isinstance(scope, str) and scope.split():
        oauth['scopes'] = scope.split()
    blob[facts.OAUTH_KEY] = oauth
    return live_mod.dump(blob)


# ---------- wake ----------

class _Wake:
    """Notices sleep (the wall clock moved further than the monotonic one) and holds network work until the network
    has answered for WAKE_SETTLE_S."""

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.last = None
        self.woke = None
        self.reach_since = None

    def observe(self, ctx):
        wall, mono = ctx.wall(), ctx.mono()
        with self.lock:
            if self.last is not None and (wall - self.last[0]) - (mono - self.last[1]) > WAKE_GAP_S:
                self.woke, self.reach_since = mono, None
            self.last = (wall, mono)
            return self.woke is not None

    def settled(self, ctx, reachable):
        with self.lock:
            if self.woke is None:
                return True
            mono = ctx.mono()
            if not reachable:
                self.reach_since = None
                return False
            if self.reach_since is None:
                self.reach_since = mono
            if mono - self.reach_since >= WAKE_SETTLE_S:
                self.woke = None
                return True
            return False


WAKE = _Wake()


# ---------- the transaction ----------

def _held_by_a_slot(doc, e):
    """Does a kept account still hold the generation a set-aside login replaces (so that login is its only working
    one)? For an entry naming its owner, only that owner's slots count."""
    return any(o.get('fp') and o.get('fp') == e.get('supersedes') and oid != e.get('account_id') and
               (not e.get('names') or o.get('uuid') == e.get('names'))
               for oid, o in doc['accounts'].items())


def _find_successor(ctx, aid, login, marked=False, doc=None):
    """A login that replaces `login` and was left by an earlier refresh: in memory, or in the recovery stash. marked:
    a refresh of these bytes was in flight, so the successor's own item is also read directly (its manifest entry
    may be missing when the manifest could not be written). doc (under the account lock): set-aside logins of an
    identity conflict are offered to the account Anthropic named, and one this account set aside is forgotten once
    its mark is gone and no kept account needs it.
    Returns (text, its recovery key or None for one held in memory, None); (None, None, None) when there is none;
    and (None, key, why) when what is kept under key can't be read just now (the Keychain is locked or slow): a
    successor may be there, and then `login` is spent."""
    mem = _UNPERSISTED.get(aid)
    if mem and mem[0] == login.fingerprint:
        return mem[1], None, None
    a = (doc or {}).get('accounts', {}).get(aid) or {}
    entries = vault.stashed(ctx)
    unread = None
    for e in entries:
        if e.get('reason') == CONFLICT:
            # another account's login, set aside: never put in the slot it was taken from; it belongs to the account
            # the answer named, when that account still holds the generation it replaces
            if not (a.get('uuid') and e.get('names') == a.get('uuid') and e.get('account_id') != aid and
                    e.get('supersedes') == login.fingerprint):
                if doc is not None and e.get('account_id') == aid and not a.get('conflict') and \
                        not _held_by_a_slot(doc, e):
                    vault.drop_stash(ctx, e['key'])     # the mark is gone and nobody needs that login any more
                continue
            r = vault.read_stash(ctx, e['key'], e.get('where'))
            if r.kind == PRESENT:
                cand = live_mod.parse(r.value)
                if cand.full_pair and cand.fingerprint != login.fingerprint:
                    return r.value, e['key'], None
            elif r.kind == UNREADABLE:
                unread = unread or (e['key'], r.why)
            continue
        if e.get('account_id') == aid and e.get('supersedes') == login.fingerprint:
            r = vault.read_stash(ctx, e['key'], e.get('where'))
            if r.kind == PRESENT:
                cand = live_mod.parse(r.value)
                if cand.full_pair and cand.fingerprint != login.fingerprint:
                    return r.value, e['key'], None
            elif r.kind == UNREADABLE:
                unread = unread or (e['key'], r.why)
    key = 'successor:' + aid
    if marked and not any(e.get('key') == key for e in entries):
        r = vault.read_stash(ctx, key)
        if r.kind == PRESENT:
            cand = live_mod.parse(r.value)
            if cand.full_pair and facts.same_sign_in(cand.refresh_expires_ms, login.refresh_expires_ms) \
                    and live_mod.compare(cand, login) == 'newer':
                return r.value, key, None
        elif r.kind == UNREADABLE:
            unread = unread or (key, r.why)
    return (None,) + (unread or (None, None))


def needs_refresh(login, now_s):
    """Is a saved login close enough to its expiry to be worth a refresh (or its expiry not known)?"""
    return login.expires_ms is None or login.expires_ms / 1000.0 - now_s < DUE_BEFORE_EXPIRY_S + UPKEEP_JITTER_S


WAITING = 'waiting login'            # how the `why` of a Result begins when a successor waits and is not saved,
SAVE_FAILED = 'save failed'          # and when a renewal's own answer could not be saved (it waits from then on)


def _waiting(ctx, doc, a, aid, cur, login, now):
    """Under the account lock: a login that replaces the saved one (cur, parsed as login) may be waiting from an
    earlier renewal whose save failed. Saved now: Result 'adopted'. Found, and it still can't be saved: it waits where
    it is, and the saved login is spent, so it is never posted again (Anthropic would refuse it, and the account
    would be marked as needing a sign-in while its working login is held here) and never put in use. One that may be
    there but can't be read just now gets the same care. None: nothing waits."""
    marked = (doc['refreshing'].get(aid) or {}).get('fp') == login.fingerprint
    text, key, why = _find_successor(ctx, aid, login, marked, doc)
    if text:
        new = live_mod.parse(text)
        done = vault.save_login(ctx, aid, text, old=cur)
        if done.ok:
            vault.note_login(a, new, proof=None, now=now)
            doc['refreshing'].pop(aid, None)
            vault.save(ctx, doc)
            _UNPERSISTED.pop(aid, None)
            if key:
                vault.drop_stash(ctx, key)
            ctx.log('Saved the waiting new login of %s (%s -> %s); nothing was spent'
                    % (a.get('email') or aid, login.fingerprint, new.fingerprint))
            return Result('adopted', aid, 'successor saved', new_fp=new.fingerprint, text=text)
        return Result('stashed' if key else 'unpersisted', aid, '%s not saved: %s' % (WAITING, done.why or 'unknown'),
                      consumed_fp=login.fingerprint, new_fp=new.fingerprint, spent_maybe=True, text=text)
    if key:
        return Result('transient', aid, '%s unreadable: %s' % (WAITING, why or 'unknown'),
                      consumed_fp=login.fingerprint, spent_maybe=True)
    return None


def adopt_waiting(ctx, aid):
    """Before a saved login that has not run out is put in use (a switch, a run): save the login that replaces it,
    when one waits. A renewal goes out well before the saved login's time is up, so after a renewal whose save failed
    the spent login still looks usable for a while. The caller holds the account's consume lock. Never posts, and asks
    nothing of the network. Result 'adopted': saved now, so read the saved login again. spent_maybe: one waits, or may
    (it can't be read just now), and is not saved: the saved login must not be put in use. Anything else: nothing
    waits, or it can't be told right now, and the caller goes on as it would have."""
    try:
        doc = vault.load(ctx)
        a = doc['accounts'].get(aid) or {}
        if not (aid in _UNPERSISTED or aid in (doc.get('refreshing') or {}) or any(
                e.get('account_id') == aid or (a.get('uuid') and e.get('names') == a.get('uuid'))
                for e in vault.stashed(ctx) if isinstance(e, dict))):
            return Result('skipped', aid, 'nothing waiting')        # the usual case: no account lock, no Keychain
        with vault.account_lock(ctx):
            doc = vault.load(ctx)
            a = doc['accounts'].get(aid)
            if a is None or a.get('conflict') or aid in doc.get('claims', {}) or vault.claimed(ctx, doc, aid):
                return Result('skipped', aid, 'not now')            # each has its own words where the caller checks
            cur = vault.read_current(ctx, aid, fresh=True, interactive=True)
            if cur.kind != PRESENT:
                return Result('skipped', aid, 'saved login not read')
            login = live_mod.parse(cur.value)
            if login.kind != 'oauth' or not login.has_refresh:
                return Result('skipped', aid, 'no_refresh_token')
            return _waiting(ctx, doc, a, aid, cur, login, ctx.wall()) or Result('skipped', aid, 'nothing waiting')
    except (vault.LockBusy, vault.VaultError, OSError) as e:
        return Result('skipped', aid, 'store busy or unreadable: %s' % e.__class__.__name__)
    except Exception as e:  # noqa: BLE001 - this look is never what stops a switch or a run; the log says it failed
        ctx.log('WARNING: could not look for a waiting login of %s: %s' % (aid, e.__class__.__name__))
        return Result('skipped', aid, 'could not look: %s' % e.__class__.__name__)


def _phase_saved(ctx, aid, now, when_due=True):
    """Under the account lock: the saved login to refresh, or a Result that ends here."""
    with vault.account_lock(ctx):
        doc = vault.load(ctx)
        a = doc['accounts'].get(aid)
        if a is None:
            return Result('skipped', aid, 'removed'), None
        if vault.claimed(ctx, doc, aid):
            return Result('skipped', aid, 'claimed'), None
        if aid in doc.get('claims', {}):
            return Result('skipped', aid, 'claim_unreaped'), None   # a finished run-as may hold a newer generation
        if a.get('conflict'):
            # Anthropic said its saved login is another account's: nothing of it is spent or adopted here, until a
            # login proven to be this account's replaces it (vault.note_login clears the mark)
            return Result('skipped', aid, 'conflict'), None
        cur = vault.read_current(ctx, aid, fresh=True, interactive=True)
        if cur.kind == UNREADABLE:
            a['unreadable_at'] = now
            vault.save(ctx, doc)
            return Result('transient', aid, 'saved login unreadable: %s' % cur.why), None
        if cur.kind == ABSENT:
            return Result('skipped', aid, 'no_saved_login'), None
        login = live_mod.parse(cur.value)
        if login.kind != 'oauth' or not login.has_refresh:
            return Result('skipped', aid, 'no_refresh_token'), None
        # A login that replaces this one may be waiting from an earlier renewal whose save failed. It is looked for
        # before the mark of a refusal: a refused login whose replacement is held here needs no sign-in.
        ended = _waiting(ctx, doc, a, aid, cur, login, now)
        if ended is not None:
            return ended, None
        if (a.get('strike') or {}).get('fp') == login.fingerprint:
            return Result('skipped', aid, 'needs_login'), None
        changed = a.pop('unreadable_at', None) is not None
        if changed:
            vault.save(ctx, doc)
        if when_due and not needs_refresh(login, now):
            return Result('adopted', aid, 'already fresh', new_fp=login.fingerprint), None   # someone just did it
        marker = doc['refreshing'].get(aid) or {}
        return None, (login, cur, marker.get('fp') == login.fingerprint, a.get('email') or aid)


def _held_elsewhere(ctx, login, aid=None):
    """Why another holder has this lineage (so we must not spend it), or None. Reads the live login fresh. aid: the
    account whose saved login this is. Claude Code's renewals can lower the sign-in's stamp a little, so a live login
    with another token and another stamp may still be this account's (_held_by_live). And when Claude Code is on
    account aid by any sign-in of it, its kept copy is not renewed either ('live_same_account', _in_use)."""
    lr = live_mod.read_live(ctx, fresh=True)
    if lr.read.kind == UNREADABLE or lr.read.degraded:
        return 'live_unreadable'                        # can't prove nobody else holds it (a stand-in copy may be old)
    other = lr.login
    held = _held_by_live(ctx, login, other, aid)
    if held is None and aid is not None and _in_use(ctx, other, aid):
        held = 'live_same_account'
    return held


def _held_by_live(ctx, login, other, aid):
    """Why Claude Code's live login (`other`) holds this lineage, or None. It is spent only when the live login is
    proved to be another account's, or plainly another sign-in. Anthropic's profile is asked at most once, outside
    every lock; no answer means "held" ('live_unsure')."""
    if aid is not None and (other is None or other.kind in ('no_login', 'empty')):
        # Claude Code lost its login while its settings still name a kept account (a chosen sign-out clears them).
        # The open chats may still hold the sign-in of any account Claude Code may be on: the one the settings name
        # (a chat open during a switch can write the earlier name back, so it may not be theirs), the one Switchboard
        # put in use, and the two of the last switch. Nobody knows which, and an idle chat can bring its sign-in back
        # by itself hours later, so those wait for as long as the login stays lost: renewing one now would spend the
        # key a chat holds, or end the access token it works with. A put-back may be writing one of them meanwhile.
        # No chat can be on any other kept account, so it is renewed as on any other day.
        from . import identity
        try:
            doc = vault.load(ctx)
        except vault.VaultError:
            return 'live_unsure'
        _, block, _ = live_mod.config_identity(ctx)
        named = identity.label_id(doc, block)
        if named is None:
            return None
        last = (doc.get('switches') or [None])[-1]
        maybe = {named, doc.get('active')} | ({last.get('from'), last.get('to')} if isinstance(last, dict) else set())
        return 'live_missing' if aid in maybe else None
    if other is None or other.kind != 'oauth':
        return None
    if live_mod.unrenewable(other):
        # a one-year token, or a login with no refresh token: it renews nothing, so it holds no sign-in here, and it is
        # never sent to Anthropic to ask whose it is (_in_use still holds the account it is proved or named for)
        return None
    if other.fingerprint and other.fingerprint == login.fingerprint:
        return 'live'
    if other.refresh_expires_ms and other.refresh_expires_ms == login.refresh_expires_ms:
        return 'live_same_login'
    if aid is None:
        return None
    from . import identity
    try:
        doc = vault.load(ctx)
    except vault.VaultError:
        return 'live_unsure'
    _, block, _ = live_mod.config_identity(ctx)
    proof = identity.offline(other, doc, block, near=True)
    ans = identity.cached_profile(other)
    resolved = ans is not None and ans.status == 'resolved'
    if proof.account_id == aid or (resolved and identity.from_profile(doc, ans, block).account_id == aid):
        return 'live_same_login'
    exact = proof.account_id is not None and \
        doc['accounts'][proof.account_id].get('rx_ms') == other.refresh_expires_ms
    if proof.kind == 'fingerprint' or exact or resolved:
        return None                                     # proved to be another account's login
    # settings that name another saved account while the live login matches no saved account may be out of date (a
    # session open during a switch writes the earlier name back): then Anthropic is asked, so a sign-in of this
    # account not kept yet is never spent on the settings' word alone
    can_ask = other.has_access and not other.expired(ctx.wall())
    lid = identity.label_id(doc, block)
    stale = can_ask and proof.account_id is None and lid is not None
    if not stale and lid != aid and not facts.same_sign_in(other.refresh_expires_ms, login.refresh_expires_ms):
        return None                                     # another sign-in, and nothing says it is this account's
    if not can_ask:
        return 'live_unsure'
    ans = identity.lookup_profile(ctx, other.access_token)
    if ans.status != 'resolved':
        return 'live_unsure'
    return 'live_same_login' if identity.from_profile(doc, ans, block).account_id == aid else None


def _in_use(ctx, other, aid):
    """Is account aid the one Claude Code is on: its live login (`other`) proved aid's from the saved accounts alone,
    or its settings naming aid? Then a Claude Code session may hold any sign-in of aid, the newest kept one included
    (it was put live, and another session can write an older one back a moment later), so its kept copy is not
    renewed here: Claude Code renews what it uses. The settings alone are enough, since this only ever holds a
    renewal back (a switch to aid then writes its login as it is, and Claude Code renews it). On a one-year token a
    switch put there more than TOKEN_SETTLED_S ago, the settings alone hold nothing (_on_token_since)."""
    from . import identity
    try:
        doc = vault.load(ctx)
    except vault.VaultError:
        return False
    if other is not None and other.kind == 'oauth' and identity.offline(other, doc).account_id == aid:
        return True
    if other is not None and live_mod.unrenewable(other) and _on_token_since(ctx, doc, other) > TOKEN_SETTLED_S:
        return False
    r, block, _ = live_mod.config_identity(ctx)
    return r.kind == PRESENT and identity.label_id(doc, block) == aid


def _on_token_since(ctx, doc, other):
    """How long ago the last recorded switch put Claude Code's live login, a kept one-year token, there; -1 when the
    last switch went elsewhere (the token came some other way, at a time not known). Claude Code can't renew a token,
    so after a switch onto one only a chat still holding the sign-in it was on before could renew that sign-in, and
    such chats read the item again within minutes: after TOKEN_SETTLED_S the settings' name alone holds nothing."""
    from . import bearer
    last = (doc.get('switches') or [None])[-1]
    bid = bearer.owns(doc, other)
    at = last.get('at') if isinstance(last, dict) and bid is not None and last.get('to') == bid else None
    if not isinstance(at, (int, float)) or isinstance(at, bool) or at > ctx.wall():
        return -1.0
    return ctx.wall() - at


def refresh(ctx, aid, budget_s=60.0, consume=None, reason='upkeep', when_due=True):
    """Refresh account aid's saved login inside the transaction. consume: the account's consume lock when the caller
    already holds it (a switch); it stays the caller's. when_due: once the lock is held, a login that is no longer close
    to expiry (another refresher just renewed it) is left alone. Never raises for an outcome; returns a Result."""
    start = ctx.mono()
    deadline = start + budget_s
    if ctx.env.get('CLAUDE_SECURESTORAGE_CONFIG_DIR'):
        return Result('skipped', aid, 'store_unmirrored')
    if STOPPING.is_set():
        return Result('skipped', aid, 'stopping')
    with _INFLIGHT_LOCK:
        if aid in _INFLIGHT:
            return Result('skipped', aid, 'in_flight')
    own = consume is None
    if own:
        try:
            consume = vault.consume_lock(ctx, aid, timeout=min(5.0, budget_s))
        except vault.LockBusy:
            return Result('skipped', aid, 'busy')
    handed = False
    try:
        try:
            ended, got = _phase_saved(ctx, aid, ctx.wall(), when_due)
        except (vault.LockBusy, vault.VaultError) as e:
            return Result('skipped', aid, 'store busy or unreadable: %s' % e.__class__.__name__)
        if ended is not None:
            return ended
        login, cur, cut_off, who = got
        held = _held_elsewhere(ctx, login, aid)
        if held:
            return Result('skipped', aid, held)
        WAKE.observe(ctx)
        reachable = ctx.http.resolve(facts.TOKEN_HOST, min(DNS_DEADLINE_S, max(0.0, deadline - ctx.mono())))
        if not WAKE.settled(ctx, reachable):
            return Result('skipped', aid, 'just_woke')
        if not reachable:
            return Result('transient', aid, 'dns')
        left = deadline - ctx.mono()
        if left < POST_MIN_LEFT_S:
            return Result('skipped', aid, 'no_time')
        if STOPPING.is_set():
            return Result('skipped', aid, 'stopping')       # asked to stop meanwhile: nothing marked, nothing sent
        try:
            with vault.account_lock(ctx):
                doc = vault.load(ctx)
                a = doc['accounts'].get(aid)
                if a is None or a.get('fp') != login.fingerprint:
                    return Result('adopted', aid, 'changed while waiting')
                doc['refreshing'][aid] = {'fp': login.fingerprint, 'at': ctx.wall(), 'reason': reason}
                vault.save(ctx, doc)
        except (vault.LockBusy, vault.VaultError, OSError) as e:
            return Result('skipped', aid, 'could not mark: %s' % e.__class__.__name__)
        ctx.log('Refreshing the saved login of %s (%s, %s)' % (who, login.fingerprint, reason))
        job = _Job(ctx, aid, login, cur.value, consume if own else None, cut_off, who)
        with _INFLIGHT_LOCK:
            _INFLIGHT[aid] = job
        handed = own
        post_timeout = max(5.0, min(RESPONSE_TIMEOUT_S, deadline - ctx.mono() - PERSIST_RESERVE_S))
        threading.Thread(target=job.run, args=(post_timeout,), name='refresh-%s' % aid[:8], daemon=True).start()
        if job.done.wait(timeout=max(0.0, deadline - ctx.mono())):
            return job.result
        ctx.log('WARNING: no answer yet to the refresh of %s (%s); its new login will be saved when it comes'
                % (who, login.fingerprint))
        return Result('unanswered', aid, 'no answer yet', consumed_fp=login.fingerprint, spent_maybe=True)
    finally:
        if own and not handed:
            consume.release()


class _Job:
    def __init__(self, ctx, aid, login, old_text, consume, cut_off, who):
        self.ctx, self.aid, self.login, self.old_text = ctx, aid, login, old_text
        self.consume, self.cut_off, self.who = consume, cut_off, who
        self.done = threading.Event()
        self.result = None

    def run(self, post_timeout):
        try:
            self.result = self._post(post_timeout)
        except Exception as e:  # noqa: BLE001 - the worker never dies with a successor in hand
            self.ctx.log('Refresh of %s stopped: %s' % (self.who, e.__class__.__name__))
            self.result = Result('transient', self.aid, 'error: %s' % e.__class__.__name__,
                                 consumed_fp=self.login.fingerprint, spent_maybe=True)
        finally:
            with _INFLIGHT_LOCK:
                if _INFLIGHT.get(self.aid) is self:
                    del _INFLIGHT[self.aid]
            if self.consume is not None:
                self.consume.release()
            self.done.set()

    def _post(self, post_timeout):
        ctx, fp = self.ctx, self.login.fingerprint
        body = json.dumps({'grant_type': 'refresh_token', 'refresh_token': self.login.refresh_token,
                           'client_id': facts.CLIENT_ID}).encode('utf-8')
        try:
            with _held_awake(ctx, post_timeout):    # a sleep between the request and its answer would lose the login
                status, hdrs, data = ctx.http.request('POST', facts.TOKEN_URL, headers={
                    'Content-Type': 'application/json', 'User-Agent': ctx.user_agent}, body=body,
                    timeout_s=post_timeout)
        except NetError as e:
            if e.sent:
                ctx.log('WARNING: the refresh of %s (%s) went out but no answer came (%s): it may be used up; the '
                        'marker stays' % (self.who, fp, e.kind))
                return Result('transient', self.aid, e.kind, consumed_fp=fp, spent_maybe=True)
            self._clear_marker()
            ctx.log('Refresh of %s (%s) did not go out: %s' % (self.who, fp, e.kind))
            return Result('transient', self.aid, e.kind, consumed_fp=fp)
        verdict, info = classify(status, data)
        if verdict == 'ok':
            return self._persist(build_successor(self.old_text, info, int(ctx.wall() * 1000)), info)
        if verdict == 'spent':
            ctx.log('WARNING: the refresh of %s (%s) was answered but the answer could not be read: it may be used up'
                    % (self.who, fp))
            return Result('transient', self.aid, info, consumed_fp=fp, spent_maybe=True)
        self._clear_marker()
        if verdict == 'permanent':
            return self._strike()
        if verdict == 'systemic':
            ctx.log('Anthropic refused Claude Code\'s sign-in client (invalid_client); no account is blamed')
            return Result('systemic', self.aid, info, consumed_fp=fp)
        ctx.log('Refresh of %s (%s) failed for now: %s' % (self.who, fp, info))
        return Result('transient', self.aid, info, consumed_fp=fp, retry_after_s=_retry_after(hdrs))

    def _clear_marker(self):
        try:
            with vault.account_lock(self.ctx):
                doc = vault.load(self.ctx)
                m = doc['refreshing'].get(self.aid) or {}
                if m.get('fp') == self.login.fingerprint:
                    doc['refreshing'].pop(self.aid, None)
                    vault.save(self.ctx, doc)
        except (vault.LockBusy, vault.VaultError, OSError):
            pass

    def _strike(self):
        ctx, fp = self.ctx, self.login.fingerprint
        try:
            with vault.account_lock(ctx):
                doc = vault.load(ctx)
                a = doc['accounts'].get(self.aid)
                if a is None or a.get('fp') != fp:
                    return Result('transient', self.aid, 'superseded', consumed_fp=fp)
                first = (a.get('strike') or {}).get('fp') != fp
                a['strike'] = {'fp': fp, 'at': ctx.wall(), 'why': 'invalid_grant', 'after_cut_off': self.cut_off}
                vault.save(ctx, doc)
        except (vault.LockBusy, vault.VaultError, OSError):
            return Result('transient', self.aid, 'could not record the refusal', consumed_fp=fp)
        ctx.log('Anthropic refused the saved login of %s (%s): needs sign-in' % (self.who, fp))
        if first:
            why = ('a refresh of it was cut off earlier and its new login was lost' if self.cut_off else
                   'Anthropic refused its saved login')
            ctx.say('%s needs sign-in: %s' % (self.who, why), 'warn', h='problem', account=self.who)
        return Result('permanent', self.aid, 'invalid_grant', consumed_fp=fp)

    def _persist(self, text, answer):
        ctx, aid, fp = self.ctx, self.aid, self.login.fingerprint
        new = live_mod.parse(text)
        acct = answer.get('account') if isinstance(answer.get('account'), dict) else {}
        token_uuid = acct.get('uuid') if isinstance(acct.get('uuid'), str) and acct.get('uuid') else None
        try:
            with vault.account_lock(ctx):
                doc = vault.load(ctx)
                a = doc['accounts'].get(aid)
                cur = vault.read_current(ctx, aid, fresh=True, interactive=True)
                same = cur.kind == PRESENT and live_mod.parse(cur.value).fingerprint == fp
                if a is not None and same:
                    if token_uuid and a.get('uuid') and token_uuid != a.get('uuid'):
                        kept, _ = vault.stash(ctx, 'successor:' + aid, text, aid, CONFLICT, supersedes=fp,
                                              names=token_uuid)
                        a['conflict'] = {'token_uuid': token_uuid, 'at': ctx.wall()}
                        doc['refreshing'].pop(aid, None)
                        owner = _give_to_owner(ctx, doc, aid, token_uuid, fp, new, text)
                        vault.save(ctx, doc)
                        if owner and kept:
                            vault.drop_stash(ctx, 'successor:' + aid)   # saved in its owner's slot, read back
                        ctx.say('The saved login of %s belongs to another account; it was set aside' % self.who,
                                'warn', h='problem', account=self.who)
                        return Result('stashed', aid, 'identity_conflict', consumed_fp=fp, new_fp=new.fingerprint,
                                      spent_maybe=True, text=text)
                    done = vault.save_login(ctx, aid, text, old=cur)
                    if done.ok:
                        vault.note_login(a, new, proof='token_account' if token_uuid else None, now=ctx.wall())
                        doc['refreshing'].pop(aid, None)
                        vault.save(ctx, doc)
                        _UNPERSISTED.pop(aid, None)
                        ctx.log('Refreshed the saved login of %s (%s -> %s)' % (self.who, fp, new.fingerprint))
                        return Result('refreshed', aid, consumed_fp=fp, new_fp=new.fingerprint, text=text)
                    ok, _ = vault.stash(ctx, 'successor:' + aid, text, aid, 'save failed: %s' % done.why,
                                        supersedes=fp)
                    if ok:
                        return Result('stashed', aid, '%s: %s' % (SAVE_FAILED, done.why or 'unknown'), consumed_fp=fp,
                                      new_fp=new.fingerprint, spent_maybe=True, text=text)
                elif a is None or cur.kind == ABSENT:
                    ok, _ = vault.stash(ctx, 'successor:' + aid, text, aid, 'removed while refreshing',
                                        supersedes=fp)
                    doc['refreshing'].pop(aid, None)
                    vault.save(ctx, doc)
                    if ok:
                        return Result('stashed', aid, 'removed', consumed_fp=fp, new_fp=new.fingerprint, text=text)
                elif cur.kind == PRESENT:
                    ok, _ = vault.stash(ctx, 'successor:' + aid, text, aid, 'replaced while refreshing',
                                        supersedes=fp)
                    doc['refreshing'].pop(aid, None)
                    vault.save(ctx, doc)
                    if ok:
                        return Result('adopted', aid, 'replaced while refreshing', consumed_fp=fp, text=text)
                else:
                    ok, _ = vault.stash(ctx, 'successor:' + aid, text, aid, 'saved login unreadable: %s' % cur.why,
                                        supersedes=fp)
                    if ok:
                        return Result('stashed', aid, 'saved login unreadable', consumed_fp=fp,
                                      new_fp=new.fingerprint, spent_maybe=True, text=text)
        except (vault.LockBusy, vault.VaultError, OSError) as e:
            ok, _ = vault.stash(ctx, 'successor:' + aid, text, aid, 'store busy: %s' % e.__class__.__name__,
                                supersedes=fp)
            if ok:
                return Result('stashed', aid, 'store busy', consumed_fp=fp, new_fp=new.fingerprint, spent_maybe=True,
                              text=text)
        _UNPERSISTED[aid] = (fp, text)
        ctx.log('ERROR: the new login of %s (%s) could not be saved anywhere; it is held in memory until it can be'
                % (self.who, new.fingerprint))
        return Result('unpersisted', aid, 'not saved', consumed_fp=fp, new_fp=new.fingerprint, spent_maybe=True,
                      text=text)


def _give_to_owner(ctx, doc, aid, token_uuid, spent_fp, new, text):
    """Under the account lock, in an identity conflict: the refresh answer named another kept account as the login's
    owner, and that account's saved login is the very generation just spent (one login kept in two slots, the other
    on a label alone). Its new login is that account's own, so it is saved there; otherwise that account would need a
    sign-in for a login Switchboard holds. Returns that account's id, or None (then it waits in the recovery stash,
    where _find_successor offers it to that account)."""
    for oid, o in doc['accounts'].items():
        if oid == aid or o.get('uuid') != token_uuid or o.get('fp') != spent_fp or o.get('conflict'):
            continue
        if vault.claimed(ctx, doc, oid) or oid in doc.get('claims', {}):
            return None                     # a `switchboard run` holds it: it saves back what it renews itself
        try:
            cur = vault.read_current(ctx, oid, fresh=True, interactive=True)
            if cur.kind != PRESENT or live_mod.parse(cur.value).fingerprint != spent_fp:
                return None
            if not vault.save_login(ctx, oid, text, old=cur).ok:
                return None
        except Exception as e:  # noqa: BLE001 - the login is in the recovery stash either way
            ctx.log('The new login of %s waits in recovery: %s' % (o.get('email') or oid, e.__class__.__name__))
            return None
        vault.note_login(o, new, proof='token_account', now=ctx.wall())
        doc['refreshing'].pop(oid, None)
        ctx.log('Saved the new login of %s, which Anthropic named as its owner (%s -> %s)'
                % (o.get('email') or oid, spent_fp, new.fingerprint))
        return oid
    return None


def _sleeping_soon(ctx):
    """The Mac is about to sleep (its lid shut on battery: ctx.sleeping_soon). False when that can't be told."""
    look = getattr(ctx, 'sleeping_soon', None)
    try:
        return bool(look()) if look is not None else False
    except Exception:  # noqa: BLE001 - not known reads as awake: the renewal goes as before
        return False


def _held_awake(ctx, seconds):
    """The Mac held awake for the one renewal request, never longer than its own deadline (ctx.hold_awake: the page's
    caffeinate, awake.held_awake), let go when the request returns or fails. Holds nothing without one."""
    hold = getattr(ctx, 'hold_awake', None)
    if hold is None:
        return contextlib.nullcontext()
    try:
        return hold(seconds)
    except Exception as e:  # noqa: BLE001 - the renewal goes either way; only the hold is lost
        ctx.log('The Mac could not be held awake for a renewal: %s' % e.__class__.__name__)
        return contextlib.nullcontext()


def _wait_at_exit(limit_s=EXIT_WAIT_S):
    """A process that ends while a refresh is out waits for its answer, so a successor is not lost with it."""
    end = time.monotonic() + limit_s
    while True:
        with _INFLIGHT_LOCK:
            jobs = list(_INFLIGHT.values())
        if not jobs:
            return
        left = end - time.monotonic()
        if left <= 0:
            return
        jobs[0].done.wait(min(left, 1.0))


atexit.register(_wait_at_exit)


# ---------- idle upkeep ----------

def _jitter(aid, span):
    h = int(hashlib.sha256(aid.encode('utf-8')).hexdigest()[:8], 16)
    return (h / float(0xFFFFFFFF) * 2.0 - 1.0) * span


def due(doc, aid, now_s):
    """Does account aid's saved login want a refresh now? (Every ~6 h, before its access token gets close to expiry,
    not while a failure is resting, never a refused one.)"""
    a = doc['accounts'].get(aid) or {}
    u = doc['upkeep'].get(aid) or {}
    if not a.get('fp') or a.get('conflict'):
        return False
    if (a.get('strike') or {}).get('fp') == a.get('fp'):
        return False
    if u.get('next_at') and now_s < u['next_at']:
        return False
    exp = a.get('expires_ms')
    if exp and exp / 1000.0 - now_s < DUE_BEFORE_EXPIRY_S + _jitter(aid, UPKEEP_JITTER_S / 2):
        return True
    last = max(float(u.get('last_ok') or 0), float(a.get('saved_at') or 0))
    return now_s - last >= UPKEEP_EVERY_S + _jitter(aid, UPKEEP_JITTER_S)


def _retry_after(headers):
    """Seconds a Retry-After header asks for (the seconds form), at most RETRY_AFTER_CAP_S (a nonsense value must not
    stop renewals for good), or None."""
    v = next((v for k, v in (headers or {}).items() if str(k).lower() == 'retry-after'), None)
    try:
        s = float(str(v).strip()) if v is not None else None
    except ValueError:
        return None
    if s is None or s != s:
        return None
    return max(0.0, min(s, RETRY_AFTER_CAP_S))


def _note_upkeep(ctx, aid, res):
    try:
        with vault.account_lock(ctx):
            doc = vault.load(ctx)
            if aid not in doc['accounts']:
                return
            u = doc['upkeep'].setdefault(aid, {})
            now = ctx.wall()
            u['last_try'] = now
            u['last_outcome'] = res.outcome
            if res.outcome in ('refreshed', 'adopted'):
                u['last_ok'], u['fails'] = now, 0
                u.pop('next_at', None)
            elif res.outcome in ('transient', 'unanswered', 'stashed', 'unpersisted', 'systemic'):
                fails = min(int(u.get('fails') or 0) + 1, 30)
                u['fails'] = fails
                u['next_at'] = now + max(min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2 ** min(fails - 1, 10))),
                                         res.retry_after_s or 0.0)
            elif res.outcome == 'skipped' and res.why in HELD_ELSEWHERE:
                u['next_at'] = now + SKIP_WAIT_S        # Claude Code renews it while it is live; not every pass
            vault.save(ctx, doc)
    except (vault.LockBusy, vault.VaultError, OSError):
        pass


def upkeep_pass(ctx, budget_s=60.0) -> List[str]:
    """Refresh idle saved logins that are due, within budget_s. One account's trouble never stops the others."""
    end = ctx.mono() + budget_s
    notes = []
    woke = WAKE.observe(ctx)
    try:
        from . import runas
        runas.reap_stale_claims(ctx)
        doc = vault.load(ctx)
    except vault.VaultError as e:
        return [str(e)]
    except Exception as e:  # noqa: BLE001 - upkeep never raises
        ctx.log('Upkeep could not start: %s' % e.__class__.__name__)
        return notes
    now = ctx.wall()
    todo = [aid for aid in doc['order'] if due(doc, aid, now)]
    if not todo:
        return notes
    if _sleeping_soon(ctx):     # every idle renewal can wait: the login it renews stays good, and it is due again
        ctx.log('Renewals wait: the lid is shut on battery, so the Mac may sleep before an answer comes')
        return notes
    if woke and not WAKE.settled(ctx, ctx.http.resolve(facts.TOKEN_HOST, min(5.0, max(0.0, end - ctx.mono())))):
        return notes
    for aid in todo:
        left = end - ctx.mono()
        if left < POST_MIN_LEFT_S + 5 or STOPPING.is_set():
            break
        who = doc['accounts'].get(aid, {}).get('email') or aid
        try:
            res = refresh(ctx, aid, budget_s=left, reason='upkeep')
        except Exception as e:  # noqa: BLE001 - isolate each account
            ctx.log('Upkeep of %s stopped: %s' % (who, e.__class__.__name__))
            res = Result('transient', aid, 'error')
        _note_upkeep(ctx, aid, res)
        if res.outcome == 'refreshed':
            notes.append('Renewed the saved login of %s' % who)
        elif res.outcome == 'permanent':
            notes.append('%s needs sign-in: Anthropic refused its saved login' % who)
        elif res.outcome == 'unanswered':
            notes.append('The renewal of the saved login of %s is still waiting for Anthropic\'s answer; the new login '
                         'is saved when it comes' % who)
        elif res.outcome == 'stashed' and res.why == CONFLICT:
            notes.append('The saved login of %s belongs to another account, Anthropic says; it was set aside, and '
                         'that account is not switched to until it is signed in again' % who)
        elif res.outcome in ('stashed', 'unpersisted'):
            notes.append('The saved login of %s was renewed but not yet saved; Switchboard keeps the new one and '
                         'will save it' % who)
    return notes
