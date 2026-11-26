"""The switch transaction with a journal: lock, keep, write, read back, verify, roll back.

A switch, in order:
1. The switch lock (one change of accounts at a time). A journal left by a cut-off switch is settled first.
2. Claude Code's live login, read fresh outside its locks. Unreadable, or only readable as a possibly older copy:
   stop, nothing written (a switch that can't read the login in use would lose it).
3. Whose it is: fingerprint, then refresh-expiry stamp, then (no lock held) Anthropic's profile answer, then the
   label. The target's saved login is read fresh; an expired access token is refreshed first (tokens.refresh, holding
   the target's consume lock for the rest of the switch so nothing else spends it meanwhile). A refresh that may have
   been used up stops the switch: a dead token is never written.
4. The account lock, then Claude Code's two credential locks. The live login is read again: bytes that moved since
   step 2 are identified again from offline evidence only (no network under locks).
5. A journal is written before the first change. A safety copy of the live login goes to the recovery stash.
6. The outgoing login is saved for its owner only with proof, and never older over newer. Without proof it stays in
   the safety copy only and no slot is written ("label and login disagree" is shown, not resolved by guessing).
7. The target's login (with the live item's machine-wide siblings) is written to Claude Code's item, then its identity
   block to the global config under Claude Code's config lock.
8. Both are read back: the item must hold exactly the bytes written and the config must name the target. A mismatch
   puts the original login and label back (the target's stays in its slot, so nothing is lost) and says so.
At start, resolve_journal() finishes or undoes a switch that was cut off, from what the live login really holds.
"""
import json
import os
import secrets

from . import bearer, ccfiles, facts, identity, live as live_mod, tokens, vault
from .api import ABSENT, PRESENT, UNREADABLE, AccountsError, Read, SwitchResult

JOURNAL = 'switch-journal.json'
MIN_FREE = 50 * 1024 * 1024
SWITCH_LOCK_WAIT_S = 30.0
CC_LOCK_BUDGET_S = 18.0             # Claude Code's two credential locks together (9 s each)
_HOOK = None                        # tests: called with each step's name (a kill -9 test stops the process there)


def _step(name):
    if _HOOK is not None:
        _HOOK(name)


# ---------- the journal ----------

def journal_path(ctx):
    return ctx.path(JOURNAL)


def read_journal(ctx):
    return ccfiles.read_json(journal_path(ctx))


def _write_journal(ctx, j):
    ccfiles.atomic_write(journal_path(ctx), (json.dumps(j, indent=1) + '\n').encode('utf-8'), mode=0o600)


def _drop_journal(ctx):
    try:
        os.unlink(journal_path(ctx))
    except FileNotFoundError:
        pass


# ---------- plain errors ----------

DIALOG_WORDS = 'macOS is waiting for you to answer a Keychain dialog. Answer it, then try again; nothing was changed.'
SLOW = ('timeout', 'busy', 'dialog')        # Keychain answers that came too late: the call may still land


def keychain_refusal(why, sentence):
    """An AccountsError for a Keychain write or delete that did not go: the dialog's own words when macOS is waiting
    for an answer, else `sentence` (slow answers are kind keychain_slow: try again)."""
    if why == 'dialog':
        return AccountsError('keychain_slow', DIALOG_WORDS)
    return AccountsError('keychain_slow' if why in SLOW else 'other', sentence)


# What a switch says of the login it moved away from when that login can't be renewed and no record owns it (a token
# whose record went, an API key, a sign-in stripped of its refresh token, an item written by hand): true for each.
UNKEPT_NOTE = 'Claude Code was on a login with no refresh token; no copy of it was made.'


def unreadable_error(r, what="Claude Code's login", verb='read'):
    why = r.why or 'unknown'
    if why == 'dialog':
        return AccountsError('keychain_slow', DIALOG_WORDS)
    if verb != 'read' and why not in ('locked', 'timeout', 'busy', 'denied', 'no_keychain'):
        return AccountsError('other', "%s couldn't be written to the Keychain (%s), so nothing was changed. Try again "
                                      "in a minute." % (what[0].upper() + what[1:], why))
    if r.degraded:
        return AccountsError('unreadable_live', "macOS didn't answer for %s in the Keychain and only an older copy "
                                                "could be read, so Switchboard did not switch. Nothing was changed; "
                                                "try again in a minute." % what)
    if why == 'locked':
        return AccountsError('keychain_locked', "Your Mac's login Keychain is locked, so Switchboard can't read %s. "
                                                "Unlock the Mac and try again; nothing was changed." % what)
    if why in ('timeout', 'busy'):
        return AccountsError('keychain_slow', "macOS is slow to answer the Keychain right now, so nothing was "
                                              "changed. Try again in a minute.")
    if why == 'denied':
        return AccountsError('keychain_denied', "macOS refused Switchboard access to %s in the Keychain. Choose "
                                                "Always Allow when macOS asks, then try again; nothing was changed."
                             % what)
    if why == 'no_keychain':
        return AccountsError('keychain_locked', "The login Keychain isn't available to Switchboard right now (it may "
                                                "be running outside your login session). Nothing was changed.")
    return AccountsError('unreadable_live', "%s couldn't be read from the Keychain (%s), so nothing was changed. "
                                            "Try again in a minute." % (what[0].upper() + what[1:], why))


KEYCHAIN_WHYS = ('locked', 'timeout', 'busy', 'denied', 'no_keychain', 'dialog')


def waiting_error(res, email):
    """The refusal when a renewed login of the account waits, or may wait, and is not saved (tokens._waiting, or a
    renewal answered just now whose save failed): its saved login is spent, so it is not put in use. The words name
    what stopped it."""
    why = res.why.rpartition(': ')[2] or 'unknown'
    if res.outcome != 'transient':                  # found, and the Keychain did not take it
        return unreadable_error(Read(UNREADABLE, why=why), "%s's renewed login" % email, verb='write')
    if why in KEYCHAIN_WHYS:
        return unreadable_error(Read(UNREADABLE, why=why), 'a renewed login that may be waiting for %s' % email)
    return AccountsError('other', "A renewed login that may be waiting for %s can't be read (%s), so nothing was "
                                  "changed. If this keeps happening, sign in to %s in Claude Code, and Switchboard "
                                  "keeps it again." % (email, why, email))


def _check_disk(ctx):
    try:
        free = min(ctx.disk_free(ctx.data_dir), ctx.disk_free(ctx.home))
    except OSError:
        return
    if free < MIN_FREE:
        raise AccountsError('disk', "Your disk is almost full (under 50 MB free), so Switchboard won't switch: it "
                                    "couldn't promise to undo a change cut off half-way. Free some space and try "
                                    "again.")


def _who(doc, aid):
    a = doc['accounts'].get(aid) or {}
    return a.get('email') or aid or 'another account'


def _identity_for(a):
    block = a.get('identity')
    if isinstance(block, dict) and block.get('accountUuid'):
        return dict(block)
    return {'accountUuid': a.get('uuid') or a.get('id'), 'emailAddress': a.get('email') or '',
            'organizationUuid': a.get('org_id') or None, 'organizationName': a.get('org_name') or None}


def _label_is(block, a):
    uuid, _, org = live_mod.label(block)
    return identity.agrees(a, uuid, org)


# ---------- keeping the outgoing login ----------

def _keep_outgoing(ctx, doc, login, proof, block):
    """Save the live login for its proven owner (never older over newer). Returns a word for what happened. Raises
    AccountsError when a save it must make fails (nothing has been written to Claude Code yet)."""
    if login is None or not login.full_pair:
        return 'nothing'
    now = ctx.wall()

    def must(done, who):
        if not done.ok:
            raise keychain_refusal(done.why, "Couldn't save the login in use for %s (Keychain: %s), so nothing was "
                                             "changed. Try again in a minute." % (who, done.why))

    if proof.strong and proof.account_id:
        owner = proof.account_id
        a = doc['accounts'][owner]
        vr = vault.read_current(ctx, owner, fresh=True, interactive=True)
        if vr.kind == UNREADABLE:
            ctx.log('Did not save the login in use for %s: its saved copy could not be read to compare (%s); it is '
                    'in the safety copy' % (a.get('email') or owner, vr.why))
            return 'unreadable'
        stored = live_mod.parse(vr.value) if vr.kind == PRESENT else None
        verdict = live_mod.compare(login, stored)
        usable = stored is not None and stored.kind == 'oauth' and stored.has_refresh and \
            (a.get('strike') or {}).get('fp') != stored.fingerprint          # a refused saved login is no login
        if not usable or verdict == 'newer' or (verdict == 'same' and not vault.same_saved(login.text, stored.text)):
            must(vault.save_login(ctx, owner, login.text, old=vr), a.get('email') or owner)
            if usable and stored.refresh_expires_ms != login.refresh_expires_ms and \
                    identity.renewal_of(login.refresh_expires_ms, stored.refresh_expires_ms):
                ctx.log('The saved login of %s (%s) was older than the login in use of the same sign-in (%s), whose '
                        'end stamp is %.1f s earlier' % (a.get('email') or owner, stored.fingerprint,
                                                         login.fingerprint,
                                                         (stored.refresh_expires_ms - login.refresh_expires_ms)
                                                         / 1000.0))
            vault.note_login(a, login, proof.kind, now)
            if block and _label_is(block, a):
                a['identity'] = dict(block)
            return 'saved'
        if verdict == 'same':
            return 'same'
        if verdict == 'older':
            ctx.log('Kept the saved login of %s: the login in use is an older copy' % (a.get('email') or owner))
            return 'kept_saved'
        ctx.log('Kept the saved login of %s and the one in use in a safety copy: which is newer cannot be told'
                % (a.get('email') or owner))
        return 'not_comparable'
    if proof.strong and proof.new_uuid:
        aid = vault.account_key(doc, proof.new_uuid, proof.new_org)
        if aid in doc['removed']:
            return 'removed'
        uuid = live_mod.label(block)[0]
        blk = dict(block) if block and uuid == proof.new_uuid else {
            'accountUuid': proof.new_uuid, 'emailAddress': proof.new_email or '', 'organizationUuid': proof.new_org}
        must(vault.save_login(ctx, aid, login.text, old=Read(ABSENT)), proof.new_email or aid)
        vault.new_account(doc, aid, blk, login, proof.kind, now)
        return 'added'
    if proof.kind == 'label':
        uuid, email, org = live_mod.label(block)
        aid = vault.account_key(doc, uuid, org)
        if not aid or aid in doc['accounts'] or aid in doc['removed']:
            if aid in doc['accounts']:
                ctx.log('Did not save the login in use for %s: only Claude Code\'s settings say whose it is (%s); it '
                        'is in the safety copy' % (doc['accounts'][aid].get('email') or aid, proof.note or 'no answer'))
            return 'unproven'
        must(vault.save_login(ctx, aid, login.text, old=Read(ABSENT)), email or aid)
        vault.new_account(doc, aid, block, login, 'label', now)
        return 'added_unproven'
    return 'unplaced'


# ---------- the switch ----------

def live_mark(login):
    """What a dead live login is recognised by when add_login asks for it to be replaced: its text fingerprint, or ''
    when Claude Code holds no login at all."""
    return '' if login is None or login.kind == 'empty' else login.text_fp


SAME_ACCOUNT = 'Claude Code is on the new sign-in of %s'     # a replace_dead put, as History says it


def _changed_meanwhile():
    return AccountsError('other', "Claude Code's login changed meanwhile, so Switchboard left it as it is.")


def switch(ctx, target, kind='manual', source='cli', reason='', interactive=False, replace_dead=None):
    """Move Claude Code's live login to account id `target`. Raises AccountsError when nothing was changed.

    replace_dead (add_login and the keep pass only): the live_mark of a live login of target's own account that can't
    work (gone, emptied or refused) or is an earlier sign-in of it than the saved one. It is written over although
    Claude Code is "already on" target; when the live login is no longer exactly that one, nothing is changed
    (AccountsError). The history says it as the same account's new sign-in, not as a switch."""
    _check_disk(ctx)
    try:
        lock = vault.switch_lock(ctx, timeout=SWITCH_LOCK_WAIT_S)
        lock.__enter__()
    except vault.LockBusy:
        raise AccountsError('other', 'Another switch is still running; try again in a moment.')
    try:
        r, j = read_journal(ctx)
        if r.kind != ABSENT:
            note = _resolve_locked(ctx)
            if read_journal(ctx)[0].kind != ABSENT:
                raise AccountsError('other', 'An earlier switch was cut off and could not be settled yet (%s). '
                                             'Nothing was changed.' % (note or 'the Keychain did not answer'))
            if note:
                ctx.say(note, 'info', h='repair', by='auto')
        try:
            res, said = _switch_locked(ctx, target, kind, source, reason, interactive, replace_dead)
        except ccfiles.DiskFull:
            _settle_quietly(ctx)
            raise AccountsError('disk', 'Your disk is full, so Switchboard stopped part-way and put Claude Code back '
                                        'in a consistent state (no login is lost). Free some space and try again.')
        except ccfiles.CCFileError as e:
            _settle_quietly(ctx)
            raise AccountsError('other', str(e))
        except OSError as e:
            _settle_quietly(ctx)
            raise AccountsError('other', "Switchboard could not write its own files (%s), so it stopped part-way; "
                                         "what was begun is settled now or at its next start." % e.__class__.__name__)
    finally:
        lock.__exit__(None, None, None)
    for text, k, facts_ in said:
        ctx.say(text, k, **facts_)
    return res


def _switch_locked(ctx, target, kind, source, reason, interactive, replace_dead=None):
    from . import runas
    runas.reap_stale_claims(ctx)        # a finished run may hold a newer generation than the saved copy: take it first
    try:
        doc = vault.load(ctx)
    except vault.VaultError as e:
        raise AccountsError('other', str(e))
    if target not in doc['accounts'] and target in bearer.records(doc):
        return _to_token(ctx, doc, bearer.records(doc)[target], kind, source, reason, interactive)
    if target not in doc['accounts']:
        raise AccountsError('not_found', 'No saved account matches that.')
    a = doc['accounts'][target]
    email = a.get('email') or target
    if vault.claimed(ctx, doc, target):
        raise AccountsError('claimed', '%s is in use by a `switchboard run` right now; switch once it ends.' % email)
    pre = live_mod.read_live(ctx, fresh=True, interactive=interactive)
    if pre.read.kind == UNREADABLE or pre.read.degraded:
        raise unreadable_error(pre.read)
    cfg_r, block, cfg_path = live_mod.config_identity(ctx)
    if cfg_r.kind == UNREADABLE:
        raise AccountsError('other', "Claude Code's settings file (%s) can't be %s, so nothing was changed. Open "
                                     "Claude Code once so it repairs it, then try again."
                            % (cfg_path, 'understood' if cfg_r.why == 'unparseable' else 'read'))
    if replace_dead is not None and live_mark(pre.login) != replace_dead:
        raise _changed_meanwhile()
    # a login that can't be renewed (a one-year token, kept or not) is never sent to Anthropic to ask whose it is
    proof = identity.prove(ctx, pre.login, doc, block, network=not live_mod.unrenewable(pre.login))
    if replace_dead is None and pre.login is not None and pre.login.kind == 'oauth' and proof.strong and \
            proof.account_id == target:
        return _already_on(ctx, target, pre.login, kind, source, reason)
    try:
        consume = vault.consume_lock(ctx, target, timeout=10.0)
    except vault.LockBusy:
        raise AccountsError('claude_busy', "%s's saved login is being renewed right now; try again in a few "
                                           "seconds. Nothing was changed." % email)
    try:
        if (doc.get('claims') or {}).get(target):
            raise AccountsError('claimed', 'A run of %s has not been tidied up yet; try again in a moment.' % email)
        tlogin = _target_login(ctx, target, a, interactive)
        if tlogin.expired(ctx.wall()):
            res = tokens.refresh(ctx, target, budget_s=45.0, consume=consume, reason='switch')
            if res.why.startswith((tokens.WAITING, tokens.SAVE_FAILED)):
                raise waiting_error(res, email)             # Anthropic is not the cause: the renewed login is here
            if res.spent_maybe or (res.outcome == 'skipped' and res.why == 'in_flight'):
                _wait_job(target)                           # a late answer may still come and be saved
                tlogin = _target_login(ctx, target, a, True)
                if tlogin.expired(ctx.wall()):
                    # (an answer that came and is kept for later is not "no answer")
                    told = '' if res.outcome in ('stashed', 'unpersisted') else ' (no answer from Anthropic)'
                    raise AccountsError('network', "Couldn't finish renewing %s's saved login%s, so nothing was "
                                                   "changed. Try again in a minute." % (email, told))
            elif res.outcome in ('refreshed', 'adopted'):
                tlogin = _target_login(ctx, target, a, True)
            elif res.outcome == 'permanent':
                raise AccountsError('dead_login', '%s needs sign-in: Anthropic refused its saved login. Sign in to it '
                                                  'in Claude Code, and Switchboard keeps it again.' % email)
            elif res.outcome == 'systemic':
                raise AccountsError('other', "Anthropic refused Claude Code's sign-in client just now; nothing was "
                                             "changed. Try again later.")
            # anything else spent nothing (no network, too little time): Claude Code renews it once it is live
        else:
            # Not run out, so nothing renews it here. But a renewal goes out early: when its answer could not be
            # saved, this login is spent already and the one that works is waiting. That one is put in use.
            res = tokens.adopt_waiting(ctx, target)
            if res.outcome == 'adopted':
                tlogin = _target_login(ctx, target, a, True)
            elif res.spent_maybe:
                raise waiting_error(res, email)
        # what is left once Claude Code's locks are let go: a death before that always leaves a journal (which
        # clears the lock folders it recorded), never orphaned locks without one
        after = {'drop_journal': False, 'drop_stash': None, 'done': False}
        try:
            try:
                lock = vault.account_lock(ctx, timeout=10.0)
                lock.__enter__()
            except vault.LockBusy:
                raise AccountsError('other', 'Switchboard is busy saving logins; try again in a moment. Nothing was '
                                             'changed.')
            try:
                try:
                    cm = ccfiles.credential_locks(ctx.env, ctx.home, budget_s=CC_LOCK_BUDGET_S, log=ctx.log)
                    cclocks = cm.__enter__()
                except ccfiles.LockBusy:
                    raise AccountsError('claude_busy', 'Claude Code is renewing its login right now; nothing was '
                                                       'changed. Try again in a few seconds.')
                try:
                    return _critical(ctx, target, pre, proof, tlogin, cclocks, kind, source, reason, after,
                                     replace_dead)
                finally:
                    cm.__exit__(None, None, None)
            finally:
                lock.__exit__(None, None, None)
        finally:
            if after['drop_journal']:
                _drop_journal(ctx)
            if after['drop_stash']:
                vault.drop_stash(ctx, after['drop_stash'])
            if after['done']:
                _step('done')
    finally:
        consume.release()


def _to_token(ctx, doc, rec, kind, source, reason, interactive):
    """Steps 2 to 8 of a switch whose target is a kept one-year token (bearer.py): the same transaction, with the
    token's login (bearer.login_text) in place of a saved sign-in. There is nothing to renew. An API key is never put
    in Claude Code's login item."""
    target, name = rec['id'], bearer.name_of(doc, rec)
    if rec['kind'] != bearer.TOKEN:
        raise AccountsError('other', bearer.KEY_SWITCH % name)
    pre = live_mod.read_live(ctx, fresh=True, interactive=interactive)
    if pre.read.kind == UNREADABLE or pre.read.degraded:
        raise unreadable_error(pre.read)
    cfg_r, block, cfg_path = live_mod.config_identity(ctx)
    if cfg_r.kind == UNREADABLE:
        raise AccountsError('other', "Claude Code's settings file (%s) can't be %s, so nothing was changed. Open "
                                     "Claude Code once so it repairs it, then try again."
                            % (cfg_path, 'understood' if cfg_r.why == 'unparseable' else 'read'))
    if bearer.owns(doc, pre.login) == target:
        return SwitchResult(ok=True, from_id=target, to_id=target, kind=kind, source=source,
                            reason='Already on the one-year token for %s' % name), []
    r = bearer.secret(ctx, target, rec['kind'])
    if r.kind == UNREADABLE:
        raise unreadable_error(r, 'the one-year token for %s' % name)
    if r.kind == ABSENT:
        raise AccountsError('dead_login', 'The one-year token for %s is missing from the Keychain. Add it again with: '
                                          'switchboard add --token' % name)
    tlogin = live_mod.parse(bearer.login_text(r.value))
    r = None
    proof = identity.prove(ctx, pre.login, doc, block, network=not live_mod.unrenewable(pre.login))
    after = {'drop_journal': False, 'drop_stash': None, 'done': False}
    try:
        try:
            lock = vault.account_lock(ctx, timeout=10.0)
            lock.__enter__()
        except vault.LockBusy:
            raise AccountsError('other', 'Switchboard is busy saving logins; try again in a moment. Nothing was '
                                         'changed.')
        try:
            try:
                cm = ccfiles.credential_locks(ctx.env, ctx.home, budget_s=CC_LOCK_BUDGET_S, log=ctx.log)
                cclocks = cm.__enter__()
            except ccfiles.LockBusy:
                raise AccountsError('claude_busy', 'Claude Code is renewing its login right now; nothing was '
                                                   'changed. Try again in a few seconds.')
            try:
                return _critical(ctx, target, pre, proof, tlogin, cclocks, kind, source, reason, after, token=rec)
            finally:
                cm.__exit__(None, None, None)
        finally:
            lock.__exit__(None, None, None)
    finally:
        if after['drop_journal']:
            _drop_journal(ctx)
        if after['drop_stash']:
            vault.drop_stash(ctx, after['drop_stash'])
        if after['done']:
            _step('done')


def _settle_quietly(ctx):
    """After a switch stopped on a file error: settle its journal now if that can be done (else at the next start)."""
    try:
        if read_journal(ctx)[0].kind != ABSENT:
            note = _resolve_locked(ctx)
            if note:
                ctx.log(note)
    except Exception as e:  # noqa: BLE001 - the original error is what the person hears about
        ctx.log('Could not settle the stopped switch yet: %s' % e.__class__.__name__)


def _wait_job(aid, limit_s=35.0):
    job = tokens._INFLIGHT.get(aid)
    if job is not None:
        job.done.wait(limit_s)


def conflict_error(email):
    """The refusal for an account whose saved login Anthropic says is another account's (status 'conflict'): no switch
    and no `switchboard run` puts that login in use."""
    return AccountsError('dead_login', "Anthropic says the login kept as %s belongs to another account, so "
                                       "Switchboard won't put it in use. Sign in to %s in Claude Code, and "
                                       "Switchboard keeps its own login." % (email, email))


def _target_login(ctx, target, a, interactive):
    email = a.get('email') or target
    try:
        now_a = vault.load(ctx)['accounts'].get(target) or a    # fresh: a renewal in this switch may have just said
    except vault.VaultError:
        now_a = a
    if now_a.get('conflict'):
        raise conflict_error(email)
    tr = vault.read_current(ctx, target, fresh=True, interactive=interactive)
    if tr.kind == UNREADABLE:
        raise unreadable_error(tr, "%s's saved login" % email)
    if tr.kind == ABSENT:
        raise AccountsError('dead_login', "%s's saved login is missing from the Keychain. Sign in to it in Claude "
                                          "Code, and Switchboard keeps it again." % email)
    tlogin = live_mod.parse(tr.value)
    if not tlogin.full_pair:
        raise AccountsError('dead_login', "%s's saved login is incomplete. Sign in to it in Claude Code, and "
                                          "Switchboard keeps it again." % email)
    if (a.get('strike') or {}).get('fp') == tlogin.fingerprint:
        raise AccountsError('dead_login', '%s needs sign-in: Anthropic refused its saved login. Sign in to it in '
                                          'Claude Code, and Switchboard keeps it again.' % email)
    return tlogin


def _already_on(ctx, target, login, kind, source, reason):
    """The live login is already the target's: keep a newer generation of it, make the label agree, change nothing
    else (a self-switch never writes the saved copy over the live login)."""
    said = []
    try:
        with vault.account_lock(ctx, timeout=10.0):
            doc = vault.load(ctx)
            a = doc['accounts'][target]
            cur = live_mod.read_live(ctx, fresh=True, interactive=True)
            if cur.usable and cur.login is not None and cur.login.full_pair:
                vr = vault.read_current(ctx, target, fresh=True, interactive=True)
                stored = live_mod.parse(vr.value) if vr.kind == PRESENT else None
                if vr.kind != UNREADABLE and identity.offline(cur.login, doc).account_id == target and \
                        (stored is None or live_mod.compare(cur.login, stored) == 'newer'):
                    if vault.save_login(ctx, target, cur.login.text, old=vr).ok:
                        vault.note_login(a, cur.login, 'fingerprint', ctx.wall())
                        vault.save(ctx, doc)
            _, block, _ = live_mod.config_identity(ctx)
            if block is not None and not _label_is(block, a):
                with ccfiles.config_lock(ctx.env, ctx.home, log=ctx.log):
                    ccfiles.write_identity(ctx.env, ctx.home, _identity_for(a))
                said.append(('Claude Code is on %s (its settings named another account; fixed)' % a.get('email'),
                             'info', {'h': 'repair', 'account': a.get('email'), 'by': 'auto'}))
    except (vault.LockBusy, ccfiles.LockBusy, ccfiles.CCFileError, OSError) as e:
        ctx.log('Already on %s; could not tidy up: %s' % (target, e.__class__.__name__))
    doc = vault.load(ctx)
    return SwitchResult(ok=True, from_id=target, to_id=target, kind=kind, source=source,
                        reason='Already on %s' % _who(doc, target)), said


def _critical(ctx, target, pre, proof, tlogin, cclocks, kind, source, reason, after, replace_dead=None, token=None):
    """Steps 4-8, holding the switch, consume, account and Claude Code's credential locks. `after` collects what the
    caller does once those locks are let go (dropping the journal is the very last step). token: the record of a kept
    one-year token that is the target (tlogin is its login); its account's identity block goes with it, or none for a
    token of its own."""
    doc = vault.load(ctx)
    if token is not None:
        token = bearer.records(doc).get(target)
        if token is None:
            raise AccountsError('not_found', 'That one-year token was removed meanwhile; nothing was changed.')
        a = doc['accounts'].get(token.get('for')) or {}
        email = bearer.name_of(doc, token)
    elif target not in doc['accounts']:
        raise AccountsError('not_found', 'That account was removed meanwhile; nothing was changed.')
    else:
        a = doc['accounts'][target]
        email = a.get('email') or target
    want_block = _identity_for(a) if token is None or a else None
    cur = live_mod.read_live(ctx, fresh=True, interactive=True)
    if cur.read.kind == UNREADABLE or cur.read.degraded:
        raise unreadable_error(cur.read)
    cfg_r, block, cfg_path = live_mod.config_identity(ctx)
    if cfg_r.kind == UNREADABLE:
        raise AccountsError('other', "Claude Code's settings file (%s) can't be read now, so nothing was changed."
                            % cfg_path)
    before_text = pre.login.text if pre.login else None
    if (cur.login.text if cur.login else None) != before_text:
        if replace_dead is not None:
            raise _changed_meanwhile()
        proof = identity.reprove(cur.login, doc, block, proof, pre.login)
    if replace_dead is None and cur.login is not None and cur.login.kind == 'oauth' and proof.strong and \
            proof.account_id == target:
        return SwitchResult(ok=True, from_id=target, to_id=target, kind=kind, source=source,
                            reason='Already on %s (Claude Code moved there by itself)' % email), []
    if token is None:
        tlogin2 = _target_login(ctx, target, a, True)
        if tlogin2.text != tlogin.text:
            tlogin = tlogin2
    from_id = proof.account_id if proof.strong else None
    out = cur.login
    held = bearer.owns(doc, out)        # the login in use is a kept token: it is in its own item already
    if held is not None:
        from_id = held
    unkept = held is None and live_mod.unrenewable(out)   # by what its text holds: never copied aside
    # The account the switch's record says it left (the put-back never puts back an account a switch just left): an
    # emptied login proves no account by itself, and it is the account Claude Code's settings named that was emptied
    # there. For the record only: what is kept of the outgoing login, and what History says, go by the proof.
    left_id = identity.label_id(doc, block) if from_id is None and out is not None and out.kind == 'wiped' else from_id
    j = {'id': secrets.token_hex(6), 'pid': os.getpid(), 'at': ctx.wall(), 'from_id': from_id, 'to_id': target,
         'kind': kind, 'source': source, 'step': 'started',
         'orig_fp': out.fingerprint if out else None, 'orig_rx': out.refresh_expires_ms if out else None,
         'orig_text_fp': out.text_fp if out is not None and out.kind != 'empty' else None,
         'orig_label': block, 'target_fp': tlogin.fingerprint, 'target_rx': tlogin.refresh_expires_ms,
         'locks': cclocks.records()}
    if token is not None:
        j['to_block'] = want_block
    if held is not None or unkept:
        j['out_token'] = 'kept' if held is not None else 'not_kept'     # what a cut-off switch's note says of it
    if left_id != from_id:
        j['left_id'] = left_id                                          # for the record a cut-off switch writes
    _write_journal(ctx, j)
    _step('journal')
    safety_key = None
    if out is not None and out.kind != 'empty' and held is None and not unkept:
        safety_key = 'safety:' + (out.fingerprint or out.text_fp)
        ok, where = vault.stash(ctx, safety_key, out.text, account_id=from_id, reason='before switching to %s' % email)
        if not ok:
            after['drop_journal'] = True
            raise AccountsError('disk', "Couldn't keep a safety copy of the login in use (the disk may be full), so "
                                        "nothing was changed.")
        j['safety_key'], j['safety_where'] = safety_key, where
        _write_journal(ctx, j)
    _step('safety')
    try:
        kept = 'token' if held is not None else 'token_not_kept' if unkept else \
            _keep_outgoing(ctx, doc, out, proof, block)
        vault.save(ctx, doc)
    except AccountsError:
        after['drop_journal'] = True
        raise
    j['step'], j['kept'] = 'live_saved', kept
    _write_journal(ctx, j)
    _step('live_saved')
    composed = live_mod.dump(live_mod.compose(tlogin.blob, out.blob if out is not None else None))
    if not cclocks.still_held():
        after['drop_journal'] = True
        raise AccountsError('claude_busy', 'Claude Code took its login lock back (the Mac may have slept); nothing '
                                           'was changed. Try again.')
    j['step'], j['written_fp'] = 'writing', live_mod.parse(composed).fingerprint
    j['written_text_fp'] = live_mod.parse(composed).text_fp     # a token's login has no refresh fingerprint
    _write_journal(ctx, j)
    _step('writing')
    done = live_mod.write_live(ctx, composed, interactive=True)
    if not done.ok:
        if done.why in SLOW:
            j['step'] = 'write_unknown'
            _write_journal(ctx, j)
            note = ("macOS is waiting for you to answer a Keychain dialog; Switchboard will finish or undo the switch "
                    "once it is answered." if done.why == 'dialog' else
                    "macOS didn't confirm the change in time; Switchboard will finish or undo it as soon as the "
                    "Keychain answers.")
            return SwitchResult(ok=False, from_id=from_id, to_id=target, kind=kind, source=source, reason=reason,
                                note=note), []
        after['drop_journal'] = True
        raise unreadable_error(Read(UNREADABLE, why=done.why), "Claude Code's login", verb='write')
    j['step'] = 'credential_written'
    _write_journal(ctx, j)
    _step('credential_written')
    try:
        with ccfiles.config_lock(ctx.env, ctx.home, log=ctx.log) as cfg_lock:
            j['locks'] = cclocks.records() + [cfg_lock.record()]
            _write_journal(ctx, j)
            _step('config_lock')
            ccfiles.write_identity(ctx.env, ctx.home, want_block)
        j['locks'] = cclocks.records()
    except (ccfiles.LockBusy, ccfiles.CCFileError, OSError) as e:
        rolled = _rollback(ctx, j, out, block, label_too=False, after=after)
        why = 'Claude Code was saving its settings' if isinstance(e, ccfiles.LockBusy) else \
            "Claude Code's settings file could not be written"
        return _undone(ctx, from_id, target, kind, source, reason, rolled, why, email)
    j['step'] = 'config_written'
    _write_journal(ctx, j)
    _step('config_written')
    back = live_mod.read_live(ctx, fresh=True, interactive=True)
    _, block2, _ = live_mod.config_identity(ctx)
    _step('verify')
    label_ok = block2 is None if want_block is None else (block2 is not None and _label_is(block2, a))
    if not (back.usable and back.login is not None and back.login.text == composed and label_ok):
        rolled = _rollback(ctx, j, out, block, label_too=True, after=after)
        return _undone(ctx, from_id, target, kind, source, reason, rolled, 'the Keychain did not keep it', email)
    doc['switches'] = (doc.get('switches') or [])[-49:] + [{'at': ctx.wall(), 'from': left_id, 'to': target,
                                                            'kind': kind, 'source': source}]
    doc['active'] = target
    vault.save(ctx, doc)
    j['step'] = 'verified'
    _write_journal(ctx, j)
    after['drop_journal'] = True
    if safety_key and kept in ('saved', 'same', 'kept_saved', 'added'):
        after['drop_stash'] = safety_key
    after['done'] = True
    note = None
    if kept in ('unproven', 'unplaced', 'unreadable', 'removed'):
        note = ("The login that was in use could not be proved to be any saved account's, so it was kept aside in a "
                "safety copy and no saved account was changed.")
    elif kept == 'token_not_kept':
        note = UNKEPT_NOTE
    elif kept == 'not_comparable':
        note = ("The login that was in use could not be told apart from the saved one by age, so both were kept (it "
                "is in a safety copy).")
    frm = _who(doc, from_id) if from_id else None
    if held is not None:
        frm = 'the one-year token for %s' % bearer.name_of(doc, bearer.records(doc).get(held) or {'id': held})
    by = 'you' if source in ('page', 'menu', 'cli') else 'auto'
    if token is not None:
        words = 'Switched Claude Code to the one-year token for %s' % email
        who = a.get('email') or email           # History's facts keep the email, as the plain switch line's do
        said = [('%s. %s' % (words, bearer.AFTER_SWITCH[bearer.TOKEN]), 'switch',
                 {'h': 'switch', 'account': who, 'to': who, 'frm': frm, 'reason': reason or None, 'by': by})]
        return SwitchResult(ok=True, from_id=from_id, to_id=target, kind=kind, source=source,
                            reason=reason or words, note=note or bearer.AFTER_SWITCH[bearer.TOKEN]), said
    from .. import names                # with its organization when another kept account has its email (names.py)
    if replace_dead is not None:                # the same account, its newer sign-in: no switch between accounts
        said = [(SAME_ACCOUNT % names.in_doc(doc, target), 'info', {'h': 'signin', 'account': email,
                                                                   'reason': reason or None, 'by': by})]
    else:
        said = [('Switched Claude Code to %s' % names.in_doc(doc, target), 'switch',
                 {'h': 'switch', 'account': email, 'to': email, 'frm': frm, 'reason': reason or None, 'by': by})]
    return SwitchResult(ok=True, from_id=from_id, to_id=target, kind=kind, source=source,
                        reason=reason or 'Switched Claude Code to %s' % email, note=note), said


def _undone(ctx, from_id, target, kind, source, reason, rolled, why, email):
    if rolled:
        text = 'The switch to %s was undone: %s. Claude Code stays on the account it was on.' % (email, why)
    else:
        text = ('The switch to %s could not finish (%s) and could not be undone yet; Switchboard will settle it as '
                'soon as it can.' % (email, why))
    return SwitchResult(ok=False, from_id=from_id, to_id=target, kind=kind, source=source, reason=reason,
                        rolled_back=rolled, note=text), [(text, 'warn', {'h': 'problem', 'account': email})]


def _rollback(ctx, j, out, orig_block, label_too, after=None):
    """Put the original live login (and label) back. The target's login stays in its slot, so this loses nothing.
    True when the original state is back (the journal then goes once the locks are let go)."""
    ok = True
    if out is None:
        from . import facts
        done = ctx.keychain.delete(facts.keychain_service(ctx.env), facts.keychain_account(ctx.env))
        ok = done.ok
    else:
        done = live_mod.write_live(ctx, out.text, interactive=True)
        back = live_mod.read_live(ctx, fresh=True, interactive=True) if done.ok else None
        ok = bool(done.ok and back is not None and back.usable and back.login is not None and
                  back.login.text == out.text)
    if ok and label_too:
        try:
            with ccfiles.config_lock(ctx.env, ctx.home, log=ctx.log):
                ccfiles.write_identity(ctx.env, ctx.home, orig_block)
        except (ccfiles.LockBusy, ccfiles.CCFileError, OSError):
            ok = False
    j['step'] = 'rolled_back' if ok else 'rollback_failed'
    try:
        _write_journal(ctx, j)
    except OSError:
        pass
    if ok:
        if after is None:
            _drop_journal(ctx)
        else:
            after['drop_journal'] = True
    return ok


# ---------- a switch that was cut off ----------

def resolve_journal(ctx):
    """Finish or undo a cut-off switch. Returns a plain note, or None when there was nothing to do."""
    if read_journal(ctx)[0].kind == ABSENT:
        return None
    try:
        with vault.switch_lock(ctx, timeout=30.0):
            note = _resolve_locked(ctx)
    except vault.LockBusy:
        return None
    if note:
        ctx.say(note, 'info', h='repair', by='auto')
    return note


def _resolve_locked(ctx):
    r, j = read_journal(ctx)
    if r.kind == ABSENT:
        return None
    if r.kind == UNREADABLE or not isinstance(j, dict):
        aside = '%s.damaged-%d' % (journal_path(ctx), int(ctx.wall()))
        try:
            os.replace(journal_path(ctx), aside)
        except OSError:
            return 'A damaged record of a switch could not be put away.'
        return 'Put away the record of a switch that could not be read.'
    if j.get('pid') != os.getpid():
        ccfiles.clear_orphans(j.get('locks'), log=ctx.log)    # its process died holding them (we hold its lock now)
    cur = live_mod.read_live(ctx, fresh=True, interactive=True)
    if cur.read.kind == UNREADABLE or cur.read.degraded:
        return 'A switch was cut off; Switchboard will settle it as soon as the Keychain answers.'
    login = cur.login
    try:
        doc = vault.load(ctx)
    except vault.VaultError:
        return 'A switch was cut off; it will be settled once the account list can be read.'
    cfg_r, block, _ = live_mod.config_identity(ctx)
    tgt = doc['accounts'].get(j.get('to_id'))
    # a renewal since the journal was written can lower the stamp a little (facts.SAME_SIGN_IN_MS), never raise it
    # (identity.renewal_of): a stamp a little later is another sign-in, made meanwhile. When the two sides' own stamps
    # are that close (accounts signed in within minutes of each other), a stamp that is not exact tells neither side
    stamps_apart = not facts.same_sign_in(j.get('target_rx'), j.get('orig_rx'))

    def same_stamp(x, rx):
        got = x.refresh_expires_ms
        return bool(rx) and (got == rx or (stamps_apart and identity.renewal_of(got, rx)))

    def is_target(x):
        return x is not None and x.kind == 'oauth' and bool(
            (j.get('written_text_fp') and x.text_fp == j.get('written_text_fp')) or
            (j.get('written_fp') and x.fingerprint == j.get('written_fp')) or
            (j.get('target_fp') and x.fingerprint == j.get('target_fp')) or
            same_stamp(x, j.get('target_rx')))

    def is_orig(x):
        if x is None:
            return not j.get('orig_text_fp')
        return bool((j.get('orig_fp') and x.fingerprint == j.get('orig_fp')) or
                    same_stamp(x, j.get('orig_rx')) or
                    (j.get('orig_text_fp') and x.text_fp == j.get('orig_text_fp')))

    try:
        if is_target(login):
            if tgt is not None and cfg_r.kind != UNREADABLE and (block is None or not _label_is(block, tgt)):
                with ccfiles.config_lock(ctx.env, ctx.home, log=ctx.log):
                    ccfiles.write_identity(ctx.env, ctx.home, _identity_for(tgt))
            elif tgt is None and 'to_block' in j and cfg_r.kind != UNREADABLE and block != j['to_block']:
                with ccfiles.config_lock(ctx.env, ctx.home, log=ctx.log):     # a switch onto a one-year token
                    ccfiles.write_identity(ctx.env, ctx.home, j['to_block'])
            with vault.account_lock(ctx, timeout=10.0):
                doc = vault.load(ctx)
                if tgt is not None:
                    doc['switches'] = (doc.get('switches') or [])[-49:] + [{
                        'at': ctx.wall(), 'from': j.get('left_id', j.get('from_id')), 'to': j.get('to_id'),
                        'kind': j.get('kind'), 'source': 'repair'}]
                    doc['active'] = j.get('to_id')
                vault.save(ctx, doc)
            _drop_journal(ctx)
            if j.get('safety_key') and j.get('kept') in ('saved', 'same', 'kept_saved', 'added'):
                vault.drop_stash(ctx, j['safety_key'])
            return 'Repaired the cut-off switch: Claude Code is on %s' % ((tgt or {}).get('email') or j.get('to_id'))
        if is_orig(login):
            orig = j.get('orig_label')
            if tgt is not None and block is not None and _label_is(block, tgt) and (orig is None or
                                                                                   isinstance(orig, dict)):
                with ccfiles.config_lock(ctx.env, ctx.home, log=ctx.log):
                    ccfiles.write_identity(ctx.env, ctx.home, orig)
            _drop_journal(ctx)
            return 'Repaired the switch that was cut off'
    except (ccfiles.LockBusy, vault.LockBusy):
        return 'A switch was cut off; Claude Code is busy, so it will be settled on the next try.'
    except (ccfiles.CCFileError, OSError) as e:
        return 'A switch was cut off and could not be settled yet (%s).' % e.__class__.__name__
    _drop_journal(ctx)
    where = {'kept': 'the one-year token that was in use is still kept in its own Keychain item',
             'not_kept': 'the login that was in use had no refresh token, so no copy of it was made'}
    where = where.get(j.get('out_token')) or \
        ('the login that was in use is in a safety copy' if j.get('safety_key') else None)
    return ('Put away the record of a switch that was cut off: Claude Code now holds a login Switchboard did not '
            'write, so nothing was changed%s.' % (' (%s)' % where if where else ''))
