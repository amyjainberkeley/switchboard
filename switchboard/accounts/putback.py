"""Putting Claude Code's sign-in back when Claude Code lost it and nobody signed out.

What Claude Code does (as of 2.1.329 and 2.1.331): every running Claude Code process saves the
whole Keychain item from its own copy. When a save fails, Claude Code writes the object to its plaintext login file
and deletes the Keychain item; a process that reads while the item is gone holds "no login", and its next save brings
the item back without the account login (only connector logins). Many processes save at once right after a switch
(the VS Code extension restarts one per open chat), so that is when the login can be lost this way. Claude Code's
settings (oauthAccount in its global config) still name the account, and nothing in Claude Code puts the login back.
A running process trusts its cached read of the item for 30 s, so a chat may say "Not logged in" that long after the
login is back.

A sign-out the person chooses (/logout) deletes the item too, and then clears oauthAccount (the sign-out fails when it
can't). So settings that name nobody are a chosen sign-out, and nothing here ever undoes one. The item goes a moment
before the settings change, so the login must be seen missing, with the settings naming the same saved account, for
PUT_BACK_AFTER_S at least; and everything is read again under the locks just before the write.

decide() is the rule, with no I/O. look() reads what the rule needs (one Keychain read while a login is there),
decides, and writes when the rule says so: under the locks a switch takes, never over an account login (older or
newer), with the live item's machine-wide parts kept as they are now, read back before it is said.

Processes that read the item while it was gone may save it again a few seconds after a put-back, without the login.
So each put-back within PUT_BACK_SPAN_S waits longer than the one before (PUT_BACK_WAITS), and after PUT_BACK_MAX it
stops and asks for /login. Several processes saving the item at once also refuse each other's writes (security exits
45), so a write that does not land is tried again at once, up to TRIES times within TRIES_WITHIN_S, each after a fresh
read; only a write that reads back counts. A look whose tries all fail spaces the next tries by PUT_BACK_WAITS too,
without counting toward the limit. When the login was first seen missing and when each put-back landed are
kept in <data>/put-back.json (no secrets) under the account lock, so every process that looks shares one wait and one
limit, and a restart resets neither.
"""
import json
import os
import random
import threading
from dataclasses import dataclass
from typing import Optional, Tuple

from . import capture, ccfiles, identity, live as live_mod, switcher, vault
from .api import ABSENT, PRESENT, UNREADABLE, PutBack

PUT_BACK_AFTER_S = 4.0          # the login must be seen missing at least this long before the first put-back
PUT_BACK_WAITS = (PUT_BACK_AFTER_S, 15.0, 60.0, 300.0)     # the wait before each put-back within PUT_BACK_SPAN_S
PUT_BACK_MAX = 4                # put-backs within PUT_BACK_SPAN_S before it stops and asks for /login
PUT_BACK_SPAN_S = 3600.0
TRIES = 5                       # writes tried at once when one does not land
TRIES_WITHIN_S = 3.0
TRY_GAP_S = (0.1, 0.4)          # a random gap between two tries, so processes saving at once fall apart
CC_LOCKS_S = 2.0                # the wait for Claude Code's two credential locks (a renewal holds them about a second)
STATE = 'put-back.json'
SIGNINS = 'logins'              # the page's sign-in folders (connect.py): one not marked over is under way
SIGNIN_OVER = 'over'
SIGNIN_FRESH_S = 1800.0         # connect.py hands a sign-in folder to the engine only when it is this fresh
# A Claude Code session open during a switch can write the earlier account's name back into the settings a moment later
# (capture._label_back corrects it, but only while a login is there). A loss first seen this soon after a switch away
# from the account the settings name is not put back by that name.
SWITCHED_AWAY_S = 600.0

NO_LOGIN = ('no_login', 'empty')
# Claude Code 2.1.331, behind a server flag, can keep its item in a split form: the item carries this key (a pointer to
# other items) and the account login can sit in another item, so an item with no login in it is not a lost sign-in.
SPLIT_KEY = 'keychainOverflow'
SPLIT = 'split'
SPLIT_LINE = ("Claude Code keeps its sign-in in a split Keychain item, a form Switchboard does not put back; it left "
              "the item as it is")
LIMIT_WORDS = ('Claude Code keeps losing its sign-in. Switchboard has stopped putting it back for now. Run /login in '
               'Claude Code once.')
_RAND = random.Random()


@dataclass(frozen=True)
class Seen:
    """What one look found. live: the Read kind of Claude Code's login (PRESENT, ABSENT, UNREADABLE); degraded: only a
    fallback copy could be read; login: Login.kind when present (SPLIT for the split form); file_login: Claude Code's
    plaintext login file holds an account login. config: the Read kind of Claude Code's settings; named: the saved
    account its identity block names, or None. saved ('ok', 'missing', 'unreadable', 'incomplete') and the marks after
    it are about that account. part_way: a switch's journal is there; signing_in: a sign-in is under way on the page.
    left_at: wall time of the last recorded switch when it went from the named account to another, else None."""
    live: str
    degraded: bool = False
    login: Optional[str] = None
    file_login: bool = False
    config: str = PRESENT
    named: Optional[str] = None
    saved: Optional[str] = None
    refused: bool = False
    ended: bool = False
    conflict: bool = False
    unproven: bool = False
    claimed: bool = False
    renewing: bool = False
    part_way: bool = False
    signing_in: bool = False
    left_at: Optional[float] = None


@dataclass(frozen=True)
class Verdict:
    """action: 'put_back', 'wait' (seen missing; look again), 'leave' or 'limit'. account: the saved account the
    settings name. key: what is said once ('refused', 'limit', ...), None when there is nothing to say. missing:
    (account, wall time first seen missing), kept for the next look. watch: the next look should come soon."""
    action: str
    account: Optional[str] = None
    reason: str = ''
    key: Optional[str] = None
    missing: Optional[Tuple[str, float]] = None
    watch: bool = False


def _kind(login):
    """Login.kind, or SPLIT for an item in the split form that holds no login itself."""
    if login is None:
        return None
    if login.kind in NO_LOGIN and isinstance(login.blob, dict) and SPLIT_KEY in login.blob:
        return SPLIT
    return login.kind


def is_missing(seen):
    """Claude Code's login is gone, as an authoritative answer: no item (and no login file), or an item (or file)
    that holds no account login. Never a failed read, a fallback copy, an API key or an emptied login."""
    if seen.live == ABSENT:
        return True
    return seen.live == PRESENT and not seen.degraded and seen.login in NO_LOGIN


# The marks that keep an account's saved login out of use, in the order they are told: (key, words after "the saved
# login of <email>").
MARKS = (('missing', 'is not in the Keychain'),
         ('unreadable', "can't be read from the Keychain right now"),
         ('incomplete', 'is incomplete'),
         ('refused', 'was refused by Anthropic'),
         ('ended', 'has passed its sign-in end date'),
         ('conflict', 'may belong to another account'),
         ('unproven', 'was not checked with Anthropic yet'),
         ('claimed', 'is in use by a `switchboard run`'),
         ('renewing', 'is being renewed, or its renewal was cut off'))


def _marked(seen):
    return {'missing': seen.saved == 'missing', 'unreadable': seen.saved == 'unreadable',
            'incomplete': seen.saved == 'incomplete', 'refused': seen.refused, 'ended': seen.ended,
            'conflict': seen.conflict, 'unproven': seen.unproven, 'claimed': seen.claimed, 'renewing': seen.renewing}


def wait_for(done, now):
    """How long the login must be seen missing before the next put-back, by the put-backs made within the span."""
    n = sum(1 for t in done if now - t < PUT_BACK_SPAN_S)
    return PUT_BACK_WAITS[min(n, len(PUT_BACK_WAITS) - 1)]


def decide(seen, before, done, now, failed=()):
    """The rule (no I/O). before: (account, wall time) the login was first seen missing, or None. done: wall times of
    the put-backs that landed. failed: wall times of the looks whose writes all failed since the last one landed; the
    next try waits PUT_BACK_WAITS after the last of them, by their count. now: wall seconds. A time past the clock (it
    was set back) starts the wait again, and a put-back dated past it still counts toward the limit."""
    if seen.live == UNREADABLE:
        return Verdict('leave', reason="Claude Code's login can't be read right now", missing=before)
    if seen.degraded:
        return Verdict('leave', reason="only a fallback copy of Claude Code's login could be read", missing=before)
    if not is_missing(seen):
        words = {'wiped': 'Claude Code emptied its login after Anthropic refused it',
                 'api_key': 'Claude Code uses an API key', 'unparseable': "Claude Code's login can't be understood",
                 SPLIT: 'Claude Code keeps its sign-in in a split Keychain item'}
        return Verdict('leave', reason=words.get(seen.login, 'Claude Code has a login'))
    if seen.file_login:
        return Verdict('leave', reason="its login file still holds a login", key='file')
    if seen.config != PRESENT:
        return Verdict('leave', reason="Claude Code's settings can't be read" if seen.config == UNREADABLE else
                       'Claude Code has no settings file')
    if not seen.named:
        return Verdict('leave', reason="Claude Code's settings name no saved account (a sign-out, or an account "
                                       "Switchboard does not keep)")
    aid = seen.named
    if seen.part_way:
        return Verdict('leave', aid, 'a switch is part-way', watch=True)
    if seen.signing_in:
        return Verdict('leave', aid, 'a sign-in is under way on the page')
    since = before[1] if before and before[0] == aid and before[1] <= now else now
    keep = (aid, since)
    if seen.left_at is not None and since - seen.left_at < SWITCHED_AWAY_S:
        return Verdict('leave', aid, 'its settings name the account a switch just left', key='switched', missing=keep)
    marked = _marked(seen)
    for key, words in MARKS:
        if marked[key]:
            return Verdict('leave', aid, 'its saved login %s' % words, key=key, missing=keep)
    if sum(1 for t in done if now - t < PUT_BACK_SPAN_S) >= PUT_BACK_MAX:
        return Verdict('limit', aid, LIMIT_WORDS, key='limit', missing=keep)
    if now - since < wait_for(done, now):
        return Verdict('wait', aid, 'seen missing; looking again', missing=keep, watch=True)
    fails = [t for t in failed if 0 <= now - t < PUT_BACK_SPAN_S]
    if fails and now - max(fails) < PUT_BACK_WAITS[min(len(fails), len(PUT_BACK_WAITS)) - 1]:
        return Verdict('wait', aid, 'the last write did not land; looking again', missing=keep, watch=True)
    return Verdict('put_back', aid, 'its login is missing and its settings still name it', missing=keep, watch=True)


# ---------- reading what the rule needs ----------

def _signing_in(ctx, now):
    """A sign-in on the page is under way: one of its folders is not marked over and is fresh."""
    folder = ctx.path(SIGNINS)
    try:
        names = os.listdir(folder)
    except OSError:
        return False
    for name in names:
        d = os.path.join(folder, name)
        try:
            if not os.path.isdir(d) or os.path.exists(os.path.join(d, SIGNIN_OVER)):
                continue
            newest = max([os.path.getmtime(d)] + [os.path.getmtime(os.path.join(d, n)) for n in os.listdir(d)])
        except OSError:
            return True                                 # can't be told: taken as under way
        if -60 <= now - newest < SIGNIN_FRESH_S:
            return True
    return False


def _file_login(ctx):
    """Claude Code's plaintext login file holds an account login (never written over here), or can't be read."""
    from . import facts
    r, raw = ccfiles.read_bytes(facts.credentials_file(ctx.env, ctx.home))
    if r.kind != PRESENT:
        return r.kind == UNREADABLE
    try:
        return live_mod.parse(raw.decode('utf-8')).kind not in NO_LOGIN
    except UnicodeDecodeError:
        return True


def see(ctx, now, interactive=False):
    """(Seen, the live LiveRead, the saved Login or None, the account's email or None). While a login is there, this
    is one Keychain read; the rest is read only once the login is missing."""
    lr = live_mod.read_live(ctx, fresh=True, interactive=interactive)
    login = lr.login
    seen = Seen(live=lr.read.kind, degraded=lr.read.degraded, login=_kind(login))
    if not is_missing(seen):
        return seen, lr, None, None
    file_login = lr.read.provenance != 'file' and _file_login(ctx)
    cfg_r, block, _ = live_mod.config_identity(ctx)
    try:
        doc = vault.load(ctx)
    except vault.VaultError:
        return Seen(live=seen.live, login=seen.login, file_login=file_login, config=UNREADABLE), lr, None, None
    aid = identity.label_id(doc, block) if cfg_r.kind == PRESENT else None
    last = (doc.get('switches') or [None])[-1]
    left = isinstance(last, dict) and last.get('from') == aid and last.get('to') != aid
    base = dict(live=seen.live, login=seen.login, file_login=file_login, config=cfg_r.kind, named=aid,
                part_way=switcher.read_journal(ctx)[0].kind != ABSENT, signing_in=_signing_in(ctx, now),
                left_at=_num(last.get('at')) if left and aid else None)
    if aid is None:
        return Seen(**base), lr, None, None
    a = doc['accounts'][aid]
    vr = vault.read_current(ctx, aid, fresh=True, interactive=interactive)
    saved = live_mod.parse(vr.value) if vr.kind == PRESENT else None
    if vr.kind == UNREADABLE:
        state = 'unreadable'
    elif saved is None:
        state = 'missing'
    else:
        state = 'ok' if saved.full_pair else 'incomplete'
    stamps = [x for x in (a.get('rx_ms'), saved.refresh_expires_ms if saved is not None else None) if x]
    fp = saved.fingerprint if saved is not None else None
    from . import tokens
    pending = tokens._UNPERSISTED.get(aid)
    marker = (doc.get('refreshing') or {}).get(aid) or {}
    seen = Seen(saved=state, refused=bool(fp) and (a.get('strike') or {}).get('fp') == fp,
                ended=any(x <= now * 1000 for x in stamps), conflict=bool(a.get('conflict')),
                unproven=(a.get('proof') or {}).get('kind') == 'label',
                claimed=bool(vault.claimed(ctx, doc, aid)) or aid in (doc.get('claims') or {}),
                renewing=bool(fp) and (marker.get('fp') == fp or bool(pending and pending[0] == fp)), **base)
    return seen, lr, saved, a.get('email') or aid


# ---------- what is kept between looks ----------

def _num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def load_record(ctx):
    """The shared record: {'missing': (account, wall time) or None, 'done': [wall times the put-backs landed],
    'failed': [wall times of looks whose writes all failed], 'limit_at': when the limit was said or None, 'last':
    {'at', 'account'} or None, 'readable': False when the file is there and can't be used (then nothing is written),
    'why': the reason it can't be used, or None}."""
    r, obj = ccfiles.read_json(ctx.path(STATE))
    obj = obj if r.kind == PRESENT else {}
    done, failed = ([t for t in (_num(x) for x in obj.get(k) or []) if t is not None]
                    if isinstance(obj.get(k), list) else [] for k in ('done', 'failed'))
    m = obj.get('missing')
    missing = (m[0], _num(m[1])) if isinstance(m, list) and len(m) == 2 and isinstance(m[0], str) and \
        _num(m[1]) is not None else None
    last = obj.get('last') if isinstance(obj.get('last'), dict) and _num(obj['last'].get('at')) is not None else None
    return {'missing': missing, 'done': done, 'failed': failed, 'limit_at': _num(obj.get('limit_at')), 'last': last,
            'readable': r.kind != UNREADABLE, 'why': r.why if r.kind == UNREADABLE else None}


def _heal_record(ctx, now):
    """A record that is there but is not a JSON object (a bad restore, a hand edit) holds only times: it is started
    again, said once in the log. A record that can't be read at all is left as it is. True when the record is usable
    now."""
    try:
        with vault.account_lock(ctx, timeout=2.0):
            rec = load_record(ctx)
            if rec['readable'] or rec['why'] != 'unparseable':
                return rec['readable']
            ctx.log("Switchboard's note of earlier put-backs (%s) could not be understood; it starts a new one" % STATE)
            _save_record(ctx, {'missing': None, 'done': [], 'failed': []}, now)
            return True
    except (vault.LockBusy, ccfiles.CCFileError, OSError) as e:
        ctx.log('Could not start a new note of put-backs: %s' % e.__class__.__name__)
        return False


def _save_record(ctx, rec, now):
    """Write the record (the caller holds the account lock). Times older than the span are dropped."""
    keep = {'missing': list(rec['missing']) if rec.get('missing') else None,
            'done': [t for t in rec['done'] if now - t < PUT_BACK_SPAN_S][-2 * PUT_BACK_MAX:],
            'failed': [t for t in rec.get('failed') or [] if now - t < PUT_BACK_SPAN_S][-2 * PUT_BACK_MAX:],
            'limit_at': rec.get('limit_at'), 'last': rec.get('last')}
    os.makedirs(ctx.data_dir, mode=0o700, exist_ok=True)
    ccfiles.atomic_write(ctx.path(STATE), (json.dumps(keep) + '\n').encode('utf-8'), mode=0o600)


def _change_record(ctx, change, now):
    """Read, change and write the record under the account lock. False when it could not be done now."""
    try:
        with vault.account_lock(ctx, timeout=2.0):
            rec = load_record(ctx)
            if not rec['readable']:
                return False
            change(rec)
            _save_record(ctx, rec, now)
            return True
    except (vault.LockBusy, ccfiles.CCFileError, OSError) as e:
        ctx.log('Could not note a look for a lost sign-in: %s' % e.__class__.__name__)
        return False


def last_put_back(ctx):
    """{'at', 'account'} of the last put-back, or None (for doctor)."""
    return load_record(ctx)['last']


class _Said:
    """What this process has said in the current episode of a missing login (said once each)."""

    def __init__(self):
        self.keys = set()


_SAID = {}
_SAID_LOCK = threading.Lock()


def _said(ctx):
    with _SAID_LOCK:
        return _SAID.setdefault(ctx.data_dir, _Said())


def reset(ctx=None):
    """Forget what this process has said (a new process starts with nothing)."""
    with _SAID_LOCK:
        if ctx is None:
            _SAID.clear()
        else:
            _SAID.pop(ctx.data_dir, None)


def _once(ctx, key):
    s = _said(ctx)
    if key in s.keys:
        return False
    s.keys.add(key)
    return True


def _left_line(v, who):
    if v.key == 'file':
        return ("Claude Code lost its sign-in, and its login file still holds a login; Switchboard left it as it is")
    if v.key == 'switched':
        return ("Claude Code lost its sign-in, and its settings name %s, the account Switchboard just switched away "
                "from; Switchboard did not put it back" % who)
    if v.key == 'record':
        return ("Claude Code lost its sign-in, and Switchboard's note of earlier put-backs (%s) can't be read; it did "
                "not put %s back" % (STATE, who))
    return ('Claude Code lost its sign-in, and the saved login of %s %s; Switchboard did not put it back'
            % (who, dict(MARKS).get(v.key, v.reason)))


# ---------- one look ----------

def look(ctx):
    """One look for a lost sign-in, and a put-back when the rule says so. Returns api.PutBack."""
    if capture.in_runas_env(ctx):
        return PutBack('leave', reason='inside a `switchboard run` session')
    now = ctx.wall()
    seen, lr, saved, email = see(ctx, now)
    if seen.login == SPLIT and seen.live == PRESENT and not seen.degraded:
        line = None
        if _once(ctx, SPLIT):
            line = SPLIT_LINE
            ctx.log(line)
        return PutBack('leave', reason=SPLIT_LINE, line=line)
    rec = load_record(ctx)
    if not rec['readable'] and rec['why'] == 'unparseable' and _heal_record(ctx, now):
        rec = load_record(ctx)
    present = not is_missing(seen) and seen.live != UNREADABLE and not seen.degraded
    if present:
        reset(ctx)                                      # the login is there: an episode is over
    if not rec['readable']:
        if present:
            return PutBack('leave', reason='Claude Code has a login')
        v = decide(seen, None, [], now)                 # the rule still decides; only a write needs the record
        if v.action in ('put_back', 'wait'):
            v = Verdict('leave', v.account, 'the record of earlier put-backs cannot be read', key='record')
    else:
        v = decide(seen, rec['missing'], rec['done'], now, rec['failed'])
        if v.missing != rec['missing'] or (present and rec['failed']):
            # the login there again ends an episode: its failed writes no longer space the next one
            _change_record(ctx, lambda r: r.update(missing=v.missing, failed=[] if present else r['failed']), now)
    who = email or v.account
    if v.action == 'limit':
        if rec['limit_at'] is not None and 0 <= now - rec['limit_at'] < PUT_BACK_SPAN_S:
            return PutBack('limit', account=who, reason=v.reason)
        if not _change_record(ctx, lambda r: r.update(limit_at=now), now) and not _once(ctx, 'limit'):
            return PutBack('limit', account=who, reason=v.reason)
        ctx.say(LIMIT_WORDS, 'warn', h='problem', account=who, by='auto')
        return PutBack('limit', account=who, reason=v.reason, line=LIMIT_WORDS)
    if v.action == 'leave' and v.key:
        line = None
        if _once(ctx, v.key):
            line = _left_line(v, who)
            ctx.log(line)
        return PutBack('leave', account=who, reason=v.reason, line=line, watch=v.watch)
    if v.action != 'put_back':
        return PutBack(v.action, account=who, reason=v.reason, watch=v.watch)
    return _put_back(ctx, v.account, who, now)


def _tries(ctx, doc, aid, saved):
    """Under the locks: write the saved login until one write reads back, at most TRIES times within TRIES_WITHIN_S,
    each after a fresh read (interactive, so an item resting after a refused write is still read). ('landed', None),
    ('present' or 'changed', words) when there is nothing to write any more, or ('failed', words)."""
    start = ctx.mono()
    cur = live_mod.read_live(ctx, fresh=True, interactive=True)
    why = None
    for n in range(TRIES):
        if cur.read.kind == UNREADABLE or cur.read.degraded:
            return 'failed', "Claude Code's login can't be read (%s)" % (cur.read.why or 'a fallback copy')
        if not is_missing(Seen(live=cur.read.kind, login=_kind(cur.login))):
            return 'present', 'a login is there now'
        cfg_r, block, _ = live_mod.config_identity(ctx)
        if cfg_r.kind != PRESENT or identity.label_id(doc, block) != aid:
            return 'changed', "Claude Code's settings changed"
        # no live object at all: the saved copy is written whole, as a switch does (switcher._critical); an object
        # without a login keeps its machine-wide parts as they are now
        blob = cur.login.blob if cur.login is not None and isinstance(cur.login.blob, dict) else None
        composed = live_mod.dump(live_mod.compose(saved.blob, blob))
        done = live_mod.write_live(ctx, composed, interactive=True)
        cur = live_mod.read_live(ctx, fresh=True, interactive=True)
        if cur.usable and cur.login is not None and cur.login.text == composed:
            return 'landed', None
        why = ('Keychain: %s' % done.why) if not done.ok else 'it did not read back'
        if n + 1 >= TRIES or ctx.mono() - start >= TRIES_WITHIN_S:
            break
        ctx.sleep(_RAND.uniform(*TRY_GAP_S))
    return 'failed', why


def _put_back(ctx, aid, who, now):
    """The write, under the switch lock, the account's consume lock, the account lock and then Claude Code's two
    credential locks, in the switch's order (a chat that renews holds Claude Code's lock until it has saved, so it
    can't save between the last look and the write): everything is read and decided again there; with any account
    login in the item, nothing is written. When a lock is busy, nothing is written and the answer is 'wait'."""
    try:
        with vault.switch_lock(ctx, timeout=0.5), vault.consume_lock(ctx, aid, timeout=2.0), \
                vault.account_lock(ctx, timeout=5.0), \
                ccfiles.credential_locks(ctx.env, ctx.home, budget_s=CC_LOCKS_S, log=ctx.log):
            seen, _, saved, _ = see(ctx, now, interactive=True)
            rec = load_record(ctx)
            again = decide(seen, rec['missing'], rec['done'], now, rec['failed']) if rec['readable'] else None
            if again is None or again.action != 'put_back' or again.account != aid or saved is None:
                return PutBack('wait', account=who, reason='not now: %s' % (again.reason if again else 'no record'),
                               watch=bool(again and again.watch))
            outcome, why = _tries(ctx, vault.load(ctx), aid, saved)
            if outcome == 'landed':
                rec['done'].append(now)
                rec['missing'] = None
                rec['failed'] = []
                rec['limit_at'] = None                  # a limit reached again later is said again
                rec['last'] = {'at': now, 'account': who}
                try:
                    _save_record(ctx, rec, now)
                except (ccfiles.CCFileError, OSError) as e:
                    ctx.log('The put-back of %s was not noted (%s): it does not count toward the limit'
                            % (who, e.__class__.__name__))
            elif outcome == 'failed':
                rec['failed'].append(now)
                try:
                    _save_record(ctx, rec, now)
                except (ccfiles.CCFileError, OSError) as e:
                    ctx.log('A put-back that did not land was not noted (%s)' % e.__class__.__name__)
    except (vault.LockBusy, vault.VaultError, ccfiles.LockBusy, ccfiles.CCFileError, OSError) as e:
        ctx.log('Putting the sign-in back waits: %s' % e.__class__.__name__)
        return PutBack('wait', account=who, reason='busy', watch=True)
    if outcome in ('present', 'changed'):
        return PutBack('leave', account=who, reason=why)
    if outcome == 'failed':
        line = None
        if _once(ctx, 'failed'):
            line = ('Claude Code lost its sign-in, and putting %s back did not work (%s); it tries again, less often '
                    'each time' % (who, why))
            ctx.log(line)
        return PutBack('failed', account=who, reason=why, line=line, watch=True)
    reset(ctx)
    ctx.log('Put the saved login of %s back in Claude Code, which had lost its sign-in (%s)'
            % (who, saved.fingerprint or 'no refresh token'))
    line = 'Claude Code lost its sign-in; Switchboard put %s back' % who
    ctx.say(line, 'info', h='repair', account=who, by='auto')
    return PutBack('put_back', account=who, reason='its login was missing and its settings still named it', line=line,
                   watch=True, at=now)
