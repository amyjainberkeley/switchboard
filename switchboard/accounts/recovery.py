"""The recovery copies as a person sees them (`switchboard leftovers`): listed without secrets, each judged still
needed or not by one rule, and cleared through the vault's own Keychain helper and locks.

A recovery copy (vault.py, "Switchboard recovery", or an owner-only file under <data>/recovery when the Keychain
refused it) is a login that existed nowhere else when it was kept: a renewed login whose save failed, one Anthropic
said is another account's, one a `switchboard run` renewed ("successor:<account id>"), and the login in use that a
switch was about to write over ("safety:<fingerprint>"). None of them is any account's login in use.

needed() is the one rule for which copies must stay. Removing an account (roster._drop_recovery) keeps what it names,
and `switchboard leftovers` says it for each copy and never clears one it names. A copy is needed when:
- a switch that was cut off names it in its journal: it puts Claude Code's login back ('switch'); when the journal
  can't be read, every safety copy, since the journal may name any of them ('journal');
- it was set aside for the account Anthropic named as its owner, and that account still holds the login it replaces
  (tokens._held_by_a_slot), so it is that account's only working login ('only_login');
- it is a kept account's own successor to the login that account holds now: that saved login is spent, and the
  account's next renewal or switch takes this one (tokens._find_successor) ('only_login');
- the account list can't be read, so none of this can be told ('unknown'). A damaged list counts as one that can't be
  read here: it is restored from its last good copy by the next ordinary read, never by this one, so no copy is
  judged, or cleared, from an older copy of the list in the look that found the damage.

only_copies() adds one more reason, for the list and for clearing only (removing an account still keeps exactly what
needed() names): a safety copy that is the only copy of a login that was in use ('only_copy'). No saved account's
login is it (neither the one it holds nor one it renewed past), Claude Code's own login item is not it, and its
account, when known, has not saved a login since the copy was kept. Such a copy is cleared only when it is named by
id with only_copy=True (`switchboard leftovers clear ID --only-copy`).

A safety copy of a login that holds a token or a key and no refresh token (live.unrenewable: a switch no longer
copies one aside) is no account's sign-in, so a login its account saved since does not free it; unless Claude Code's
login item holds it, it is the only copy of that token ('only_copy', 'token' in its row). It is told by the mark
vault.stash wrote beside it, never by reading it. A copy marked as holding no token (an emptied login, MCP logins
only) is judged like every other copy. A copy with no refresh fingerprint and no mark was listed before the mark
existed and may be a token's: a login its account saved since does not free it either, but it is never called a
token.
"""
import hashlib
import os

from . import switcher, vault
from .api import PRESENT, UNREADABLE, AccountsError

ID_LEN = 6          # hex digits of a copy's id: sha256 of its recovery key; longer only when two would share one


def needed(ctx, doc, entries, journal=None):
    """{recovery key: (why, account id or None)} for every entry that must stay: why is 'switch', 'journal',
    'only_login' or 'unknown' (see the module's notes). doc None: the account list could not be read. journal: what
    switcher.read_journal said, read now when not given."""
    from . import tokens
    r, j = switcher.read_journal(ctx) if journal is None else journal
    pending = j.get('safety_key') if r.kind == PRESENT and isinstance(j, dict) else None
    out = {}
    for e in entries:
        key = e.get('key')
        if not isinstance(key, str):
            continue
        if key == pending:
            out[key] = ('switch', e.get('account_id'))
        elif r.kind == UNREADABLE and key.startswith('safety:'):
            out[key] = ('journal', e.get('account_id'))
        elif doc is None:
            out[key] = ('unknown', e.get('account_id'))
        elif e.get('names') and tokens._held_by_a_slot(doc, e):
            owner = next((oid for oid, o in doc['accounts'].items() if o.get('fp') == e.get('supersedes') and
                          o.get('uuid') == e.get('names')), None)
            out[key] = ('only_login', owner)
        elif e.get('reason') != tokens.CONFLICT and e.get('account_id') in doc['accounts'] and e.get('supersedes') and \
                e.get('supersedes') == doc['accounts'][e['account_id']].get('fp'):
            out[key] = ('only_login', e['account_id'])
    return out


def _claude_codes_login(ctx):
    """(marks, unsure): the fingerprints that name the login in Claude Code's own login item (its refresh token's,
    and its whole text's), and True when that item can't be read now. Never a secret."""
    from . import live as live_mod
    cur = live_mod.read_live(ctx, fresh=True, interactive=False)
    if cur.read.kind == UNREADABLE or cur.read.degraded:
        return frozenset(), True
    login = cur.login
    if login is None or login.kind == 'empty':
        return frozenset(), False
    return frozenset(m for m in (login.fingerprint, login.text_fp) if m), False


def only_copies(ctx, doc, entries, keep):
    """{recovery key: (account id or None, unsure)} for every safety copy needed() does not name (keep) that is the
    only copy of a login that was in use (see the module's notes). unsure: Claude Code's login item could not be
    read, so it can't be told whether that item holds it; it is kept all the same. Claude Code's item is read only
    when some copy is not already accounted for by a saved account."""
    if doc is None:
        return {}
    accounts = doc.get('accounts') or {}
    held = set()
    for a in accounts.values():
        held.update(f for f in [a.get('fp')] + list(a.get('previous_fps') or []) if isinstance(f, str) and f)
    cands = []
    for e in entries:
        key = e.get('key')
        if not isinstance(key, str) or not key.startswith('safety:') or key in keep:
            continue
        mark = e.get('fp') if isinstance(e.get('fp'), str) and e.get('fp') else key[len('safety:'):]
        if mark in held:
            continue
        a = accounts.get(e.get('account_id')) if isinstance(e.get('account_id'), str) else None
        at, saved = e.get('at'), (a or {}).get('saved_at')
        if a is not None and _no_token(e) and \
                all(isinstance(t, (int, float)) and not isinstance(t, bool) for t in (at, saved)) and saved > at:
            continue                        # its account saved a login since: the copy is not its only way in
        cands.append((key, mark, e.get('account_id') if isinstance(e.get('account_id'), str) else None))
    if not cands:
        return {}
    marks, unsure = _claude_codes_login(ctx)
    return dict((key, (aid, unsure)) for key, mark, aid in cands if mark not in marks)


def no_refresh(e):
    """A safety copy of a login that held a token or a key and no refresh token, such as a one-year token: marked so
    when it was made (vault.stash). Nothing else counts, so a copy is never read to tell."""
    return isinstance(e.get('key'), str) and e['key'].startswith('safety:') and e.get('token') is True


def _no_token(e):
    """A copy known to hold no token: a sign-in's (it has a refresh fingerprint), or one marked so when it was made.
    A copy with neither was listed before the mark existed and may be a token's, so it can't be told without a read."""
    return bool(e.get('fp')) or e.get('token') is False


def _email(doc, aid):
    for part in ('accounts', 'removed'):
        a = (doc or {}).get(part, {}).get(aid) if aid else None
        if isinstance(a, dict) and a.get('email'):
            return a['email']
    return None


def why_kept(e):
    """Why a copy was kept, in a few plain words, from the reason it was kept with."""
    from . import tokens
    r = e.get('reason') if isinstance(e.get('reason'), str) else ''
    if r == tokens.CONFLICT:
        return "a renewed login Anthropic said is another account's"
    if r.startswith('before switching to '):
        return '%s in use before a switch to %s' % ('a token with no refresh token,' if no_refresh(e) else
                                                    'the login', r[len('before switching to '):])
    for start, words in (('save failed', 'a renewed login that could not be saved'),
                         ('removed while refreshing', 'a renewed login of an account removed as it renewed'),
                         ('replaced while refreshing', 'a renewed login of an account given another login meanwhile'),
                         ('saved login unreadable', "a renewed login: the saved one couldn't be read then"),
                         ('store busy', 'a renewed login: the account list was busy then'),
                         ('renewed by a run', 'a login a switchboard run renewed')):
        if r.startswith(start):
            return words
    return 'a login kept in case it was needed again'


def _ids(keys):
    for n in (ID_LEN, 12, 64):
        ids = dict((k, hashlib.sha256(k.encode('utf-8')).hexdigest()[:n]) for k in keys)
        if len(set(ids.values())) == len(ids):
            return ids
    return ids


def listing(ctx, doc=None, entries=None):
    """Every recovery copy, oldest first, with no secret: [{'id', 'key', 'account_id', 'email', 'why', 'at', 'where',
    'needed', 'need', 'needed_by', 'unsure'}]. need is needed()'s why, or 'only_copy' (only_copies()); needed_by the
    email of the account that needs it; unsure (only_copy only): Claude Code's login item could not be read; token:
    the copy is of a login with no refresh token (no_refresh)."""
    if doc is None:
        try:
            doc = vault.load(ctx, restore_it=False)     # a damaged list is 'unknown' here, never an older copy of it
        except vault.VaultError:
            doc = None
    entries = [e for e in (vault.stashed(ctx) if entries is None else entries) if isinstance(e.get('key'), str)]
    keep = needed(ctx, doc, entries)
    only = only_copies(ctx, doc, entries, keep)
    ids = _ids([e['key'] for e in entries])
    out = []
    for e in entries:
        need, who = keep.get(e['key'], (None, None))
        unsure = False
        if need is None and e['key'] in only:
            need, (who, unsure) = 'only_copy', only[e['key']]
        at = e.get('at') if isinstance(e.get('at'), (int, float)) and not isinstance(e.get('at'), bool) else None
        out.append({'id': ids[e['key']], 'key': e['key'], 'account_id': e.get('account_id'),
                    'email': _email(doc, e.get('account_id')), 'why': why_kept(e), 'at': at,
                    'where': 'keychain' if e.get('where') == 'keychain' else 'file', 'needed': need is not None,
                    'need': need, 'needed_by': _email(doc, who) if need in ('only_login', 'only_copy') else None,
                    'unsure': unsure, 'token': no_refresh(e)})
    out.sort(key=lambda x: (x['at'] is None, x['at'] or 0, x['id']))
    return out


def _pick(rows, ids):
    """The rows named by ids (a copy's id in any case, or its recovery key), and the ids that name none."""
    picked, unknown = [], []
    for want in ids:
        w = want.strip() if isinstance(want, str) else ''
        hit = next((x for x in rows if w and (x['id'] == w.lower() or x['key'] == w)), None)
        if hit is None:
            unknown.append(want)
        elif hit not in picked:
            picked.append(hit)
    return picked, unknown


def clear(ctx, ids=(), everything=False, only_copy=False):
    """Remove recovery copies that nothing needs, under the switch and account locks, so nothing a switch or a renewal
    is doing can change what they are needed for meanwhile: each is judged again there. Named by `ids`, or every one
    (`everything`). only_copy: a copy named by id whose one need is that it is the only copy of a login that was in
    use is cleared too (the person gave that login up); it never widens `everything`. Returns {'cleared': [rows],
    'kept': [rows still needed], 'failed': [(row, why)], 'unknown': [ids]}. Nothing is cleared when an id names no
    copy."""
    from . import roster, uninstall
    roster._refuse_inside_run(ctx)
    res = {'cleared': [], 'kept': [], 'failed': [], 'unknown': []}
    try:
        with vault.switch_lock(ctx, timeout=30.0), vault.account_lock(ctx, timeout=10.0):
            try:
                doc = vault.load(ctx, restore_it=False)   # never judged from an older copy of a damaged list
            except vault.VaultError:
                doc = None                              # every copy is then 'unknown', so none is cleared
            rows = listing(ctx, doc)
            picked, res['unknown'] = (rows, []) if everything else _pick(rows, list(ids))
            if res['unknown']:
                return res
            for row in picked:
                if row['needed'] and not (only_copy and not everything and row['need'] == 'only_copy'):
                    res['kept'].append(row)
                    continue
                _, why = uninstall._take_out(ctx, vault.SERVICE_RECOVERY, row['key'])
                if why:
                    res['failed'].append((row, why))
                    continue
                vault.drop_stash(ctx, row['key'])           # its file, and its line in the manifest
                path = os.path.join(vault._recovery_dir(ctx), vault._safe(row['key']) + '.json')
                if os.path.exists(path):
                    res['failed'].append((row, 'file'))
                else:
                    res['cleared'].append(row)
    except vault.LockBusy:
        raise AccountsError('other', 'Switchboard is busy; try again in a moment. Nothing was cleared.')
    if res['cleared']:
        ctx.log('Cleared %d recovery cop%s: %s' % (len(res['cleared']), 'y' if len(res['cleared']) == 1 else 'ies',
                                                     ', '.join(r['id'] for r in res['cleared'])))
    return res

