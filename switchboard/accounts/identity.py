"""Fingerprints and ownership proof: whose login is this, and do the label and the login agree.

Evidence, strongest first:
1. 'fingerprint': the refresh token's fingerprint equals a saved account's current (or earlier) one;
2. 'refresh_expiry': its refresh-expiry stamp is one saved account's sign-in's (one sign-in, renewed since): an exact
   match with exactly one account. A renewal keeps the stamp or lowers it a little (facts.SAME_SIGN_IN_MS), never
   raises it, but a first sign-in of another account made a minute before can sit that close too, so a stamp a little
   lower than an account's is proof only where nothing is written on it (offline(near=True)), or, in prove(), when
   Anthropic can't be asked and it fits exactly one account, the one the config's label names;
3. 'token_account': the account a token answer named (a refresh we made);
4. 'profile': Anthropic's profile endpoint, asked with the access token outside every lock, 5 s, advisory: a resolved
   answer that disagrees blocks, an unresolved one never does, a 401 means "expired", not "someone else";
5. 'label': only the email/account in ~/.claude.json, which other programs rewrite (Claude Desktop's Code tab).
"Label and login disagree" is a state to show, never something resolved quietly.
"""
import json
import threading
from dataclasses import dataclass
from typing import Optional

from . import facts, live as live_mod
from .net import NetError

STRONG = ('fingerprint', 'refresh_expiry', 'token_account', 'profile')
_PROFILE_CACHE = {}                 # access fingerprint -> ProfileAnswer (resolved only)
_PROFILE_LOCK = threading.Lock()
PROFILE_CACHE_MAX = 64


@dataclass(frozen=True)
class ProfileAnswer:
    """status: 'resolved', 'unresolved' or 'expired' (401: the token lapsed; says nothing about the owner)."""
    status: str
    uuid: Optional[str] = None
    email: Optional[str] = None
    org: Optional[str] = None
    why: Optional[str] = None


@dataclass(frozen=True)
class Proof:
    """Whose login it is. account_id: a saved account's id when one matches, else None (new_uuid/new_org then name an
    owner the server proved that is not saved yet). label_id: the saved account the config's label names."""
    account_id: Optional[str]
    kind: str
    label_id: Optional[str] = None
    disagree: bool = False
    new_uuid: Optional[str] = None
    new_org: Optional[str] = None
    new_email: Optional[str] = None
    note: str = ''

    @property
    def strong(self):
        return self.kind in STRONG


def renewal_of(rx, saved_rx):
    """Can a login with refresh-expiry stamp rx be the same or a later generation of a sign-in saved with saved_rx?
    Claude Code keeps or lowers the stamp at a renewal and never raises it, so a later stamp is another sign-in (one
    made in the same sitting a minute later, say)."""
    return facts.same_sign_in(rx, saved_rx) and rx <= saved_rx


def offline(login, doc, block=None, near=False):
    """Proof from the saved accounts alone (no network, no Keychain): a fingerprint, or an exact stamp of exactly one
    account. near: also a stamp a little lower than an account's (renewal_of), for callers that only decide what not
    to spend, never what to write into a slot. block: the config's identity block, when the caller has it: with near,
    it picks one of several accounts whose sign-ins the stamp fits (accounts signed in within minutes of each other),
    and a label naming another account makes a stamp that is not exact no proof."""
    if login is None or login.kind != 'oauth':
        return Proof(None, 'none')
    accounts = doc['accounts']
    if login.fingerprint:
        for aid, a in accounts.items():
            if a.get('fp') == login.fingerprint or login.fingerprint in (a.get('previous_fps') or []):
                return Proof(aid, 'fingerprint')
    rx = login.refresh_expires_ms
    if rx:
        hits = [aid for aid, a in accounts.items() if a.get('rx_ms') == rx]
        if len(hits) == 1:
            return Proof(hits[0], 'refresh_expiry')
        if not near:
            return Proof(None, 'none')
        close = _renewal_of_any(rx, accounts)
        named = live_mod.label(block)[0]
        lid = label_id(doc, block) if named else None
        if len(close) == 1 and (not named or lid == close[0]):
            return Proof(close[0], 'refresh_expiry')
        if len(close) > 1 and lid in close:
            return Proof(lid, 'refresh_expiry')
    return Proof(None, 'none')


def _renewal_of_any(rx, accounts):
    """The saved accounts whose sign-in a login with stamp rx can be a renewal of."""
    return [aid for aid, a in accounts.items() if renewal_of(rx, a.get('rx_ms'))]


def label_id(doc, block):
    """The saved account the config's identity block names, or None."""
    from .vault import account_key
    uuid, email, org = live_mod.label(block)
    if not uuid:
        return None
    aid = account_key(doc, uuid, org)
    return aid if aid in doc['accounts'] else None


def agrees(a, uuid, org):
    """Does a saved account record (or label) match (uuid, org)? An organization only disagrees when both sides name
    one (a matching account id never excuses a different organization)."""
    if not uuid or a.get('uuid') != uuid:
        return False
    return not (org and a.get('org_id') and a.get('org_id') != org)


def _ask(ctx, access_token, deadline_s):
    """(status, body) from the profile endpoint, the whole call (name lookup included) within deadline_s: the name is
    resolved in a worker with its own deadline, then the request runs in a worker the caller never waits past (a
    stalled connection is left to end by itself). Raises NetError."""
    end = ctx.mono() + deadline_s
    if not ctx.http.resolve(facts.API_HOST, deadline_s):
        raise NetError('dns')
    box = {}

    def run():
        try:
            box['answer'] = ctx.http.request('GET', facts.PROFILE_URL, headers={
                'Authorization': 'Bearer ' + access_token, 'Content-Type': 'application/json',
                'User-Agent': ctx.user_agent}, body=None, timeout_s=max(0.5, end - ctx.mono()))
        except BaseException as e:  # noqa: BLE001 - handed to the caller
            box['error'] = e
    worker = threading.Thread(target=run, name='profile', daemon=True)
    worker.start()
    worker.join(max(0.0, end - ctx.mono()))
    if worker.is_alive():
        raise NetError('timeout', True, 'no answer within %.0f s' % deadline_s)
    if 'error' in box:
        raise box['error']
    status, _, body = box['answer']
    return status, body


def lookup_profile(ctx, access_token, deadline_s=5.0):
    """Ask Anthropic whose access token this is, within deadline_s in all. Never call it while holding a lock."""
    if not access_token:
        return ProfileAnswer('unresolved', why='no_access_token')
    key = live_mod.fingerprint(access_token)
    with _PROFILE_LOCK:
        hit = _PROFILE_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        status, body = _ask(ctx, access_token, deadline_s)
    except NetError as e:
        return ProfileAnswer('unresolved', why=e.kind)
    except Exception as e:  # noqa: BLE001 - an advisory answer never breaks its caller
        return ProfileAnswer('unresolved', why=e.__class__.__name__)
    if status == 401:
        return ProfileAnswer('expired', why='http-401')
    if status != 200:
        return ProfileAnswer('unresolved', why='http-%d' % status)
    try:
        obj = json.loads(body.decode('utf-8'))
        acct = obj.get('account') if isinstance(obj, dict) else None
        uuid = acct.get('uuid') if isinstance(acct, dict) else None
        if not isinstance(uuid, str) or not uuid:
            return ProfileAnswer('unresolved', why='bad-response')
        email = acct.get('email')
        org = obj.get('organization')
        org = org.get('uuid') if isinstance(org, dict) else None
        ans = ProfileAnswer('resolved', uuid=uuid, email=email if isinstance(email, str) else None,
                            org=org if isinstance(org, str) and org else None)
    except (ValueError, UnicodeDecodeError, AttributeError):
        return ProfileAnswer('unresolved', why='bad-response')
    with _PROFILE_LOCK:
        if len(_PROFILE_CACHE) >= PROFILE_CACHE_MAX:
            _PROFILE_CACHE.clear()
        _PROFILE_CACHE[key] = ans
    return ans


def cached_profile(login):
    """A profile answer already known for this login's access token (no network)."""
    if login is None or not login.access_token:
        return None
    with _PROFILE_LOCK:
        return _PROFILE_CACHE.get(live_mod.fingerprint(login.access_token))


def from_profile(doc, ans, block):
    """Proof from a resolved profile answer (owner saved or not)."""
    from .vault import account_key
    lid = label_id(doc, block)
    uuid, _, org = live_mod.label(block)
    aid = account_key(doc, ans.uuid, ans.org or '')
    owner = aid if aid in doc['accounts'] else None
    disagree = bool(uuid) and not (uuid == ans.uuid and not (org and ans.org and org != ans.org))
    if owner is None:
        return Proof(None, 'profile', label_id=lid, disagree=disagree, new_uuid=ans.uuid, new_org=ans.org,
                     new_email=ans.email)
    return Proof(owner, 'profile', label_id=lid, disagree=disagree)


def prove(ctx, login, doc, block, network=True):
    """The best proof available. network: may ask the profile endpoint (callers hold no lock)."""
    lid = label_id(doc, block)
    uuid, _, org = live_mod.label(block)
    p = offline(login, doc, block)
    if p.account_id:
        owner = doc['accounts'][p.account_id]
        disagree = bool(uuid) and not agrees(owner, uuid, org)
        return Proof(p.account_id, p.kind, label_id=lid, disagree=disagree)
    if login is None or login.kind != 'oauth':
        return Proof(None, 'none', label_id=lid)
    ans = cached_profile(login)
    if ans is None and network and login.has_access and not login.expired(ctx.wall()):
        ans = lookup_profile(ctx, login.access_token)
    if ans is not None and ans.status == 'resolved':
        return from_profile(doc, ans, block)
    # Anthropic can't say: a stamp a little lower than exactly one account's, the one the label names, is that
    # account's renewal (so a switch away still saves it while the network is down); several such accounts prove none
    if lid is not None and _renewal_of_any(login.refresh_expires_ms, doc['accounts']) == [lid]:
        return Proof(lid, 'refresh_expiry', label_id=lid, disagree=not agrees(doc['accounts'][lid], uuid, org))
    if uuid:
        return Proof(lid, 'label', label_id=lid, note=(ans.why if ans else 'not asked') or '')
    return Proof(None, 'none', label_id=lid, note=(ans.why if ans else '') or '')


def reprove(login, doc, block, before, before_login):
    """Proof for bytes that moved after `before` was established, without the network (under locks): the same
    sign-in (a refresh-expiry stamp equal to the earlier bytes', or a little lower: renewal_of) keeps the owner that
    was proved for the earlier bytes."""
    p = offline(login, doc, block)
    lid = label_id(doc, block)
    uuid, _, org = live_mod.label(block)
    if p.account_id:
        return Proof(p.account_id, p.kind, label_id=lid,
                     disagree=bool(uuid) and not agrees(doc['accounts'][p.account_id], uuid, org))
    if login is not None and before_login is not None and \
            renewal_of(login.refresh_expires_ms, before_login.refresh_expires_ms) and before.strong:
        return Proof(before.account_id, 'refresh_expiry', label_id=lid, disagree=before.disagree,
                     new_uuid=before.new_uuid, new_org=before.new_org, new_email=before.new_email)
    if uuid:
        return Proof(lid, 'label', label_id=lid)
    return Proof(None, 'none', label_id=lid)
