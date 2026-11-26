"""Keeping logins: a new /login becomes an account; a rotation by Claude Code resyncs the saved copy.

Cheap by design: each pass looks at the global config's identity block (its file time and account) and the
plaintext login file's time; the Keychain item is read only when one of those changed, or every 3 minutes (Claude
Code renews its token without touching either). Then:
- the live login is a saved account's (by fingerprint or refresh-expiry stamp) with newer bytes: Claude Code rotated
  it, so the saved copy is brought up to date (never older over newer);
- it is a login no saved account has: whose it is comes from Anthropic's profile answer (asked outside every lock),
  else from the label when the label names an account not saved yet. A label naming an account that is saved with
  another login is not enough (a label alone can put one account's login in another's slot): it waits for proof.
- an account removed on purpose stays out; bytes read only from a fallback copy (degraded) are never kept.
A failed read is retried after 60 s when macOS was slow, 5 minutes otherwise, and said once.

The same pass keeps Claude Code's settings naming the account its login is: a session that was open during a switch
can write the earlier account's name back a moment later, and nothing else would ever correct it.

And it keeps Claude Code on the newest sign-in kept for the account it is on (_newest_back). A sign-in again from the
page puts the new login live at once (roster.add_login), but a Claude Code session that held the older sign-in can
renew it and write it back a moment later, and that older sign-in still ends on its own date. So when Claude Code's
login is an earlier sign-in of an account whose kept sign-in is newer and still works, or its login ended on its date
or was emptied and a newer sign-in of the account its settings name is kept, the newest is put back live through the
switch transaction (the live login's mark checked again under the locks, read back, undone on a mismatch). Newer is
told by the sign-ins' end dates. An emptied login of the same sign-in as the kept one, or of a later one, gets nothing:
the account needs a sign-in, said once. An emptied login whose end date is gone gets the kept sign-in once: the try is
kept in the vault's record (PUT_LIVE), so no process writes that sign-in over an emptied login again until another
sign-in is kept for the account or Claude Code is seen on another working login of it. A working
login must be proved this account's (its fingerprint, its sign-in's stamp, or Anthropic's answer); one that can't work
any more may go by its settings' label, as add_login's does. Over a login that still works, at most RENEW_MAX attempts
in RENEW_SPAN_S for one account, landed or not, counted in the vault's record (so every process that runs this pass,
and a restart, count together): then one line says so and Claude Code is left on the older sign-in, which still
works, until the span has passed. Over one that can't work any more the cap does not hold Claude Code back: it is never
left with no working login because of the cap. After an attempt that failed or was undone, the next one waits
(PUT_LIVE_WAITS, longer each time), whichever login it is over, and History says so once. Never the older sign-in
saved over the newer one, and the put spends
nothing: a kept copy that has run out goes live as it is (tokens: never renewed while its account is in use).
"""
import copy
import dataclasses
import os
import threading

from . import identity, live as live_mod, vault
from .api import ABSENT, PRESENT, UNREADABLE

REREAD_S = 180.0
RETRY_SLOW_S = 60.0
RETRY_OTHER_S = 300.0
LABEL_BACK_AFTER_S = 20.0       # how long the settings must have named another account before they are put back
LABEL_BACK_MAX = 6              # times within LABEL_BACK_SPAN_S that the settings are made to name the login's account
LABEL_BACK_SPAN_S = 3600.0
RENEW_MAX = 3                   # times within RENEW_SPAN_S the newest kept sign-in of one account is put back live
RENEW_SPAN_S = 3600.0
RENEW = 'renew'                 # the source those switches are recorded with (the vault's switches), to count them
PUT_LIVE = 'put_live'           # the vault's record of the keep pass's puts, per account (fingerprints and times only)
# The wait before the next put after the first, the second, and every later failed or undone one in a row. The pass
# reads Claude Code's item every REREAD_S at least, and what undoes a put (a chat that writes its older login back, a
# Keychain that refuses) does not change from one pass to the next: ten minutes spans three reads, and the steps after
# it keep a lasting trouble to one try an hour. Over a login that can't work, the loop's forced switch goes on
# meanwhile.
PUT_LIVE_WAITS = (600.0, 1800.0, 3600.0)
PUT_LIVE_HOLD_S = 120.0         # while one attempt is under way, no other process starts one (a switch is done by then)
PUT_FAILED = ('Putting the sign-in Switchboard keeps for %s back in Claude Code did not work. Switchboard tries again '
              'later, less often each time.')
KEPT_BACK = 'Switchboard put the sign-in it keeps for %s back in Claude Code'   # a put the end dates don't prove newer
NEEDS_SIGN_IN = ('%s needs sign-in: Anthropic refused its login in Claude Code, and Switchboard keeps no newer sign-in '
                 'of it. Sign in to it in Claude Code, and Switchboard keeps it again.')
NEEDS_AGAIN = ('%s needs sign-in: Anthropic refused its login in Claude Code again after Switchboard put back the '
               'sign-in it keeps. Sign in to it in Claude Code, and Switchboard keeps it again.')
_STATES = {}
_STATES_LOCK = threading.Lock()


class _State:
    def __init__(self):
        self.sig = None
        self.read_at = None
        self.retry_at = 0.0
        self.said = set()
        self.last_owner = None
        self.label_other = None         # (the other account the settings name, when that was first seen)
        self.label_back = []


def _state(ctx):
    with _STATES_LOCK:
        return _STATES.setdefault(ctx.data_dir, _State())


def reset(ctx=None):
    with _STATES_LOCK:
        if ctx is None:
            _STATES.clear()
        else:
            _STATES.pop(ctx.data_dir, None)


def in_runas_env(ctx):
    """True inside a `switchboard run` session (its config folder is one of ours): never keep or switch from there."""
    folder = ctx.env.get('CLAUDE_CONFIG_DIR') or ''
    if not folder:
        return False
    run_dir = os.path.realpath(ctx.path('run'))
    try:
        real = os.path.realpath(folder)
    except (OSError, ValueError):
        return False
    return real == run_dir or real.startswith(run_dir + os.sep)


def _stat(path):
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        return None


def signal(ctx):
    """What changes when Claude Code's login may have: the config's identity, the login file, our own store."""
    from . import facts
    r, block, path = live_mod.config_identity(ctx, cached=True)
    return (path, _stat(path), live_mod.label(block) if r.kind == PRESENT else r.why,
            _stat(facts.credentials_file(ctx.env, ctx.home)), _stat(vault.store_path(ctx)))


def _once(st, key, text):
    if key in st.said:
        return None
    st.said.add(key)
    return text


def keep_pass(ctx, force=False):
    """One pass. Returns a plain note when a login was kept or resynced (or a problem is said for the first time)."""
    if in_runas_env(ctx):
        return None
    st = _state(ctx)
    now_m = ctx.mono()
    if not force and now_m < st.retry_at:
        return None
    sig = signal(ctx)
    if not force and sig == st.sig and st.read_at is not None and now_m - st.read_at < REREAD_S:
        return None
    try:
        from . import runas
        runas.reap_stale_claims(ctx)
    except Exception as e:  # noqa: BLE001 - keeping goes on
        ctx.log('Could not tidy a finished run: %s' % e.__class__.__name__)
    lr = live_mod.read_live(ctx, fresh=True)
    if lr.read.kind == UNREADABLE:
        slow = lr.read.why in ('timeout', 'busy', 'dialog')
        st.retry_at = now_m + (RETRY_SLOW_S if slow else RETRY_OTHER_S)
        if lr.read.why == 'dialog':
            return _once(st, 'unreadable', "macOS is waiting for you to answer a Keychain dialog, so Claude Code's "
                                           "login can't be read yet; trying again in a minute.")
        return _once(st, 'unreadable', "Couldn't read Claude Code's login from the Keychain (%s); trying again in %s."
                     % ('macOS is slow to answer' if slow else lr.read.why, 'a minute' if slow else '5 minutes'))
    st.said.discard('unreadable')
    st.sig, st.read_at = sig, now_m
    login = lr.login
    if not lr.read.degraded and login is not None and \
            (login.kind == 'wiped' or (login.full_pair and _ended(ctx, login))):
        back = _ended_back(ctx, st, login)
        if back is not None or not login.full_pair:
            return back
    if lr.read.degraded or login is None or not login.full_pair:
        return None
    try:
        doc = vault.load(ctx)
    except vault.VaultError as e:
        return _once(st, 'store', str(e))
    cfg_r, block, _ = live_mod.config_identity(ctx, cached=True)    # parsed by signal() just now
    proof = identity.offline(login, doc, block if cfg_r.kind == PRESENT else None)
    if proof.account_id:
        _noticed(ctx, st, doc, proof.account_id)
        a = doc['accounts'][proof.account_id]
        older = _newer_kept(ctx, a, login)
        _seen_working(ctx, doc, proof.account_id, login, older)
        if older:
            return _newest_back(ctx, st, proof.account_id, login)
        fixed = _label_back(ctx, st, proof.account_id, login)
        if login.fingerprint == a.get('fp') and login.access_fp == a.get('access_fp') and \
                login.expires_ms == a.get('expires_ms'):
            return _check_label_proof(ctx, st, doc, proof.account_id, login) or fixed
        return _resync(ctx, st, proof.account_id, login) or fixed
    got = _keep_new(ctx, st, login)
    if isinstance(got, _Older):                     # Anthropic says whose it is, and that account's kept one is newer
        return _newest_back(ctx, st, got.aid, login)
    return got


class _Older(object):
    """What _keep_new answers for a login Anthropic says is a kept account's, whose kept sign-in is newer: put back once
    the locks it holds are let go."""

    def __init__(self, aid):
        self.aid = aid


def _ended(ctx, login):
    """Has this login's sign-in ended on its date?"""
    return login.refresh_expires_ms is not None and login.refresh_expires_ms <= ctx.wall() * 1000


def _held_works(ctx, a):
    """Does account record `a` keep a sign-in that may still work: its date not passed, not refused by Anthropic when
    Switchboard renewed it, not said by Anthropic to be another account's?"""
    rx = a.get('rx_ms')
    if not isinstance(rx, int) or isinstance(rx, bool) or rx <= ctx.wall() * 1000 or a.get('conflict'):
        return False
    return not ((a.get('strike') or {}).get('fp') and (a.get('strike') or {}).get('fp') == a.get('fp'))


def _newer_kept(ctx, a, login):
    """Does account record `a` keep a newer sign-in than `login`, one that still works (not refused by Anthropic, its
    date not passed)? A working login whose date is not known counts as older (an emptied one: _ended_back)."""
    if not _held_works(ctx, a):
        return False
    return login.refresh_expires_ms is None or live_mod.older_sign_in(login, a.get('rx_ms'))


def _ended_back(ctx, st, login):
    """Claude Code's login can't work any more (emptied after a refusal, or past its date): the newest kept sign-in of
    its account back live, when its end date proves it a later sign-in. Whose it is: its fingerprint or stamp, else
    the account its settings name (as add_login judges a dead login: nothing that works is written over).

    An emptied login of the same sign-in as the kept one (end dates within facts.SAME_SIGN_IN_MS), or of a later one:
    the kept one is a spent generation or an older sign-in, so nothing is written, the account needs a sign-in (said
    once), and the loop's forced switch can follow in the same pass. An emptied login that lost its end date can't be
    told apart: the kept sign-in is tried once. Over a kept sign-in past its date, or refused when Switchboard renewed
    it, nothing is ever written."""
    try:
        doc = vault.load(ctx)
    except vault.VaultError:
        return None
    aid = identity.offline(login, doc).account_id
    if aid is None:
        r, block, _ = live_mod.config_identity(ctx)
        aid = identity.label_id(doc, block) if r.kind == PRESENT else None
    a = doc['accounts'].get(aid) if aid else None
    if a is None or not _held_works(ctx, a):
        return None
    if login.refresh_expires_ms is not None:
        if live_mod.older_sign_in(login, a.get('rx_ms')):
            return _newest_back(ctx, st, aid, login, capped=False)
        return _needs_sign_in(ctx, aid, NEEDS_SIGN_IN) if login.kind == 'wiped' else None
    if _put_record(doc, aid).get('tried') == a.get('fp'):
        return _needs_sign_in(ctx, aid, NEEDS_AGAIN)    # tried once already, and Claude Code emptied it again
    return _newest_back(ctx, st, aid, login, capped=False, once=True)


def _put_record(doc, aid):
    """Account aid's entry in the vault's record of the keep pass's puts (PUT_LIVE), or {}. 'tried': the fingerprint of
    the kept sign-in tried once over an emptied login with no end date; 'needs': the fingerprint of the kept sign-in
    for which "needs sign-in" was said."""
    recs = doc.get(PUT_LIVE)
    rec = recs.get(aid) if isinstance(recs, dict) else None
    return rec if isinstance(rec, dict) else {}


def _change_put_record(ctx, aid, change):
    """Under the account lock: change(entry, doc) changes account aid's entry in place, and the vault is saved when it
    changed. Returns what change returned. Raises vault.LockBusy, vault.VaultError, OSError."""
    with vault.account_lock(ctx, timeout=5.0):
        doc = vault.load(ctx)
        rec = copy.deepcopy(_put_record(doc, aid))
        before = copy.deepcopy(rec)
        out = change(rec, doc)
        if rec != before:
            recs = dict(doc[PUT_LIVE]) if isinstance(doc.get(PUT_LIVE), dict) else {}
            if rec:
                recs[aid] = rec
            else:
                recs.pop(aid, None)
            doc[PUT_LIVE] = recs
            vault.save(ctx, doc)
    return out


def _seen_working(ctx, doc, aid, login, older):
    """Claude Code is on a working login of account aid. When it is not the one tried once over an emptied login, that
    try is over, and so is a "needs sign-in" said for the account (each is said again when it happens again). When it
    is not an older sign-in than the one kept (older False), nothing needs putting back: a spell of failed puts is
    over, and the next failure is said again and waits the first step. The attempt times of the hourly cap stay."""
    rec = _put_record(doc, aid)
    drop = [k for k in ('tried', 'needs') if rec.get(k)] if rec.get('tried') != login.fingerprint else []
    if not older:
        drop += [k for k in ('fails', 'said', 'next_at') if k in rec]
    if not drop:
        return
    try:
        _change_put_record(ctx, aid, lambda r, d: [r.pop(k, None) for k in drop])
    except (vault.LockBusy, vault.VaultError, OSError) as e:
        ctx.log('Could not note that Claude Code works on %s again: %s' % (aid, e.__class__.__name__))


def _needs_sign_in(ctx, aid, words):
    """Nothing is written over Claude Code's emptied login of account aid: the account needs a sign-in. Said once for
    the sign-in kept for it, by whichever process sees it first (the vault's record). Returns the line said, or
    None."""
    from .. import names

    def change(rec, doc):
        a = doc['accounts'].get(aid)
        if a is None or rec.get('needs') == a.get('fp'):
            return None
        rec['needs'] = a.get('fp')
        return names.in_doc(doc, aid), a.get('email') or aid
    try:
        said = _change_put_record(ctx, aid, change)
    except (vault.LockBusy, vault.VaultError, OSError) as e:
        ctx.log('Could not note that %s needs a sign-in: %s' % (aid, e.__class__.__name__))
        return None
    if said is None:
        return None
    text = words % said[0]
    ctx.log("Did not put the kept sign-in of %s over Claude Code's emptied login: it is not a later sign-in, or it was "
            "tried once already" % said[1])
    ctx.say(text, 'warn', h='problem', account=said[1], by='auto')
    return text


def _quiet(ctx):
    """A copy of ctx that keeps the lines a switch says instead of saying them, so the keep pass says them in its own
    words. Returns (the copy, the list of (text, kind, facts))."""
    lines = []
    return dataclasses.replace(ctx, events=lambda text, kind, facts_: lines.append((text, kind, facts_))), lines


def _num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _waiting(rec, now):
    """Does account aid's entry hold the next attempt back now (after a failed one, or while one is under way)? A time
    further ahead than any wait (the clock was set back) holds nothing."""
    nxt = _num(rec.get('next_at'))
    return nxt is not None and now < nxt <= now + PUT_LIVE_WAITS[-1] + PUT_LIVE_HOLD_S


def _begin(rec, doc, aid, now, capped, once):
    """Under the account lock: may an attempt start now? 'wait', 'tried' (once: that sign-in was tried already), the
    number of attempts this hour when capped, or 'go' with the attempt counted and held for PUT_LIVE_HOLD_S."""
    if _waiting(rec, now):
        return 'wait'
    if once and rec.get('tried') == (doc['accounts'].get(aid) or {}).get('fp'):
        return 'tried'
    raw = rec.get('at') if isinstance(rec.get('at'), list) else []
    at = [t for t in (_num(x) for x in raw) if t is not None and 0 <= now - t < RENEW_SPAN_S]
    if capped and len(at) >= RENEW_MAX:
        return len(at)
    rec['at'] = (at + [now])[-4 * RENEW_MAX:]
    rec['next_at'] = now + PUT_LIVE_HOLD_S
    return 'go'


def _end(rec, doc, aid, now, outcome, once):
    """Under the account lock: note how an attempt ended. 'landed' ends a spell of failed ones (and, once, keeps the
    fingerprint tried); 'changed' (Claude Code's login changed before anything was written) lets the next one go;
    'failed' makes the next one wait PUT_LIVE_WAITS by the failures in a row. True when this failure is the first of
    its spell (it is said then, and only then)."""
    if outcome == 'landed':
        for k in ('next_at', 'fails', 'said'):
            rec.pop(k, None)
        if once:
            rec['tried'] = (doc['accounts'].get(aid) or {}).get('fp')
        return False
    if outcome == 'changed':
        rec.pop('next_at', None)
        return False
    fails = rec.get('fails') if isinstance(rec.get('fails'), int) and not isinstance(rec.get('fails'), bool) else 0
    fails = min(max(fails, 0) + 1, 99)
    rec['fails'] = fails
    rec['next_at'] = now + PUT_LIVE_WAITS[min(fails, len(PUT_LIVE_WAITS)) - 1]
    first = not rec.get('said')
    rec['said'] = True
    return first


def _newest_back(ctx, st, aid, login, capped=True, once=False):
    """Put account aid's newest kept sign-in live over `login`, an earlier sign-in of the same account, through the
    switch transaction. Over a login that still works (capped): RENEW_MAX attempts in RENEW_SPAN_S, then said once and
    left. Over one that can't work any more: not capped. Every attempt, landed or not, is counted in the vault's record
    (PUT_LIVE) under the account lock, so every process that runs this pass counts together: each write of Claude
    Code's item can set many open chats saving at once. After an attempt that failed or was undone, the next waits
    PUT_LIVE_WAITS, longer for each failure in a row, and History says it once for the whole spell. once: an emptied
    login with no end date; when the put lands, the record keeps the fingerprint tried. History says "new sign-in"
    only when the end dates prove one (live.older_sign_in), else that the kept sign-in was put back. Returns the line
    said, or None."""
    from . import switcher
    from .api import AccountsError
    try:
        doc = vault.load(ctx)
    except vault.VaultError:
        return None
    a = doc['accounts'].get(aid)
    if a is None or _waiting(_put_record(doc, aid), ctx.wall()):
        return None
    email = a.get('email') or aid
    proven = live_mod.older_sign_in(login, a.get('rx_ms'))
    try:
        go = _change_put_record(ctx, aid, lambda rec, d: _begin(rec, d, aid, ctx.wall(), capped, once))
    except (vault.LockBusy, vault.VaultError, OSError) as e:
        ctx.log('Putting the newer sign-in of %s back waits: %s' % (email, e.__class__.__name__))
        return None
    if go == 'wait':
        return None
    if go == 'tried':
        return _needs_sign_in(ctx, aid, NEEDS_AGAIN)
    if go != 'go':
        ctx.log('Did not put the newer sign-in of %s back: Claude Code went back to an older one %d times this hour'
                % (email, go))
        text = _once(st, 'renew:' + aid, 'Claude Code keeps going back to an older sign-in of %s. Switchboard leaves '
                                         'it as it is for now and puts the newer one back within the hour.' % email)
        if text:
            ctx.say(text, 'warn', h='signin', account=email, by='auto')
        return text
    quiet, lines = _quiet(ctx)
    outcome, why = 'failed', None
    try:
        res = switcher.switch(quiet, aid, kind='manual', source=RENEW, interactive=False,
                              replace_dead=switcher.live_mark(login),
                              reason='Claude Code had an older sign-in of %s' % email if proven else
                              "Claude Code's login of %s was emptied" % email)
        if res.ok:
            outcome = 'landed'
        else:
            why = res.note or res.reason
    except AccountsError as e:
        # the same words as switcher._changed_meanwhile: Claude Code's login moved before anything was written
        outcome, why = ('changed' if e.args == switcher._changed_meanwhile().args else 'failed'), str(e)
    _say(ctx, lines, proven, outcome == 'landed')
    try:
        first = _change_put_record(ctx, aid, lambda rec, d: _end(rec, d, aid, ctx.wall(), outcome, once))
    except (vault.LockBusy, vault.VaultError, OSError) as e:
        first = False
        ctx.log('Could not note the attempt to put the sign-in of %s back: %s' % (email, e.__class__.__name__))
    if outcome == 'changed':
        st.sig = None                                   # the next pass looks again
        ctx.log('Putting the newer sign-in of %s back waits: %s' % (email, why))
        return None
    if outcome == 'failed':
        ctx.log('Putting the newer sign-in of %s back did not work: %s' % (email, why))
        if not first:
            return None
        text = PUT_FAILED % email
        ctx.say(text, 'warn', h='signin', account=email, by='auto')
        return text
    st.said.discard('renew:' + aid)
    if not proven:
        ctx.log("Put the kept sign-in of %s back in use: Claude Code's login of it was emptied and its end date "
                "was gone" % email)
        return KEPT_BACK % email
    ctx.log('Put the newer sign-in of %s back in use: Claude Code had an older sign-in of it' % email)
    return switcher.SAME_ACCOUNT % email


def _say(ctx, lines, proven, landed):
    """Say the lines a switch of the keep pass kept back (_quiet), with "new sign-in" only when it is proven. A switch
    that did not land says its warning in the log only: _newest_back says one line for the whole spell."""
    from . import switcher
    new = switcher.SAME_ACCOUNT % ''
    for text, kind, facts_ in lines:
        if not landed and kind == 'warn':
            ctx.log(text)
            continue
        if not proven and facts_.get('h') == 'signin' and text.startswith(new):
            text = KEPT_BACK % text[len(new):]
        ctx.say(text, kind, **facts_)


def _noticed(ctx, st, doc, owner):
    """Claude Code moved to another saved account without Switchboard (a /login): said as Claude Code's switch."""
    before, st.last_owner = st.last_owner, owner
    if before is None or before == owner:
        return
    recent = [s for s in (doc.get('switches') or []) if s.get('to') == owner and ctx.wall() - (s.get('at') or 0) < 600]
    if recent:
        return
    email = doc['accounts'][owner].get('email') or owner
    from .. import names                 # with its organization when another kept account has its email (names.py)
    ctx.say('Claude Code is now on %s' % names.in_doc(doc, owner), 'switch', h='switch', account=email, to=email,
            by='claude-code')


def _locked_live(ctx, login):
    """Under our locks: is the live login still exactly `login`?"""
    cur = live_mod.read_live(ctx, fresh=True)
    return cur.usable and cur.login is not None and cur.login.text == login.text


def _label_back(ctx, st, aid, login):
    """The live login is account aid's (its own bytes say so), and Claude Code's settings name another account: make
    the settings name aid again, and say so in History. A Claude Code session that was open during a switch can write
    the earlier name back a moment later; nothing else corrects it, and Claude Code then shows one account while it
    works as another.

    Only once the settings have named that other account for LABEL_BACK_AFTER_S (a sign-in under way in Claude Code
    writes its login and its settings a moment apart). Written under the locks a switch takes, and only when the login
    is still the one that was read and no switch was cut off part-way (that one is settled first, from its own
    record). An account whose own record is not settled (_owner_unsure) is never written for. Something that goes on
    writing another name wins after LABEL_BACK_MAX times in LABEL_BACK_SPAN_S, counted by each process that runs this
    pass: one line says so and the settings are left as they are until the span has passed. A write that could not be
    made is tried again when something changes, or at the next full look. Reading the account's numbers never depends
    on this (roster.access_token goes by the login). Returns the line it said, or None."""
    from . import ccfiles, switcher
    r, block, _ = live_mod.config_identity(ctx)
    try:
        a = vault.load(ctx)['accounts'].get(aid) if r.kind == PRESENT and block is not None else None
    except vault.VaultError:
        a = None
    if a is None or switcher._label_is(block, a) or _owner_unsure(a):
        st.label_other = None
        return None
    email = a.get('email') or aid
    now_m = ctx.mono()
    other = (aid, live_mod.label(block))
    if st.label_other is None or st.label_other[0] != other:
        st.label_other = (other, now_m)
    if now_m - st.label_other[1] < LABEL_BACK_AFTER_S:
        st.sig = None                                   # the next pass looks again
        return None
    st.label_back = [t for t in st.label_back if now_m - t < LABEL_BACK_SPAN_S]
    if len(st.label_back) >= LABEL_BACK_MAX:
        return _once(st, 'label-keeps-changing',
                     "Claude Code's settings keep naming another account than the one its login is (%s). Switchboard "
                     "goes by the login, and leaves the settings as they are for now." % email)
    try:
        with vault.switch_lock(ctx, timeout=0.5), vault.account_lock(ctx, timeout=5.0):
            if switcher.read_journal(ctx)[0].kind != ABSENT:
                return None
            a = vault.load(ctx)['accounts'].get(aid)
            if a is None or not _locked_live(ctx, login):
                st.sig = None
                return None
            _, block, _ = live_mod.config_identity(ctx)
            want = switcher._identity_for(a)
            if block is None or (aid, live_mod.label(block)) != other or _owner_unsure(a) or \
                    not switcher._label_is(want, a):
                return None
            with ccfiles.config_lock(ctx.env, ctx.home, log=ctx.log):
                ccfiles.write_identity(ctx.env, ctx.home, want)
    except (vault.LockBusy, vault.VaultError, ccfiles.LockBusy, ccfiles.CCFileError, OSError) as e:
        ctx.log('Putting the settings back waits: %s' % e.__class__.__name__)
        return None
    st.label_other = None
    st.label_back.append(now_m)
    st.said.discard('label-keeps-changing')
    text = 'Claude Code is on %s (its settings named another account; fixed)' % email
    ctx.say(text, 'info', h='repair', account=email, by='auto')
    return text


def _owner_unsure(a):
    """A saved account whose own login is not settled: first kept on a label alone and not checked with Anthropic yet,
    or one Anthropic said is another account's. The settings are never written from such a record."""
    return bool(a.get('conflict')) or (a.get('proof') or {}).get('kind') == 'label'


def _resync(ctx, st, aid, login):
    try:
        with vault.switch_lock(ctx, timeout=0.5), vault.account_lock(ctx, timeout=5.0):
            doc = vault.load(ctx)
            a = doc['accounts'].get(aid)
            if a is None or not _locked_live(ctx, login):
                st.sig = None
                return None
            vr = vault.read_current(ctx, aid, fresh=True)
            if vr.kind == UNREADABLE:
                a['unreadable_at'] = ctx.wall()
                vault.save(ctx, doc)
                st.sig = None
                return None
            stored = live_mod.parse(vr.value) if vr.kind == PRESENT else None
            verdict = live_mod.compare(login, stored)
            refused = stored is not None and (a.get('strike') or {}).get('fp') == stored.fingerprint
            if stored is not None and stored.full_pair and not refused and verdict not in ('newer',) and \
                    not (verdict == 'same' and not vault.same_saved(stored.text, login.text)):
                if verdict == 'same':
                    vault.note_login(a, login, None, a.get('saved_at'))
                    vault.save(ctx, doc)
                return None
            if not vault.save_login(ctx, aid, login.text, old=vr).ok:
                st.sig = None
                return None
            vault.note_login(a, login, 'fingerprint' if login.fingerprint == a.get('fp') else 'refresh_expiry',
                             ctx.wall())
            vault.save(ctx, doc)
            email = a.get('email') or aid
            if stored is not None and stored.refresh_expires_ms != login.refresh_expires_ms and \
                    identity.renewal_of(login.refresh_expires_ms, stored.refresh_expires_ms):
                ctx.log('The saved login of %s (%s) was older than Claude Code\'s login of the same sign-in (%s), '
                        'whose end stamp is %.1f s earlier' % (email, stored.fingerprint, login.fingerprint,
                                                              (stored.refresh_expires_ms - login.refresh_expires_ms)
                                                              / 1000.0))
            ctx.log('Saved the newer login Claude Code made for %s (%s)' % (email, login.fingerprint))
            return 'Saved the newer login Claude Code made for %s' % email
    except (vault.LockBusy, vault.VaultError, OSError) as e:
        st.sig = None
        ctx.log('Resync waits: %s' % e.__class__.__name__)
        return None


def _check_label_proof(ctx, st, doc, aid, login):
    """An account first kept on its label alone is checked with Anthropic while its token is fresh."""
    a = doc['accounts'][aid]
    if (a.get('proof') or {}).get('kind') != 'label' or not login.has_access or login.expired(ctx.wall()):
        return None
    ans = identity.lookup_profile(ctx, login.access_token)
    if ans.status != 'resolved':
        return None
    try:
        with vault.changing(ctx, timeout=5.0) as d2:
            a2 = d2['accounts'].get(aid)
            if a2 is None:
                return None
            if identity.agrees(a2, ans.uuid, ans.org or ''):
                a2['proof'] = {'kind': 'profile', 'at': ctx.wall()}
                a2.pop('conflict', None)            # Anthropic's profile says this login is this account's own
                return None
            a2['conflict'] = {'profile_uuid': ans.uuid, 'profile_email': ans.email, 'at': ctx.wall()}
    except (vault.LockBusy, vault.VaultError, OSError):
        return None
    return _once(st, 'conflict:' + aid, 'The login kept as %s belongs to %s according to Anthropic; label and login '
                                        'disagree.' % (a.get('email') or aid, ans.email or 'another account'))


def _keep_new(ctx, st, login):
    """A login no saved account has."""
    _, block, _ = live_mod.config_identity(ctx)
    ans = None
    if login.has_access and not login.expired(ctx.wall()):
        ans = identity.lookup_profile(ctx, login.access_token)
    try:
        with vault.switch_lock(ctx, timeout=0.5), vault.account_lock(ctx, timeout=5.0):
            doc = vault.load(ctx)
            if not _locked_live(ctx, login):
                st.sig = None
                return None
            _, block, _ = live_mod.config_identity(ctx)
            if identity.offline(login, doc, block).account_id:
                return None                                 # kept meanwhile (the page's Add)
            now = ctx.wall()
            if ans is not None and ans.status == 'resolved':
                p = identity.from_profile(doc, ans, block)
                aid = p.account_id or vault.account_key(doc, ans.uuid, ans.org or '')
                if aid in doc['removed']:
                    return None
                if p.account_id:
                    a = doc['accounts'][aid]
                    vr = vault.read_current(ctx, aid, fresh=True)
                    if vr.kind == UNREADABLE:
                        st.sig = None
                        return None
                    stored = live_mod.parse(vr.value) if vr.kind == PRESENT else None
                    refused = (a.get('strike') or {}).get('fp') == a.get('fp')
                    if stored is not None and stored.full_pair and not refused and \
                            live_mod.compare(login, stored) not in ('newer',):
                        # the saved one is a newer sign-in: keep it, and put it back live (once the locks are let go)
                        return _Older(aid) if _newer_kept(ctx, a, login) else None
                    if not vault.save_login(ctx, aid, login.text, old=vr).ok:
                        st.sig = None
                        return None
                    vault.note_login(a, login, 'profile', now)
                    if block and not p.disagree:
                        a['identity'] = dict(block)
                    vault.save(ctx, doc)
                    email = a.get('email') or aid
                    if stored is not None and identity.renewal_of(login.refresh_expires_ms, stored.refresh_expires_ms):
                        # one sign-in renewed by Claude Code, whose owner only Anthropic could tell: a renewal
                        if stored.refresh_expires_ms != login.refresh_expires_ms:
                            ctx.log('The saved login of %s (%s) was older than Claude Code\'s login of the same '
                                    'sign-in (%s), whose end stamp is %.1f s earlier'
                                    % (email, stored.fingerprint, login.fingerprint,
                                       (stored.refresh_expires_ms - login.refresh_expires_ms) / 1000.0))
                        ctx.log('Saved the newer login Claude Code made for %s (%s -> %s)'
                                % (email, stored.fingerprint, login.fingerprint))
                        return 'Saved the newer login Claude Code made for %s' % email
                    ctx.say('Kept the new sign-in of %s' % email, 'info', h='kept', account=email, by='auto')
                    return 'Kept the new sign-in of %s' % email
                blk = dict(block) if block and not p.disagree else {
                    'accountUuid': ans.uuid, 'emailAddress': ans.email or '', 'organizationUuid': ans.org}
                return _add(ctx, doc, aid, blk, login, 'profile', now)
            uuid, email, org = live_mod.label(block)
            aid = vault.account_key(doc, uuid, org) if uuid else None
            if aid and aid not in doc['accounts'] and aid not in doc['removed']:
                return _add(ctx, doc, aid, block, login, 'label', now)
            if aid in doc['removed']:
                return None
            st.sig = None
            return _once(st, 'unproven:' + (login.fingerprint or ''),
                         "Claude Code is on a login Switchboard can't match to an account yet; it will check again.")
    except (vault.LockBusy, vault.VaultError, OSError) as e:
        st.sig = None
        ctx.log('Keeping waits: %s' % e.__class__.__name__)
        return None


def _add(ctx, doc, aid, block, login, proof, now):
    from .api import Read, ABSENT
    if not vault.save_login(ctx, aid, login.text, old=Read(ABSENT)).ok:
        return None
    a = vault.new_account(doc, aid, block, login, proof, now)
    vault.save(ctx, doc)
    email = a.get('email') or aid
    n = len(doc['accounts'])
    text = 'Kept %s · %d account%s' % (email, n, '' if n == 1 else 's')
    ctx.say(text, 'switch', h='kept', account=email, by='auto')
    return text
