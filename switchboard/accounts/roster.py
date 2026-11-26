"""The account list as people use it: find, keep the live login, remove, name, order; the live login's owner; health.

api.py's account functions are one line each into here and the other modules, so the contract file stays small.
"""
import os
import re
import threading
import time

from . import capture, ccfiles, facts, identity, live as live_mod, switcher, vault
from .api import ABSENT, PRESENT, UNREADABLE, AccountsError, Live, Read

LIVE_TTL_S = 180.0
_LIVE = {}
_LIVE_LOCK = threading.Lock()
_ALIAS = re.compile(r'[a-z0-9._-]+')


def _load(ctx):
    try:
        return vault.load(ctx)
    except vault.VaultError as e:
        raise AccountsError('other', str(e))


def _refuse_inside_run(ctx):
    if capture.in_runas_env(ctx):
        raise AccountsError('other', 'This shell is inside a `switchboard run` session; change accounts from a normal '
                                     'terminal or the page.')


def accounts(ctx):
    _load(ctx)
    return vault.list_accounts(ctx)


def find_id(ctx, target, doc=None):
    """An account id by its 1-based number, id, alias or email. AccountsError not_found or ambiguous."""
    doc = _load(ctx) if doc is None else doc
    t = (target or '').strip() if isinstance(target, str) else str(target or '')
    if not t:
        raise AccountsError('not_found', 'Name an account: its number, email, short name or id.')
    order = [aid for aid in doc['order'] if aid in doc['accounts']]
    if t.isdigit():
        n = int(t)
        if 1 <= n <= len(order):
            return order[n - 1]
        raise AccountsError('not_found', 'There is no account %s; Switchboard has %d.' % (t, len(order)))
    if t in doc['accounts']:
        return t
    low = t.lower()
    hits = [aid for aid in order if (doc['accounts'][aid].get('alias') or '').lower() == low]
    if hits:
        return hits[0]
    hits = [aid for aid in order if (doc['accounts'][aid].get('email') or '').lower() == low]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:                   # one email, two organizations: say how to name one (names.py)
        from .. import names
        raise AccountsError('ambiguous', names.ambiguous(low, [(order.index(aid) + 1, names.org_words(
            doc['accounts'][aid].get('org_name'))) for aid in hits]))
    from .. import names                # the name every surface shows: "maya@northwind.example (Northwind)"
    if names.is_label(t):
        hits = [aid for aid in order if names.in_doc(doc, aid).lower() == low]
        if len(hits) == 1:
            return hits[0]
    raise AccountsError('not_found', 'No saved account matches "%s".' % t)


def find(ctx, target):
    doc = _load(ctx)
    return vault.to_account(ctx, doc, find_id(ctx, target, doc))


# ---------- the live login, for the page (cheap, cached by change signal) ----------

def live_view(ctx):
    sig = capture.signal(ctx)
    with _LIVE_LOCK:
        hit = _LIVE.get(ctx.data_dir)
    if hit is not None and hit[0] == sig and ctx.mono() - hit[1] < LIVE_TTL_S:
        return hit[2]
    lr = live_mod.read_live(ctx, fresh=False)
    result = _live_of(ctx, lr)
    if lr.read.kind != UNREADABLE:
        with _LIVE_LOCK:
            _LIVE[ctx.data_dir] = (sig, ctx.mono(), result)
    return result


def forget_live(ctx=None):
    with _LIVE_LOCK:
        if ctx is None:
            _LIVE.clear()
        else:
            _LIVE.pop(ctx.data_dir, None)


def _live_of(ctx, lr):
    r = lr.read
    if r.kind == UNREADABLE:
        return Live(state=UNREADABLE, account_id=None, email=None, proof='none', why=r.why)
    if r.kind == ABSENT:
        return Live(state=ABSENT, account_id=None, email=None, proof='none', why='Claude Code is not signed in')
    try:
        doc = vault.load(ctx)
    except vault.VaultError:
        doc = vault.blank()
    _, block, _ = live_mod.config_identity(ctx, cached=True)
    uuid, email, org = live_mod.label(block)
    lid = identity.label_id(doc, block)
    login = lr.login
    from . import bearer
    held = bearer.owns(doc, login)
    if held is not None:                # a kept one-year token: no sign-in is in use, and it is never sent anywhere
        return Live(state='other', account_id=None, email=None, proof='none', degraded=r.degraded, bearer=held,
                    why='Claude Code is on a one-year token, which auto-switch leaves as it is')
    # no record owns it: it is no account's, and no usage is read with it. An emptied sign-in stays that, whatever
    # its siblings hold (an MCP login or a plugin's own key is no login of the account's).
    if login.kind != 'wiped' and live_mod.unrenewable(login):
        return Live(state='other', account_id=None, email=None, proof='none', degraded=r.degraded,
                    why="Claude Code is on a login with no refresh token, which is no saved account's")
    if login is None or login.kind != 'oauth':
        state = 'wiped' if login is not None and login.kind == 'wiped' else 'other'
        why = ("Claude Code's login was emptied (Anthropic refused it); sign in again in Claude Code"
               if state == 'wiped' else 'Claude Code is not using an account login')
        return Live(state=state, account_id=lid or (vault.account_key(doc, uuid, org) if uuid else None),
                    email=email or None, proof='label' if uuid else 'none', degraded=r.degraded, why=why)
    p = identity.prove(ctx, login, doc, block, network=False)
    if p.account_id:
        aid, who = p.account_id, doc['accounts'][p.account_id].get('email') or email
    elif p.new_uuid:
        aid, who = vault.account_key(doc, p.new_uuid, p.new_org or ''), p.new_email
    else:
        aid, who = (lid or (vault.account_key(doc, uuid, org) if uuid else None)), email or None
    why = None
    if p.disagree:
        why = ("Claude Code's settings name %s, but its login is %s's: label and login disagree"
               % (email or 'another account', who or 'another account'))
    elif not p.strong:
        why = 'Not proved yet whose login this is (only its label says)' if uuid else 'Not known whose login this is'
    return Live(state=PRESENT, account_id=aid, email=who or None, proof=p.kind, disagree=p.disagree,
                degraded=r.degraded, why=why)


# ---------- an access token for reading usage (api.access_token) ----------

FORCED_EVERY_S = 30 * 60.0      # a refresh asked for by a refused read (401), at most this often per account
_FORCED = {}
_FORCED_LOCK = threading.Lock()


def access_token(ctx, aid, fresh=False):
    """An access token to read aid's usage with; the caller never stores or logs it.

    The active account's is Claude Code's own: never refreshed here, and unreadable 'expired' once it has lapsed
    (Claude Code renews it when it is next used). When the login proves its own account, a config label that names
    another account changes nothing: the numbers are the login's account's, and that account goes on being read. Only
    an account first kept on its label alone, and not checked with Anthropic yet, is refused while the label names
    another (its numbers could be the other account's). Another account's comes from the vault, refreshed through
    tokens.py's transaction
    when it has lapsed, or when fresh (the endpoint just refused it) at most once per FORCED_EVERY_S, so an endpoint
    that keeps refusing can't make us spend a refresh token on every read. An account marked 'conflict' (Anthropic said
    its saved login is another account's) gets none: its numbers would be the other account's."""
    from . import tokens
    try:
        rec = vault.load(ctx)['accounts'].get(aid) or {}
    except vault.VaultError:
        rec = {}                                    # (the list can't be read: the live login's numbers still can)
    if rec.get('conflict'):
        return Read(UNREADABLE, why='conflict')
    lr = live_mod.read_live(ctx, fresh=fresh)
    if lr.read.kind != UNREADABLE:
        lv = _live_of(ctx, lr)
        if lv.state == PRESENT and lv.account_id == aid and not live_mod.unrenewable(lr.login):
            if lv.disagree and (rec.get('proof') or {}).get('kind') == 'label':
                return Read(UNREADABLE, why='disagree')
            return _token_of(lr.login, ctx.wall(), lr.read.provenance, lr.read.degraded)
    doc = _load(ctx)
    if aid not in doc['accounts']:
        return Read(UNREADABLE, why='not_found')
    cur = vault.read_current(ctx, aid, fresh=fresh)
    if cur.kind != PRESENT:
        return Read(UNREADABLE, why=cur.why or 'no_saved_login')
    login = live_mod.parse(cur.value)
    if login.kind != 'oauth':
        return Read(UNREADABLE, why='no_token')
    lapsed = not login.has_access or login.expired(ctx.wall())
    forced = bool(fresh) and not lapsed and _may_force(ctx, aid)
    # a renewal that failed rests as long as the upkeep pass would rest (a 429's Retry-After included), and its
    # outcome is noted the same way, so the reader's every-5-minutes asks don't keep posting to a failing endpoint
    resting = ctx.wall() < float((doc['upkeep'].get(aid) or {}).get('next_at') or 0)
    if (lapsed or forced) and login.has_refresh and not resting:
        res = tokens.refresh(ctx, aid, budget_s=45.0, reason='usage', when_due=not forced)
        tokens._note_upkeep(ctx, aid, res)
        if res.outcome in ('refreshed', 'adopted'):
            if res.text:
                login = live_mod.parse(res.text)
            else:
                again = vault.read_current(ctx, aid, fresh=True)
                if again.kind == PRESENT:
                    login = live_mod.parse(again.value)
    return _token_of(login, ctx.wall(), 'keychain', False)


def _may_force(ctx, aid):
    key = (ctx.data_dir, aid)
    with _FORCED_LOCK:
        last = _FORCED.get(key)
        if last is not None and ctx.mono() - last < FORCED_EVERY_S:
            return False
        _FORCED[key] = ctx.mono()
        return True


def _token_of(login, now, provenance, degraded):
    if login is None or login.kind != 'oauth' or not login.access_token:
        return Read(UNREADABLE, why='no_token')
    if login.expired(now):
        return Read(UNREADABLE, why='expired')
    return Read(PRESENT, value=login.access_token, provenance=provenance, degraded=degraded)


# ---------- changes to the list ----------

def add_current(ctx):
    """Keep the live login as an account, or refresh the saved copy of the account it is."""
    _refuse_inside_run(ctx)
    lr = live_mod.read_live(ctx, fresh=True, interactive=True)
    if lr.read.kind == UNREADABLE or lr.read.degraded:
        raise switcher.unreadable_error(lr.read)
    login = lr.login
    if lr.read.kind == ABSENT or login is None or login.kind == 'empty':
        raise AccountsError('not_found', "Claude Code isn't signed in. Sign in with /login in Claude Code first.")
    if login.kind == 'wiped':
        raise AccountsError('dead_login', "Claude Code's login was emptied after Anthropic refused it. Sign in again "
                                          "with /login in Claude Code, then add it.")
    if not login.full_pair:
        raise AccountsError('other', "Claude Code isn't using a complete account login (an API key or a token "
                                     "login), so there is nothing to keep.")
    cfg_r, block, cfg_path = live_mod.config_identity(ctx)
    if cfg_r.kind == UNREADABLE:
        raise AccountsError('other', "Claude Code's settings file (%s) can't be read; nothing was changed." % cfg_path)
    uuid, email, org = live_mod.label(block)
    doc = _load(ctx)
    p = identity.offline(login, doc, block)
    ans = None
    if not p.account_id and login.has_access and not login.expired(ctx.wall()):
        ans = identity.lookup_profile(ctx, login.access_token)
    note = None
    if p.account_id:
        owner, kind = p.account_id, p.kind
    elif ans is not None and ans.status == 'resolved':
        if uuid and (ans.uuid != uuid or (org and ans.org and org != ans.org)):
            raise AccountsError('disagree', "The login in Claude Code belongs to %s, but Claude Code's settings name "
                                            "%s. Nothing was changed; sign in again in Claude Code."
                                % (ans.email or 'another account', email or 'another account'))
        owner, kind = vault.account_key(doc, ans.uuid, ans.org or ''), 'profile'
    else:
        if not uuid:
            raise AccountsError('not_found', "Claude Code's settings name no account; sign in with /login first.")
        owner, kind = vault.account_key(doc, uuid, org), 'label'
        if owner in doc['accounts']:
            # the label names an account kept with another login, and nothing proves this one is it: writing it there
            # on the label alone is how one account's login ends up in another's slot
            raise AccountsError('network', "Switchboard couldn't confirm with Anthropic whose login this is (%s), and "
                                           "Claude Code's settings name %s, which is kept with another login. Nothing "
                                           "was changed; try again in a minute."
                                % ((ans.why if ans is not None else 'its token has expired') or 'no answer', email))
        note = 'could not be checked with Anthropic just now'
    try:
        with vault.switch_lock(ctx, timeout=30.0), vault.account_lock(ctx, timeout=10.0):
            doc = vault.load(ctx)
            cur = live_mod.read_live(ctx, fresh=True, interactive=True)
            r2, block2, _ = live_mod.config_identity(ctx)
            if not cur.usable or cur.login is None or cur.login.text != login.text or \
                    live_mod.label(block2) != live_mod.label(block):
                raise AccountsError('other', "Claude Code's login changed while it was being checked; nothing was "
                                             "changed. Try again.")
            now = ctx.wall()
            if owner in doc['accounts']:
                a = doc['accounts'][owner]
                vr = vault.read_current(ctx, owner, fresh=True, interactive=True)
                if vr.kind == UNREADABLE:
                    raise switcher.unreadable_error(vr, "%s's saved login" % (a.get('email') or owner))
                stored = live_mod.parse(vr.value) if vr.kind == PRESENT else None
                refused = (a.get('strike') or {}).get('fp') == a.get('fp')
                if stored is None or not stored.full_pair or refused or \
                        live_mod.compare(login, stored) in ('newer', None) or \
                        (live_mod.compare(login, stored) == 'same' and not vault.same_saved(stored.text, login.text)):
                    done = vault.save_login(ctx, owner, login.text, old=vr)
                    if not done.ok:
                        raise switcher.unreadable_error(Read(UNREADABLE, why=done.why), 'the login', verb='write')
                    vault.note_login(a, login, kind, now)
                if block and identity.agrees(a, uuid, org):        # never another account's label
                    a['identity'] = dict(block)
                    a['email'] = email or a.get('email')
            else:
                done = vault.save_login(ctx, owner, login.text, old=Read(ABSENT))
                if not done.ok:
                    raise switcher.unreadable_error(Read(UNREADABLE, why=done.why), 'the login', verb='write')
                if kind == 'profile' and not block:
                    block = {'accountUuid': ans.uuid, 'emailAddress': ans.email or '', 'organizationUuid': ans.org}
                vault.new_account(doc, owner, block, login, kind, now)
            doc['removed'].pop(owner, None)
            vault.save(ctx, doc)
            acc = vault.to_account(ctx, doc, owner)
    except vault.LockBusy:
        raise AccountsError('other', 'Switchboard is busy; try again in a moment. Nothing was changed.')
    forget_live(ctx)
    ctx.say('Added %s' % acc.email, 'info', h='signin', account=acc.email, by='you', reason=note)
    return acc


# ---------- a login Claude Code's own sign-in left in a throwaway config folder (api.add_login) ----------

def _throwaway(ctx, config_dir):
    """The environment Claude Code ran with for that folder. Refused: anything that is not a folder of its own (Claude
    Code's own folder or item, a `switchboard run` folder), since its item is removed afterwards."""
    if not isinstance(config_dir, str) or not config_dir.strip() or not os.path.isabs(config_dir):
        raise AccountsError('other', 'That sign-in folder is not a full path, so nothing was changed.')
    if not os.path.isdir(config_dir):
        raise AccountsError('unreadable_live', 'The sign-in folder is gone, so there is no login to keep. Nothing was '
                                               'changed; press Add account to try again.')
    env_t = {'CLAUDE_CONFIG_DIR': config_dir}
    if ctx.env.get('USER'):
        env_t['USER'] = ctx.env['USER']
    own = "That is Claude Code's own folder, not a sign-in folder, so nothing was changed."
    if facts.keychain_service(env_t) in (facts.keychain_service(ctx.env), facts.keychain_service({})):
        raise AccountsError('other', own)
    for folder in (facts.config_home(ctx.env, ctx.home), facts.secure_dir(ctx.env, ctx.home),
                   os.path.join(ctx.home, '.claude'), ctx.home):
        try:
            if os.path.exists(folder) and os.path.samefile(config_dir, folder):
                raise AccountsError('other', own)
        except OSError:
            pass
    run_dir = os.path.realpath(ctx.path('run'))
    real = os.path.realpath(config_dir)
    if real == run_dir or real.startswith(run_dir + os.sep):
        raise AccountsError('other', 'That folder belongs to a `switchboard run`, so nothing was changed.')
    return env_t


def remove_sign_in(ctx, config_dir):
    """Remove the Keychain item Claude Code's own sign-in left for a throwaway config folder (api.remove_sign_in): named
    by Claude Code's rule for that folder, through the Keychain wrapper. Refuses what add_login refuses (Claude Code's
    own folder or item, a `switchboard run` folder). True when the item is gone or was never there."""
    env_t = _throwaway(ctx, config_dir)
    gone = ctx.keychain.delete(facts.keychain_service(env_t), facts.keychain_account(ctx.env))
    if not gone.ok:
        ctx.log("A sign-in folder's Keychain item could not be removed yet (%s)" % gone.why)
    return gone.ok


def _throwaway_login(ctx, env_t, service, account):
    """(Login, where) Claude Code left for that folder: its own Keychain item, else the folder's .credentials.json.
    A full OAuth pair, or AccountsError."""
    kc = ctx.keychain.read(service, account, fresh=True, interactive=True)
    text, where = None, None
    if kc.kind == PRESENT and (kc.value or '').strip():
        text, where = kc.value, 'keychain'
    else:
        fr, raw = ccfiles.read_bytes(facts.credentials_file(env_t, ctx.home))
        if fr.kind == PRESENT:
            try:
                got = raw.decode('utf-8')
            except UnicodeDecodeError:
                got = None
            if got and got.strip():
                text, where = got, 'file'               # a folder made for this one sign-in: it holds no older copy
    if text is None:
        if kc.kind == UNREADABLE:
            raise switcher.unreadable_error(kc, "the new sign-in's login")
        raise AccountsError('unreadable_live', 'The sign-in left no login to keep (Claude Code did not finish it). '
                                               'Nothing was changed; press Add account to try again.')
    login = live_mod.parse(text)
    if not login.full_pair:
        raise AccountsError('unreadable_live', 'The sign-in left an incomplete login, so there is nothing to keep. '
                                               'Nothing was changed; press Add account to try again.')
    return login, where


def _dead_live(ctx, doc, owner):
    """The live_mark of Claude Code's live login when it is owner's and can't work: gone, emptied after a refusal, or
    refused (its refresh token struck by Anthropic, or lapsed). None otherwise, and whenever that can't be told:
    add_login touches the live login only then. Caller holds the account lock (nothing here asks the network)."""
    lr = live_mod.read_live(ctx, fresh=True, interactive=True)
    if lr.read.kind == UNREADABLE or lr.read.degraded:
        return None
    cfg_r, block, _ = live_mod.config_identity(ctx)
    luuid, _, lorg = live_mod.label(block) if cfg_r.kind == PRESENT else ('', '', '')
    lid = vault.account_key(doc, luuid, lorg) if luuid else None      # kept or not: keyed as owner was
    login = lr.login
    if lr.read.kind == ABSENT or login is None or login.kind in ('empty', 'wiped'):
        return switcher.live_mark(login) if lid == owner else None
    if login.kind != 'oauth':
        return None                                     # an API key or a token login: not ours to replace
    p = identity.offline(login, doc)
    if p.account_id != owner and not (p.account_id is None and lid == owner):
        return None
    struck = (doc['accounts'].get(owner, {}).get('strike') or {}).get('fp')
    lapsed = login.refresh_expires_ms is not None and login.refresh_expires_ms <= ctx.wall() * 1000
    refused = bool(login.fingerprint) and login.fingerprint == struck
    return switcher.live_mark(login) if (refused or lapsed) else None


def _older_live(ctx, doc, owner, newer):
    """The live_mark of Claude Code's live login when it is owner's (proved from the saved accounts alone: its
    fingerprint or its sign-in's stamp), a full login that still works, and an earlier sign-in than `newer` (an earlier
    refreshTokenExpiresAt). None otherwise, and whenever that can't be told: an API key, a token login (no refresh
    token) or another account's login is never replaced. Judged before the new login is saved (the saved stamp still
    names the older sign-in then). Caller holds the account lock (nothing here asks the network)."""
    lr = live_mod.read_live(ctx, fresh=True, interactive=True)
    if lr.read.kind != PRESENT or lr.read.degraded:
        return None
    login = lr.login
    if login is None or not login.full_pair or identity.offline(login, doc).account_id != owner:
        return None
    return switcher.live_mark(login) if live_mod.older_sign_in(login, newer.refresh_expires_ms) else None


def add_login(ctx, config_dir):
    """Keep the login Claude Code's own sign-in left in a throwaway config folder (the page's Add). Returns the Account.

    1. The folder's login: the Keychain item Claude Code uses with CLAUDE_CONFIG_DIR=config_dir, else the folder's
       .credentials.json. A full OAuth pair only.
    2. Whose it is: Anthropic's profile answer for its access token, asked with no lock held; with no answer, its
       fingerprint when that is a kept account's, else the folder's label, but only for an account not kept yet
       (as add_current: a label naming an account kept with another login is not proof).
    3. Kept like add_current, under the switch and account locks: a new account, or a newer generation of a kept one
       (never older over newer; the replaced copy stays as an earlier generation), read back.
    4. The throwaway item is removed once the kept copy is safe.
    5. The live login is touched only when it is this account's and it is gone, emptied or refused, or a full login
       of an earlier sign-in of this account than the one just saved: then the new login is made live through the
       switch transaction (the live login's mark, taken here under the locks, is checked again before the write).
       Anything else about Claude Code is left as it is.
    The caller says what was added (connect.py words it with the account that was asked for)."""
    _refuse_inside_run(ctx)
    env_t = _throwaway(ctx, config_dir)
    service, account = facts.keychain_service(env_t), facts.keychain_account(ctx.env)
    login, where = _throwaway_login(ctx, env_t, service, account)
    _, _, cfg = ccfiles.read_config(env_t, ctx.home)
    block = ccfiles.identity_of(cfg)
    uuid, email, org = live_mod.label(block)
    doc = _load(ctx)
    p = identity.offline(login, doc)
    ans = None
    if login.has_access and not login.expired(ctx.wall()):
        ans = identity.lookup_profile(ctx, login.access_token)
    if ans is not None and ans.status == 'resolved':
        if uuid and (ans.uuid != uuid or (org and ans.org and org != ans.org)):
            raise AccountsError('disagree', "The new sign-in's login belongs to %s, but its settings name %s. Nothing "
                                            "was changed; press Add account to try again."
                                % (ans.email or 'another account', email or 'another account'))
        owner, kind = vault.account_key(doc, ans.uuid, ans.org or ''), 'profile'
        if p.account_id and p.account_id != owner:
            raise AccountsError('disagree', "Anthropic says the new sign-in is %s, but Switchboard keeps that login "
                                            "as another account. Nothing was changed."
                                % (ans.email or 'another account'))
        if not block:
            block = {'accountUuid': ans.uuid, 'emailAddress': ans.email or '', 'organizationUuid': ans.org}
            uuid, email, org = live_mod.label(block)
    elif p.account_id:
        owner, kind = p.account_id, p.kind
    else:
        if not uuid:
            raise AccountsError('network', "Switchboard couldn't confirm with Anthropic whose the new sign-in is (%s), "
                                           "and it names no account. Nothing was changed; try again in a minute."
                                % ((ans.why if ans is not None else 'its token has expired') or 'no answer'))
        owner, kind = vault.account_key(doc, uuid, org), 'label'
        if owner in doc['accounts']:
            raise AccountsError('network', "Switchboard couldn't confirm with Anthropic whose the new sign-in is (%s), "
                                           "and it names %s, which is kept with another login. Nothing was changed; "
                                           "try again in a minute."
                                % ((ans.why if ans is not None else 'its token has expired') or 'no answer', email))
    try:
        with vault.switch_lock(ctx, timeout=30.0), vault.account_lock(ctx, timeout=10.0):
            doc = vault.load(ctx)
            dead = _dead_live(ctx, doc, owner)         # judged before the new login lifts the old one's strike
            older = None if dead is not None else _older_live(ctx, doc, owner, login)
            now = ctx.wall()
            if owner in doc['accounts']:
                a = doc['accounts'][owner]
                vr = vault.read_current(ctx, owner, fresh=True, interactive=True)
                if vr.kind == UNREADABLE:
                    raise switcher.unreadable_error(vr, "%s's saved login" % (a.get('email') or owner))
                stored = live_mod.parse(vr.value) if vr.kind == PRESENT else None
                refused = stored is not None and (a.get('strike') or {}).get('fp') == stored.fingerprint
                verdict = live_mod.compare(login, stored)
                if stored is None or not stored.full_pair or refused or verdict in ('newer', None):
                    done = vault.save_login(ctx, owner, login.text, old=vr)
                    if not done.ok:
                        raise switcher.unreadable_error(Read(UNREADABLE, why=done.why), 'the new login', verb='write')
                    vault.note_login(a, login, kind, now)
                    kept = 'saved'
                else:
                    kept = verdict                      # 'same' or 'older': the kept copy is as new or newer
                if block and identity.agrees(a, uuid, org):
                    a['identity'] = dict(block)
                    a['email'] = email or a.get('email')
            else:
                done = vault.save_login(ctx, owner, login.text, old=Read(ABSENT))
                if not done.ok:
                    raise switcher.unreadable_error(Read(UNREADABLE, why=done.why), 'the new login', verb='write')
                vault.new_account(doc, owner, block, login, kind, now)
                kept = 'added'
            doc['removed'].pop(owner, None)
            vault.save(ctx, doc)
            who = doc['accounts'][owner].get('email') or owner
    except vault.LockBusy:
        raise AccountsError('other', 'Switchboard is busy; try again in a moment. Nothing was changed.')
    except vault.VaultError as e:
        raise AccountsError('other', str(e))
    ctx.log('Kept the new sign-in of %s (%s, %s, from its %s)' % (who, login.fingerprint, kept, where))
    gone = ctx.keychain.delete(service, account)        # its kept copy read back (or a newer one is kept)
    if not gone.ok:
        ctx.log('The sign-in folder\'s Keychain item could not be removed yet (%s); it is removed with the folder'
                % gone.why)
    forget_live(ctx)
    if kept != 'saved':
        older = None                                    # the folder's login was not the one kept: nothing newer to put
    if dead is not None or older is not None:
        why = 'no working login' if dead is not None else 'an older sign-in of it'
        try:
            res = switcher.switch(ctx, owner, kind='manual', source='page', interactive=True,
                                  replace_dead=dead if dead is not None else older,
                                  reason='Put the new sign-in of %s in use: Claude Code had %s' % (who, why))
            if not res.ok:
                ctx.say('Kept the new sign-in of %s; Claude Code is not on it yet: %s' % (who, res.note or res.reason),
                        'warn', h='problem', account=who)
        except AccountsError as e:
            ctx.log('Did not put the new sign-in of %s in use: %s' % (who, e))
            ctx.say('Kept the new sign-in of %s; Claude Code is not on it yet: %s' % (who, e), 'warn', h='problem',
                    account=who)
        forget_live(ctx)
    try:
        doc = vault.load(ctx)
    except vault.VaultError as e:
        raise AccountsError('other', str(e))
    if owner not in doc['accounts']:
        raise AccountsError('not_found', 'That account was removed meanwhile.')
    return vault.to_account(ctx, doc, owner)


def remove(ctx, target, even_in_use=False):
    """Forget a saved account (its vault items and metadata) and the tokens kept for it. The live login is never
    touched, so removing an account whose token Claude Code is on is refused (switch away first), except for the
    uninstaller's --all (even_in_use), after which nothing switches."""
    _refuse_inside_run(ctx)
    from . import runas
    aid = find_id(ctx, target)
    runas.reap_stale_claims(ctx)
    try:
        with vault.switch_lock(ctx, timeout=30.0), vault.account_lock(ctx, timeout=10.0):
            doc = vault.load(ctx)
            a = doc['accounts'].get(aid)
            if a is None:
                raise AccountsError('not_found', 'That account was already removed.')
            email = a.get('email') or aid
            if vault.claimed(ctx, doc, aid) or aid in doc['claims']:
                raise AccountsError('claimed', '%s is in use by a `switchboard run`; remove it once that ends.' % email)
            from . import bearer
            who = bearer.name_of(doc, {'id': aid, 'for': aid})      # how a token kept for it is named
            # Claude Code on a token kept for it: switch away first (bearer.py). Told under the switch lock, which
            # every switch takes, so no switch onto that token can come between this and the removal.
            if not even_in_use:
                bearer.refuse_in_use(ctx, doc, [b for b, r in bearer.records(doc).items() if r.get('for') == aid],
                                     'remove it', 'removed')
            # The list first: when it can't be written, nothing has changed. Then the login; when the Keychain
            # refuses, the account goes back on the list as it was. No login is read but Claude Code's, and that only
            # when a token is kept for it (the uninstaller's --all runs this with even_in_use: nothing is read).
            pos = doc['order'].index(aid) if aid in doc['order'] else None
            kept = dict((k, doc[k][aid]) for k in ('upkeep', 'refreshing') if aid in doc[k])
            doc['accounts'].pop(aid, None)
            doc['order'] = [x for x in doc['order'] if x != aid]
            for k in ('upkeep', 'refreshing', 'claims'):
                doc[k].pop(aid, None)
            doc['removed'][aid] = {'at': ctx.wall(), 'email': email}
            try:
                vault.save(ctx, doc)
            except (OSError, ccfiles.CCFileError) as e:
                raise AccountsError('disk' if isinstance(e, ccfiles.DiskFull) or getattr(e, 'errno', None) == 28
                                    else 'other',
                                    "Couldn't write the list of accounts (%s), so %s was not removed. Free some disk "
                                    "space and try again." % (e.__class__.__name__, email))
            done = ctx.keychain.delete(vault.SERVICE_CURRENT, aid)
            if not done.ok:
                doc['accounts'][aid] = a
                doc['order'].insert(len(doc['order']) if pos is None else min(pos, len(doc['order'])), aid)
                for k, v in kept.items():
                    doc[k][aid] = v
                doc['removed'].pop(aid, None)
                try:
                    vault.save(ctx, doc)
                except (OSError, ccfiles.CCFileError) as e:
                    raise AccountsError('other', "Couldn't remove %s's saved login from the Keychain (%s), and the "
                                                 "list of accounts could not be put back (%s): %s is no longer listed, "
                                                 "and its saved login stays in your Keychain until the uninstaller's "
                                                 "--all removes it." % (email, done.why, e.__class__.__name__, email))
                raise switcher.keychain_refusal(done.why, "Couldn't remove %s's saved login from the Keychain (%s); "
                                                          "nothing was removed." % (email, done.why))
            ctx.keychain.delete(vault.SERVICE_PREVIOUS, aid)
            _drop_recovery(ctx, doc, aid)
            tokens_said = bearer.forget_for(ctx, doc, aid, who)    # its tokens, under the same locks
    except vault.LockBusy:
        raise AccountsError('other', 'Switchboard is busy; try again in a moment. Nothing was removed.')
    try:
        from . import usage
        usage.forget_account(aid, path=ctx.path(usage.STORE))     # its readings and samples go with it
    except Exception as e:  # noqa: BLE001 - the account is removed either way; old numbers only take room
        ctx.log('The readings of a removed account stay for now: %s' % e.__class__.__name__)
    forget_live(ctx)
    ctx.say('Removed %s from Switchboard' % email, 'info', h='removed', account=email, by='you')
    for text, kind, facts_ in tokens_said:
        ctx.say(text, kind, **facts_)


def _drop_recovery(ctx, doc, aid):
    """A removed account's recovery copies go with it: its successor and the safety copies taken of it as the login in
    use, in the Keychain or in a file, with their manifest lines. Two stay: the safety copy a cut-off switch may still
    need to put Claude Code's login back, and a login set aside from this account's slot that is another kept
    account's only working one (Anthropic named that account as its owner)."""
    from . import recovery
    entries = vault.stashed(ctx)
    keep = recovery.needed(ctx, doc, entries)       # the one rule for what must stay (a journal that can't be read
    keys = set(e['key'] for e in entries if e.get('account_id') == aid and isinstance(e.get('key'), str))
    keys.add('successor:' + aid)                    # may name any safety copy)
    for key in sorted(keys - set(keep)):
        vault.drop_stash(ctx, key)


def _alias_word(alias):
    """alias as it is kept (trimmed, lower case), or None for none. AccountsError when it can't be one."""
    norm = None if alias is None or not str(alias).strip() else str(alias).strip().lower()
    if norm is not None:
        if not _ALIAS.fullmatch(norm) or norm.isdigit() or norm.startswith('-'):
            raise AccountsError('other', 'A short name is letters, digits, dots, dashes and underscores, not only '
                                         'digits, and does not start with a dash.')
        if norm in TAKEN_WORDS:
            raise AccountsError('other', TAKEN_WORDS[norm])
    return norm


# Short names a command would read as its own word: `switchboard folder off` clears the folder's account, so an
# account named "off" could not be set for a folder by that name.
TAKEN_WORDS = {'off': '"off" is a word of its own in switchboard folder off; choose another short name.'}


def _alias_free(doc, aid, norm):
    """AccountsError when norm is an account id, or the alias of an account other than aid (None: of any)."""
    if norm in doc['accounts']:
        raise AccountsError('other', '"%s" is an account id; choose another short name.' % norm)
    for other, a in doc['accounts'].items():
        if other != aid and (a.get('alias') or '').lower() == norm:
            raise AccountsError('other', '"%s" is already the short name of %s.' % (norm, a.get('email')))


def check_alias(ctx, alias, keep_for=None):
    """Would set_alias give an account this alias? Asked before a sign-in, so a refusal comes first; changes nothing.
    keep_for: the id of an account that may already have it. Returns the alias as kept, or None for none."""
    norm = _alias_word(alias)
    if norm is not None:
        _alias_free(_load(ctx), keep_for, norm)
    return norm


def set_alias(ctx, target, alias):
    _refuse_inside_run(ctx)
    aid = find_id(ctx, target)
    norm = _alias_word(alias)
    try:
        with vault.switch_lock(ctx, timeout=10.0), vault.changing(ctx) as doc:
            if aid not in doc['accounts']:
                raise AccountsError('not_found', 'That account was removed meanwhile.')
            if norm is not None:
                _alias_free(doc, aid, norm)
                doc['accounts'][aid]['alias'] = norm
            else:
                doc['accounts'][aid].pop('alias', None)
    except vault.LockBusy:
        raise AccountsError('other', 'Switchboard is busy; try again in a moment.')


def reorder(ctx, ids):
    _refuse_inside_run(ctx)
    try:
        with vault.switch_lock(ctx, timeout=10.0), vault.changing(ctx) as doc:
            new = []
            for aid in ids or []:
                if aid not in doc['accounts']:
                    raise AccountsError('not_found', 'No saved account has the id %s.' % aid)
                if aid not in new:
                    new.append(aid)
            doc['order'] = new + [aid for aid in doc['order'] if aid not in new and aid in doc['accounts']]
    except vault.LockBusy:
        raise AccountsError('other', 'Switchboard is busy; try again in a moment.')


# ---------- doctor ----------

def health(ctx):
    out = {'keychain': ctx.keychain.state() if hasattr(ctx.keychain, 'state') else {},
           'claude_code': facts.check(ctx.home), 'warnings': []}
    r, j = switcher.read_journal(ctx)
    out['journal'] = None if r.kind == ABSENT else (
        {'from': j.get('from_id'), 'to': j.get('to_id'), 'step': j.get('step'), 'at': j.get('at')}
        if r.kind == PRESENT else {'damaged': True})
    if r.kind != ABSENT:
        out['warnings'].append('A switch was cut off; Switchboard settles it at its next start or switch.')
    store = {'path': vault.store_path(ctx), 'schema': vault.SCHEMA, 'readable': True, 'accounts': 0,
             'backups': sum(1 for n in range(1, vault.BACKUPS + 1)
                            if os.path.exists('%s.%d' % (vault.store_path(ctx), n)))}
    try:
        doc = vault.load(ctx)
        store['accounts'] = len(doc['accounts'])
        claims = []
        for aid, c in doc.get('claims', {}).items():
            claims.append({'account_id': aid, 'alive': vault.claim_alive(ctx, c) if isinstance(c, dict) else False,
                           'cleanup': (c or {}).get('cleanup') if isinstance(c, dict) else None})
        out['claims'] = claims
        out['refreshing'] = dict((aid, {'fp': m.get('fp'), 'at': m.get('at')})
                                 for aid, m in doc.get('refreshing', {}).items() if isinstance(m, dict))
        for aid, a in doc['accounts'].items():
            if a.get('conflict'):
                out['warnings'].append('The login kept as %s may belong to another account (label and login '
                                       'disagree).' % (a.get('email') or aid))
    except vault.VaultError as e:
        store['readable'] = False
        out['warnings'].append(str(e))
        out['claims'], out['refreshing'] = [], {}
    out['store'] = store
    from . import putback
    last = putback.last_put_back(ctx)
    words = "Put back Claude Code's lost sign-in"
    try:
        when = time.strftime('%H:%M on %a %b %d', time.localtime(last['at'])) if last is not None else None
    except (OverflowError, ValueError, OSError):        # a hand-edited record
        when = "a time that can't be read"
    out['put_back'] = {'ok': True, 'line': '%s: never' % words} if last is None else {
        'line': '%s: last at %s, for %s' % (words, when, last.get('account') or 'an account')}
    out['recovery'] = [dict((k, e.get(k)) for k in ('key', 'account_id', 'reason', 'fp', 'at', 'where'))
                       for e in vault.stashed(ctx)]
    from . import keychain as keychain_mod
    argv = keychain_mod.argv_note(ctx.data_dir)
    if argv:
        out['keychain_argv'] = argv
    _, _, cfg = ccfiles.read_config(ctx.env, ctx.home)
    if isinstance(cfg, dict) and cfg.get('primaryApiKey'):
        out['warnings'].append('Claude Code also has an API key in its settings; it may use that instead of the '
                               'account login.')
    out['warnings'].extend(out['claude_code']['warnings'])
    return out
