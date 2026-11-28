"""The plan the chats run by: which model families have room on the account Claude Code is on, and what comes next.

Files, all under <data>/running/ (owner-only, JSON, written whole by a rename; a missing, unreadable or wrong-version
file is "nothing known" to every reader, and no reader fails because of one):

- room.json, the plan, written here (build(), then write()) at the end of every pass of the automatic loop and at
  once after a switch. For each family (fable, opus, sonnet, haiku): can a request for it be served now on the
  account in use ('room'), its own window's percent ('pct'), when what blocks it refills ('until'), and the first
  family below it with room ('use'). Then whether nothing has room ('out', 'out_until'), the loop's last pass reached
  nothing ('offline'), Claude Code's sign-in ('signin'), a switch under way ('switching'), the loop's next move
  ('next') and one or two plain sentences for the page and the status text ('words', None when everything runs as
  asked).
- wants/<session id>.json, what a chat asks for: {'v': 1, 'at', 'session', 'asked', 'sent', 'agent'}. read_wants()
  counts a family as asked for while a file under WANT_KEEP names it, and removes files older than WANT_DROP.
- refused.json, refusals seen inside chats: {'v': 1, 'by_account': {account id or email or '?': {family: {'at',
  'until', 'kind'}}}}. An entry counts for REFUSED_FRESH seconds, or until 'until' while the same account is in use.
- put-back.json, read only: when Claude Code's sign-in was last put back ('wrote_at', while 'missing' is None).
- on.json, read only: {'v': 1, 'on': true, ...} while the chats' plugin is turned on. The plan is written only then:
  its 'use' says what the plugin sends in place of a family, which is untrue while nothing sends it.

A family has no room when the account's 5-hour or weekly window is at STEP_DOWN_AT, its own weekly window is, or a
request for it was refused there in the last REFUSED_FRESH seconds (refused.json, or a limit notice in a chat that
names its window). Numbers older than NUMBERS_FRESH and numbers not known count as room. Once without room, a family
gets room back only when the account changes, the blocking window's reset has passed, or a fresh number shows every
window that could block it at STEP_UP_AT or under: so a window at 99, 100, 99, 100 never flips the chats' model back
and forth (each change of model re-reads a whole chat without its cache).
"""
import os

from . import core
from .accounts import policy, usage

STEP_DOWN_AT = 100.0         # a family steps down when a window that counts for it is at this percent
STEP_UP_AT = 95.0            # and steps back up once a fresh number shows every such window at this or under
REFUSED_FRESH = 600.0
NUMBERS_FRESH = 900.0
WANT_KEEP = 6 * 3600.0
WANT_DROP = 7 * 86400.0
MAX_WANTS = 4096             # want files looked at in one read (one per open chat; more is said in the log)
V = 1
FOLDER = 'running'
ROOM, WANTS, REFUSED, PUT_BACK, ON = 'room.json', 'wants', 'refused.json', 'put-back.json', 'on.json'
KINDS = {'forced': 'forced', 'model': 'model', 'sign': 'sign', 'optional': 'optional'}


def folder():
    return core.data_path(FOLDER)


def _num(x):
    return usage.num(x)


# ---------- reading what the chats write ----------

def read_wants(now, where=None, drop=True):
    """{family: the newest time a chat asked for it} from wants/*.json written in the last WANT_KEEP seconds. Files
    older than WANT_DROP are removed when drop. Never raises."""
    d = os.path.join(where or folder(), WANTS)
    out = {}
    try:
        with os.scandir(d) as it:
            entries = [e for _, e in zip(range(MAX_WANTS + 1), (e for e in it if e.name.endswith('.json')))]
    except OSError:
        return out
    if len(entries) > MAX_WANTS:
        entries = entries[:MAX_WANTS]
        core.log('room: more than %d files in wants/; the rest were not read this pass' % MAX_WANTS)
    for e in entries:
        try:
            mtime = e.stat(follow_symlinks=False).st_mtime
        except OSError:
            continue
        if drop and now - mtime > WANT_DROP:
            try:
                os.unlink(e.path)
            except OSError:
                pass
            continue
        rec = usage.read_json(e.path)
        if not isinstance(rec, dict) or rec.get('v') != V or rec.get('agent') is True:
            continue
        at, fam = _num(rec.get('at')), rec.get('asked')
        if at is None or fam not in policy.FAMILIES or not -usage.AHEAD <= now - at <= WANT_KEEP:
            continue
        out[fam] = max(at, out.get(fam, at))
    return out


def read_refused(where=None):
    """refused.json's 'by_account' ({key: {family: {'at', 'until', 'kind'}}}), {} when it is missing or not usable."""
    rec = usage.read_json(os.path.join(where or folder(), REFUSED))
    if not isinstance(rec, dict) or rec.get('v') != V or not isinstance(rec.get('by_account'), dict):
        return {}
    return rec['by_account']


def refused_families(by_account, account, now, in_use=True, since=None):
    """{family: until} refused on this account now (an api.Account, or None for the account in use when it is not
    known): an entry under its id, its email, or '?' (for the account in use only, and only when written after
    `since`, the time Claude Code's login became this account, when that is known) counts for REFUSED_FRESH seconds,
    or until its 'until' while the same account is in use. until: when it lapses."""
    keys = ([account.id, account.email] if account is not None else []) + (['?'] if in_use else [])
    since = _num(since)
    out = {}
    for key in keys:
        fams = by_account.get(key) if isinstance(key, str) else None
        if not isinstance(fams, dict):
            continue
        for fam, e in fams.items():
            if fam not in policy.FAMILIES or not isinstance(e, dict):
                continue
            at, until = _num(e.get('at')), _num(e.get('until'))
            if at is None or at > now + usage.AHEAD:
                continue
            if key == '?' and since is not None and at < since:
                continue                                # written for the account before this one
            ends = at + REFUSED_FRESH
            if in_use and key != '?' and until is not None and until > ends:
                ends = until
            if ends > now:
                out[fam] = max(ends, out.get(fam, ends))
    return out


def read_put_back(where=None):
    """(signed in as far as the put-back knows, when a lost sign-in was last put back or None)."""
    rec = usage.read_json(os.path.join(where or core.data_dir(), PUT_BACK))
    if not isinstance(rec, dict):
        return True, None
    missing = rec.get('missing')
    if isinstance(missing, list) and missing:
        return False, None
    return True, _num(rec.get('wrote_at'))


def is_on(where=None):
    """The chats' plugin is turned on (on.json says so). Missing or unreadable: off."""
    rec = usage.read_json(os.path.join(where or folder(), ON))
    return isinstance(rec, dict) and rec.get('v') == V and rec.get('on') is True


def read_plan(where=None):
    """The last plan written, or None."""
    rec = usage.read_json(os.path.join(where or folder(), ROOM))
    return rec if isinstance(rec, dict) and rec.get('v') == V else None


# ---------- the plan ----------

def _fresh_pct(w, reading, now):
    """A window's percent when it was read in the last NUMBERS_FRESH seconds (and not after now), else None."""
    at = w.read_at if w.read_at is not None else (reading.read_at if reading else None)
    if w.pct is None or at is None or at > now + usage.AHEAD or now - at > NUMBERS_FRESH:
        return None
    return float(w.pct)


def _windows(reading, now):
    """{'5-hour': (pct, reset), 'weekly': ..., family: ...} of fresh numbers on the account in use."""
    out = {}
    for w in (reading.windows if reading else ()):
        pct = _fresh_pct(w, reading, now)
        if w.name in (policy.FIVE, policy.WEEK):
            out[w.name] = (pct, w.resets_at)
        elif isinstance(w.name, str) and w.name.startswith(policy.MODEL) and policy.family_of(w.name):
            f = policy.family_of(w.name)
            if f not in out or (pct is not None and (out[f][0] is None or pct > out[f][0])):
                out[f] = (pct, w.resets_at)
    return out


def _own_pct(reading, f):
    """A family's own window percent as last read (any age), None when it has none or it is not known."""
    best = None
    for w in (reading.windows if reading else ()):
        if isinstance(w.name, str) and w.name.startswith(policy.MODEL) and policy.family_of(w.name) == f \
                and w.pct is not None and (best is None or w.pct > best):
            best = float(w.pct)
    return best


def _blocked(f, wins, refused, all_refused, now):
    """[(what, until)] of what blocks family f now."""
    out = []
    for name in (policy.FIVE, policy.WEEK, f):
        pct, reset = wins.get(name, (None, None))
        if pct is not None and pct >= STEP_DOWN_AT:
            out.append((name, reset if reset is not None and reset > now else None))
    if f in refused:
        out.append(('refused', refused[f]))
    elif all_refused is not None:
        out.append(('refused', all_refused))
    return out


def _back_up(f, prev, account_id, wins, now):
    """A family without room in the last plan gets it back (no flapping): the account changed, the blocking window's
    reset has passed, or a fresh number shows every window that can block it at STEP_UP_AT or under. Old numbers do
    not give it back while the block's reset is still ahead (they would flip the chats' model back and forth on each
    late read); with no reset known, unknown counts as room (fail open)."""
    if not isinstance(prev, dict) or not isinstance(prev.get('models'), dict):
        return True
    was = prev['models'].get(f)
    if not isinstance(was, dict) or was.get('room') is not False:
        return True
    acct = prev.get('account') if isinstance(prev.get('account'), dict) else {}
    if acct.get('id') != account_id:
        return True
    until = _num(was.get('until'))
    if until is not None and until <= now:
        return True
    present = [n for n in (policy.FIVE, policy.WEEK, f) if n in wins]
    known = [wins[n][0] for n in present if wins[n][0] is not None]
    if not present or len(known) < len(present):
        return until is None
    return max(known) <= STEP_UP_AT


def build(s, decision, refused=None, put_back=(True, None), prev=None, switching=False, since=None):
    """ROOM-CONTRACT file 1 from a policy.Snapshot, its Decision, refused.json's 'by_account' (read_refused), the
    put-back's (signed in, back at) (read_put_back) and the last plan (for the step-up rule). since: when Claude
    Code's login became the account in use (a '?' refusal from before it is not this account's). Pure."""
    now = s.now
    by_id = dict((a.id, a) for a in s.accounts)
    active = by_id.get(s.active) if s.active else None
    reading = s.readings.get(s.active) if s.active else None
    wins = _windows(reading, now)
    ref = refused_families(refused or {}, active, now, since=since)
    for f in (s.refused or {}).get(s.active, ()) if s.active else ():
        ref.setdefault(f, now + REFUSED_FRESH)          # a notice in a chat: when it lapses is not said
    all_refused = now + REFUSED_FRESH if s.rate_limited or s.live_refused else None
    models = {}
    for f in policy.FAMILIES:
        blocks = _blocked(f, wins, ref, all_refused, now)
        room = not blocks and _back_up(f, prev, s.active, wins, now)
        until = None
        if blocks:
            times = [t for _, t in blocks]
            until = max(times) if all(t is not None for t in times) else None
        elif not room:
            until = _num(((prev or {}).get('models') or {}).get(f, {}).get('until'))
        models[f] = {'room': room, 'pct': _own_pct(reading, f), 'until': until, 'use': None}
    for i, f in enumerate(policy.FAMILIES):
        if not models[f]['room']:
            models[f]['use'] = next((g for g in policy.FAMILIES[i + 1:] if models[g]['room']), None)
    out = not any(m['room'] for m in models.values())
    untils = [m['until'] for m in models.values() if m['until'] is not None]
    signed = put_back[0] and not s.lost and not s.live_refused and not (active is not None and
                                                                        active.status == 'needs_login')
    plan = {
        'v': V, 'at': round(now, 3),
        'account': {'id': active.id, 'email': active.email} if active is not None else None,
        'models': models, 'out': out, 'out_until': min(untils) if out and untils else None,
        'offline': bool(s.offline), 'signin': {'ok': bool(signed), 'back_at': put_back[1] if signed else None},
        'switching': bool(switching), 'next': _next(s, decision, by_id),
    }
    plan['words'] = words(plan, s, decision, refused=frozenset(ref),
                          label=policy.label_of(s, active) if active else None)
    return plan


def _her_pick(d):
    return d is not None and d.action != 'switch' and bool(d.hold) and d.hold.startswith('you picked ')


def _next(s, d, by_id):
    if d is None or not d.target or (d.kind not in KINDS and policy.stay_why(s) is None):
        return None
    if _her_pick(d):
        return None             # a pick by hand holds: no move is planned while it does (the words say until when)
    t = by_id.get(d.target)
    kind = 'signin' if policy.stay_why(s) is not None else KINDS[d.kind]
    if d.action == 'switch':
        when = 'now'
    elif d.hold and ('working' in d.hold or 'quiet minutes' in d.hold):
        when = 'quiet'
    else:
        when = 'calm'
    return {'to': policy.label_of(s, t) if t else d.target, 'kind': kind, 'when': when, 'why': d.reason or None}


def words(plan, s, decision, refused=frozenset(), label=None):
    """One or two plain sentences for the page and the status text, or None when everything runs as asked.
    refused: the families refused on the account in use now; label: that account in words (policy.label_of)."""
    acct = label or (plan.get('account') or {}).get('email') or (plan.get('account') or {}).get('id')
    if not plan['signin']['ok']:
        return 'Claude Code is signed out of %s.' % acct if acct else 'Claude Code is not signed in.'
    if plan['offline']:
        return 'No connection to Anthropic; Claude Code stays where it is.'
    if plan['out']:
        if decision is not None and decision.hold == 'every account is out':
            return decision.reason
        when = plan['out_until']
        return 'Nothing has room on %s%s.' % (acct, ' until %s' % policy._clock(when, s.now) if when else '')
    asked = policy.asked_families(s)
    nxt = plan.get('next')
    for f in asked:
        m = plan['models'][f]
        if m['room']:
            continue
        until = ' until %s' % policy._clock(m['until'], s.now) if m['until'] else ''
        # held down by the step-up rule (it was at STEP_DOWN_AT and is not yet back at STEP_UP_AT): not "used up"
        used = f in refused or m['pct'] is None or m['pct'] >= STEP_DOWN_AT
        if used:
            head = '%s is used up on %s%s.' % (policy.family_words(f), acct, until)
        else:
            head = '%s is at %s%% on %s.' % (policy.family_words(f), policy._pct(m['pct']), acct)
        if not m['use']:
            return head
        use = policy.family_words(m['use'])
        if _her_pick(decision):
            stays = decision.hold.split('; it stays ', 1)[1] if '; it stays ' in decision.hold else ''
            return '%s Chats run on %s: you picked %s, so Claude Code stays there%s.' % (
                head, use, acct, ' ' + stays if stays else '')
        if nxt and nxt.get('kind') == 'model':
            return '%s Chats run on %s until Switchboard moves to %s.' % (head, use, nxt['to'])
        return '%s Chats run on %s%s.' % (head, use, ' until then' if used and until else '')
    return None


def write(plan, where=None):
    """Put the plan in place: an owner-only file renamed over the old one, in an owner-only folder. True when it
    landed; never raises."""
    d = where or folder()
    try:
        os.makedirs(d, mode=0o700, exist_ok=True)
        usage.write_atomic(os.path.join(d, ROOM), plan)
        return True
    except (OSError, ValueError, TypeError) as e:
        core.log('room: the plan could not be written (%s)' % type(e).__name__)
        return False
