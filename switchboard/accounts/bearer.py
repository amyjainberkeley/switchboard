"""A one-year token or an API key kept as an account: `switchboard add --token`.

Two kinds, told apart by their shape only (adding sends nothing to Anthropic):
- 'token': what `claude setup-token` prints, an OAuth access token (`sk-ant-oat...`). Anthropic's pages, read on
  2026-11-25: it "can only make model requests, so it can't establish Remote Control sessions or fetch claude.ai
  connectors" (code.claude.com/docs/en/authentication). It is billed to the subscription that made it.
- 'key': an API key from the Claude Console (`sk-ant-api...`, platform.claude.com/docs/en/manage-claude/
  authentication), billed per token by Anthropic.

Where things are kept:
- The secret: one Keychain item per record, service SERVICES[kind], account = the record's id, written and read back
  through the Keychain wrapper (its value goes on `security`'s standard input, never in its arguments).
- The record, in accounts.json under the top-level key 'bearers' (no secret in it): {'id', 'kind', 'for' (the id of
  the kept account it belongs to, or None), 'label' (its own name when it belongs to no kept account), 'alias' (a
  short name to type), 'added_at' (when it was kept; the token carries no end date of its own), 'last4', 'fp' (the
  first 16 hex of its SHA-256, to know it again in Claude Code's login item), 'rule'}.

Auto-switch never moves Claude Code onto one by itself: each starts with the rule 'out'. A token may be given 'last'.
A key is always 'out' (it is billed per token), whatever its record says.

Putting a token in Claude Code's login item happens inside switcher.switch's transaction (login_text gives the
bytes). Claude Code 2.1.331 (read, not run) reads a stored login with an access token and no refresh token, uses it
while its expiresAt is not within 5 minutes (null: always), and never tries to renew it.
"""
import hashlib
import hmac
import re
import secrets

from . import ccfiles, live as live_mod, vault
from .api import PRESENT, UNREADABLE, AccountsError

TOKEN, KEY = 'token', 'key'
KINDS = (TOKEN, KEY)
SERVICES = {TOKEN: 'Switchboard token', KEY: 'Switchboard API key'}
PREFIX = {TOKEN: 'sk-ant-oat', KEY: 'sk-ant-api'}
_REST = re.compile(r'[A-Za-z0-9_-]{16,500}\Z')      # what follows the prefix: plain characters, nothing to split on
_NAME = re.compile(r'[a-z0-9][a-z0-9._-]{0,39}\Z')
SCOPES = ('user:inference',)                         # what a token from `claude setup-token` is issued with
RULES = {TOKEN: ('out', 'last'), KEY: ('out',)}
WHAT = {TOKEN: 'one-year token', KEY: 'API key'}
A_WHAT = {TOKEN: 'a one-year token', KEY: 'an API key'}
CANNOT = {TOKEN: 'model requests only; its limits are not known',
          KEY: 'billed per token by Anthropic'}
AFTER_SWITCH = {TOKEN: 'On a one-year token, claude.ai connectors and Remote Control do not work; model requests do.'}

REFUSED = ('That is neither a one-year token from `claude setup-token` (it starts sk-ant-oat) nor an API key from the '
           'Claude Console (it starts sk-ant-api). Nothing was kept.')
KEY_FOR = ('An API key is billed to its Console organization, not to one of your Claude accounts: give it a name with '
           '--label WORD.')
KEY_SWITCH = ('%s is an API key, billed per token by Anthropic, so Switchboard does not move Claude Code onto it. To '
              'use it in one terminal only, keep it for the gateway too (switchboard keys add NAME), turn the gateway '
              'on, then run: switchboard claude')
NAME_IT ='Say which account it is for (--for NAME), or give it a name of its own (--label WORD).'
NAME_WORDS = 'A name is 1 to 40 letters, digits, dots, dashes and underscores, not only digits.'
IN_USE = 'Claude Code is on the one-year token for %s; switch to another account first, then %s. Nothing was %s.'
OLDER_COPY = ("macOS didn't answer for Claude Code's login in the Keychain and only an older copy could be read, so "
              "Switchboard can't tell whether Claude Code is on that one-year token. Nothing was %s; try again in a "
              "minute.")
FORGET_UNWRITTEN = ("The one-year token kept for %s was not removed: the list of accounts couldn't be written (%s). "
                    "It is still kept, as %s; to remove it: switchboard remove %s")
FORGET_UNLISTED = ("The one-year token kept for %s is no longer listed, but it stays in your Keychain (item "
                   "\"Switchboard token\", account %s): the Keychain refused to remove it, and the list of accounts "
                   "couldn't be written (%s).")


def kind_of(text):
    """'token', 'key', or None for anything else (a refresh token, a sign-in's whole login, a typo)."""
    if not isinstance(text, str):
        return None
    t = text.strip()
    for kind in KINDS:
        if t.startswith(PREFIX[kind]) and _REST.match(t[len(PREFIX[kind]):]):
            return kind
    return None


def fingerprint(secret):
    return live_mod.fingerprint(secret.strip()) if isinstance(secret, str) else None


def records(doc):
    """{id: record} of every kept token and key; a record that is not whole is left out."""
    got = doc.get('bearers')
    if not isinstance(got, dict):
        return {}
    return dict((k, v) for k, v in got.items()
                if isinstance(k, str) and isinstance(v, dict) and v.get('kind') in KINDS and v.get('id') == k)


def rule_of(rec):
    """The rule auto-switch follows for a record: a key is always 'out'; a token 'last' when it was given that."""
    if rec.get('kind') == TOKEN and rec.get('rule') in RULES[TOKEN]:
        return rec['rule']
    return 'out'


def name_of(doc, rec):
    """What a record is called, as every surface prints it: the name of the account it is for (its whole email, or
    its email and organization when another kept account has that email, names.in_doc), else its own label. A list
    edited by hand may hold anything, so it is cleaned for printing (outside.said)."""
    from .. import names, outside
    a = doc['accounts'].get(rec.get('for')) if isinstance(rec.get('for'), str) else None
    if a is not None:
        return outside.said(names.in_doc(doc, rec['for']) or rec['for'])
    return outside.said(rec.get('label') if isinstance(rec.get('label'), str) and rec.get('label') else rec['id'])


def _s(v):
    """A word from a record, lower case, or '' (a hand-edited list may hold anything)."""
    return v.lower() if isinstance(v, str) else ''


def find_id(doc, name):
    """The id of the record called `name` (its id, short name or label), or None."""
    t = name.strip().lower() if isinstance(name, str) else ''
    if not t:
        return None
    recs = records(doc)
    if t in recs:
        return t
    for field in ('alias', 'label'):
        for bid, rec in recs.items():
            if _s(rec.get(field)) == t:
                return bid
    return None


def fallback_token(doc, account_id):
    """The one-year token kept for a kept account, or None. Where using it once every sign-in has ended would plug in;
    nothing calls it for that yet."""
    for rec in records(doc).values():
        if rec['kind'] == TOKEN and rec.get('for') == account_id:
            return rec
    return None


def owns(doc, login):
    """The id of the kept token that is this login (Claude Code's login item holding a token Switchboard put there),
    or None. Known by its access token's fingerprint; a login with a refresh token is a sign-in, never a token."""
    if login is None or login.kind != 'oauth' or login.has_refresh or not login.access_fp:
        return None
    for bid, rec in records(doc).items():
        if rec['kind'] == TOKEN and rec.get('fp') == login.access_fp:
            return bid
    return None


def refuse_in_use(ctx, doc, bids, then, nothing):
    """Called holding the switch lock, before a kept token's record goes or changes: when Claude Code's login item
    holds one of the tokens `bids` names, refuse, so the next switch away still knows the login is a kept token. A
    login item that can't be read is refused too (whether it holds one can't be told), and so is one only its older
    file copy could be read for: that file may hold a login Claude Code has since replaced."""
    if not any(records(doc).get(b, {}).get('kind') == TOKEN for b in bids):
        return
    pre = live_mod.read_live(ctx, fresh=True, interactive=True)
    if pre.read.kind == UNREADABLE:
        from .switcher import unreadable_error
        raise unreadable_error(pre.read)
    if pre.read.degraded:
        raise AccountsError('unreadable_live', OLDER_COPY % nothing)
    held = owns(doc, pre.login)
    if held is not None and held in bids:
        raise AccountsError('other', IN_USE % (name_of(doc, records(doc)[held]), then, nothing))


def login_text(token):
    """What Claude Code's login item holds for a token: the shape Claude Code gives a token it is handed by
    itself (no refresh token, no end time, the inference scope)."""
    return live_mod.dump({'claudeAiOauth': {'accessToken': token.strip(), 'refreshToken': None, 'expiresAt': None,
                                            'scopes': list(SCOPES), 'subscriptionType': None, 'rateLimitTier': None}})


def secret(ctx, bid, kind):
    """The kept secret as a Read, from its own Keychain item (fresh). Its shape is checked: anything else is
    unreadable, why 'unparseable'. The caller never logs or shows it."""
    r = ctx.keychain.read(SERVICES[kind], bid, fresh=True, interactive=True)
    if r.kind == PRESENT and kind_of(r.value) != kind:
        from .api import Read, UNREADABLE
        return Read(UNREADABLE, why='unparseable')
    return r


def _same(a, b):
    return hmac.compare_digest(hashlib.sha256(a.encode('utf-8')).digest(), hashlib.sha256(b.encode('utf-8')).digest())


def _taken(doc, word, mine=None):
    """Who already answers to `word` (an account's number, id, email or short name, or another record's), or None."""
    if word.isdigit():
        return 'an account number'
    for aid, a in doc['accounts'].items():
        if word in (aid.lower(), (a.get('email') or '').lower(), (a.get('alias') or '').lower()):
            return a.get('email') or aid
    for bid, rec in records(doc).items():
        if bid != mine and word in (bid, _s(rec.get('alias')), _s(rec.get('label'))):
            return rec.get('label') or name_of(doc, rec)
    return None


def _word(value):
    if value is None:
        return None
    w = str(value).strip().lower()
    if not _NAME.match(w) or w.isdigit():
        raise AccountsError('other', NAME_WORDS)
    return w


def add(ctx, text, for_id=None, label=None, alias=None):
    """Keep a token or a key as an account. for_id: the kept account it is for (a token only); else label, its own
    name. alias: a short name to type. A second token for the same account takes the place of the first. Returns
    (record without secret, what happened: 'added', 'replaced' or 'same'). Sends nothing to Anthropic."""
    kind = kind_of(text)
    if kind is None:
        raise AccountsError('other', REFUSED)
    value = text.strip()
    if kind == KEY and for_id:
        raise AccountsError('other', KEY_FOR)
    label, alias = _word(label), _word(alias)
    if not for_id and not label:
        raise AccountsError('other', NAME_IT)
    if for_id:
        label = None
    try:
        with vault.switch_lock(ctx, timeout=10.0), vault.account_lock(ctx, timeout=10.0):
            doc = vault.load(ctx)
            if for_id and for_id not in doc['accounts']:
                raise AccountsError('not_found', 'That account was removed meanwhile; nothing was kept.')
            recs = records(doc)
            fp = fingerprint(value)
            old = next((r for r in recs.values() if r.get('fp') == fp), None) or \
                (fallback_token(doc, for_id) if for_id and kind == TOKEN else None)
            mine = old['id'] if old else None
            for word in (label, alias):
                who = _taken(doc, word, mine) if word else None
                if who:
                    raise AccountsError('other', '"%s" is already %s; choose another name.'
                                        % (word, who if who == 'an account number' else 'the name of ' + who))
            if old is not None and old.get('fp') == fp and old.get('kind') == kind and old.get('for') == for_id \
                    and (label is None or old.get('label') == label) and (alias is None or old.get('alias') == alias):
                return dict(old), 'same'
            if old is not None and old.get('fp') != fp:
                refuse_in_use(ctx, doc, [old['id']], 'keep the new one', 'kept')
            bid = mine or '%s-%s' % (kind, secrets.token_hex(4))
            before = secret(ctx, bid, kind) if mine else None
            done = ctx.keychain.write(SERVICES[kind], bid, value, interactive=True)
            back = ctx.keychain.read(SERVICES[kind], bid, fresh=True, interactive=True) if done.ok else None
            if back is None or back.kind != PRESENT or not _same(back.value or '', value):
                _put_back(ctx, kind, bid, before)
                from .switcher import keychain_refusal
                raise keychain_refusal(done.why if not done.ok else 'verify',
                                       "The Keychain did not keep the %s exactly (%s), so nothing was kept."
                                       % (WHAT[kind], done.why or 'it read back different'))
            rec = {'id': bid, 'kind': kind, 'for': for_id, 'label': label,
                   'alias': alias if alias is not None else (old or {}).get('alias'),
                   'added_at': ctx.wall(), 'last4': value[-4:], 'fp': fp, 'rule': rule_of(old or {})}
            doc['bearers'] = dict(recs, **{bid: rec})
            try:
                vault.save(ctx, doc)
            except OSError as e:
                _put_back(ctx, kind, bid, before)
                raise AccountsError('disk' if getattr(e, 'errno', None) == 28 else 'other',
                                    "Couldn't write the list of accounts (%s), so nothing was kept."
                                    % e.__class__.__name__)
    except vault.LockBusy:
        raise AccountsError('other', 'Switchboard is busy; try again in a moment. Nothing was kept.')
    except vault.VaultError as e:
        raise AccountsError('other', str(e))
    value = None
    who = name_of(doc, rec)
    ctx.log('Kept %s for %s (%s)' % (A_WHAT[kind], who, bid))
    ctx.say('Kept %s for %s' % (A_WHAT[kind], who), 'info', h='added', account=who, by='you')
    return dict(rec), 'replaced' if mine else 'added'


def _put_back(ctx, kind, bid, before):
    """After a write that did not hold: the item as it was (the earlier secret, or no item)."""
    if before is not None and before.kind == PRESENT:
        ctx.keychain.write(SERVICES[kind], bid, before.value, interactive=True)
    else:
        ctx.keychain.delete(SERVICES[kind], bid)


def remove(ctx, bid, even_in_use=False):
    """Forget a kept token or key: its record, then its Keychain item (when the Keychain refuses, the record comes
    back as it was). Claude Code's login item is never touched, so a token Claude Code is on is refused (switch away
    first), except for the uninstaller's --all (even_in_use), after which nothing switches. Returns what it was
    called."""
    try:
        with vault.switch_lock(ctx, timeout=30.0), vault.account_lock(ctx, timeout=10.0):
            doc = vault.load(ctx)
            if not even_in_use:
                refuse_in_use(ctx, doc, [bid], 'remove it', 'removed')
            rec = records(doc).get(bid)
            if rec is None:
                raise AccountsError('not_found', 'That was already removed.')
            who = name_of(doc, rec)
            _drop(ctx, doc, bid, who)
    except vault.LockBusy:
        raise AccountsError('other', 'Switchboard is busy; try again in a moment. Nothing was removed.')
    except vault.VaultError as e:
        raise AccountsError('other', str(e))
    text, kind, facts_ = removed_said(rec, who)
    ctx.say(text, kind, **facts_)
    return who


def removed_said(rec, who):
    """What History says of a kept token or key that was removed: (text, kind, facts) for ctx.say."""
    return ('Removed the %s for %s from Switchboard' % (WHAT[rec['kind']], who), 'info',
            {'h': 'removed', 'account': who, 'by': 'you'})


def _drop(ctx, doc, bid, who):
    """Its record out of the list, then its Keychain item; when the Keychain refuses, the record goes back as it was.
    The caller holds the switch and account locks, and has checked that Claude Code is not on it."""
    recs = records(doc)
    rec = recs.pop(bid)
    _save_bearers(ctx, doc, recs, dict(recs, **{bid: rec}))
    done = ctx.keychain.delete(SERVICES[rec['kind']], bid)
    if not done.ok:
        _save_bearers(ctx, doc, dict(recs, **{bid: rec}), recs)
        from .switcher import keychain_refusal
        raise keychain_refusal(done.why, "Couldn't remove the %s for %s from the Keychain (%s); nothing was "
                                         "removed." % (WHAT[rec['kind']], who, done.why))


def _save_bearers(ctx, doc, recs, before):
    """doc's records set to recs and the list written. When the write fails, doc holds `before` again, as the file
    still does (an atomic write leaves the file as it was), and the error goes on to the caller."""
    doc['bearers'] = recs
    try:
        vault.save(ctx, doc)
    except BaseException:
        doc['bearers'] = before
        raise


def forget_for(ctx, doc, account_id, who):
    """For roster.remove, holding the switch and account locks, once the account `who` names is off the list: every
    token kept for it goes too (roster.remove checked first that Claude Code is on none of them). Returns what History
    says of each; a token the Keychain won't give up keeps its record, for the next try. The account is already
    removed, so a list that can't be written is not an error here: History says what stayed (doc holds what the file
    holds, _save_bearers)."""
    said = []
    for bid, rec in sorted(records(doc).items()):
        if rec.get('for') == account_id:
            try:
                _drop(ctx, doc, bid, who)
                said.append(removed_said(rec, who))
            except AccountsError as e:
                ctx.log('The token kept for a removed account stays for now: %s' % e.kind)
            except (OSError, ccfiles.CCFileError, vault.VaultError) as e:
                why = e.__class__.__name__
                if bid in records(doc):
                    ctx.log('The token kept for a removed account stays for now: %s' % why)
                    name = name_of(doc, rec)
                    said.append((FORGET_UNWRITTEN % (who, why, name, name), 'warn', {'h': 'problem', 'account': who}))
                else:
                    ctx.log('The record of a removed account\'s token went; its Keychain item stays: %s' % why)
                    said.append((FORGET_UNLISTED % (who, bid, why), 'warn', {'h': 'problem', 'account': who}))
    return said


def set_rule(ctx, bid, rule):
    """Give a kept token or key a rule in auto-switch. A key takes only 'out'; a token 'out' or 'last' (its limits
    are not known, so it is never used as usual). Returns the record's name."""
    try:
        with vault.switch_lock(ctx, timeout=10.0), vault.changing(ctx) as doc:
            recs = records(doc)
            rec = recs.get(bid)
            if rec is None:
                raise AccountsError('not_found', 'That was removed meanwhile.')
            if rule not in RULES[rec['kind']]:
                raise AccountsError('other', refusal(rec['kind'], rule))
            rec['rule'] = rule
            doc['bearers'] = recs
            return name_of(doc, rec)
    except vault.LockBusy:
        raise AccountsError('other', 'Switchboard is busy; try again in a moment.')


def refusal(kind, rule):
    """The one line that says no to a rule a kind of record can't have."""
    if kind == KEY:
        return ('An API key is billed per token by Anthropic, so auto-switch never puts Claude Code on one: it stays '
                'kept out.')
    return "A one-year token's limits are not known, so auto-switch uses it last or not at all."


def sign_ins_only(entry, name, command):
    """The one line a command that works on sign-ins only (`switchboard move`, `swap`, `folder`, `export`) says when
    it is given the name of a kept token or key. `entry` is that one's view() entry, `name` the word typed."""
    from .. import outside
    what = 'the %s for %s' % (WHAT[entry['kind']], entry['for']) if entry.get('for') else A_WHAT[entry['kind']]
    return '%s is %s; switchboard %s is for sign-ins.' % (outside.said(str(name)), what, command)


def set_alias(ctx, bid, alias):
    """A short name to type for a kept token or key (None takes it away)."""
    word = _word(alias)
    try:
        with vault.switch_lock(ctx, timeout=10.0), vault.changing(ctx) as doc:
            recs = records(doc)
            rec = recs.get(bid)
            if rec is None:
                raise AccountsError('not_found', 'That was removed meanwhile.')
            who = _taken(doc, word, bid) if word else None
            if who:
                raise AccountsError('other', '"%s" is already %s; choose another name.'
                                    % (word, who if who == 'an account number' else 'the name of ' + who))
            rec['alias'] = word
            doc['bearers'] = recs
            return name_of(doc, rec)
    except vault.LockBusy:
        raise AccountsError('other', 'Switchboard is busy; try again in a moment.')


def view(doc, live_id=None):
    """Every kept token and key for the surfaces, oldest first, without a secret: {'id', 'kind', 'for' (the name of
    the account it is for, name_of, or None), 'for_id' (that account's id, which a surface matches its row by, or
    None), 'name' (that account's name, or its label), 'label', 'alias', 'added_at', 'last4', 'rule', 'active' (Claude
    Code is on it now)}. Every word in it but the id is cleaned for printing (outside.said)."""
    from .. import outside

    def added(rec):                 # a hand-edited list may hold anything here: a number or nothing
        t = rec.get('added_at')
        return float(t) if isinstance(t, (int, float)) and not isinstance(t, bool) and t > 0 else None

    out = []
    for bid, rec in sorted(records(doc).items(), key=lambda kv: (added(kv[1]) or 0.0, kv[0])):
        a = doc['accounts'].get(rec.get('for')) if isinstance(rec.get('for'), str) else None
        out.append({'id': bid, 'kind': rec['kind'], 'for': name_of(doc, rec) if a else None,
                    'for_id': rec['for'] if a else None,
                    'name': name_of(doc, rec), 'label': outside.said(_s(rec.get('label'))) or None,
                    'alias': outside.said(_s(rec.get('alias'))) or None,
                    'added_at': added(rec),
                    'last4': rec.get('last4') if isinstance(rec.get('last4'), str) else '', 'rule': rule_of(rec),
                    'active': bid == live_id})
    return out


def remove_all(ctx):
    """For the uninstaller's --all: every kept token and key, records and items. Returns how many were removed."""
    try:
        doc = vault.load(ctx)
    except vault.VaultError:
        return 0
    n = 0
    for bid in sorted(records(doc)):
        try:
            remove(ctx, bid, even_in_use=True)
            n += 1
        except AccountsError as e:
            ctx.log('A kept token or key stays for now: %s' % e.kind)
    return n
