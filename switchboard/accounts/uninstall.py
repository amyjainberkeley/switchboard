"""Every account and every saved login, taken out for the uninstaller's --all (`switchboard tidy --all`).

The engine keeps logins in the Keychain under four services (vault.py): "Switchboard login" (each account's current
login), "Switchboard login (previous)" (its earlier generations), "Switchboard recovery" (a refresh's successor,
"successor:<id>", and a switch's safety copy, "safety:<fingerprint>") and "Switchboard login (Mac-wide parts)" (the
Mac-wide parts of Claude Code's item, kept for a put-back into no item at all; counted with the recovery copies),
plus owner-only recovery files under <data>/recovery when the Keychain refused one. tidy --all removes each kept
account with api.remove first; what that leaves is a safety copy, a successor kept for an account removed while it
was refreshing, the Mac-wide parts, and whatever an earlier removal could not delete. Once the uninstaller deletes the
data folder, nothing could find those again.

remove_all() finds every item its own records know: the accounts kept, the accounts removed earlier, the refreshes
and upkeep it notes, and every recovery entry (the manifest and the files beside it). Each item is looked for first
(without reading it, so no dialog), and each delete is checked. Claude Code's own item, the live login, is never
touched, and neither is a run's private item (runas.py removes its own).

It refuses, changing nothing, inside a `switchboard run` session, while a run holds an account, and while a switch
that was cut off can't be settled yet (its safety copy may be the only copy of a login). An item that can't be
removed is named, and the records that find it stay, so the uninstaller keeps the program and can run again.
"""
import os

from . import keychain, roster, runas, switcher, vault
from .api import ABSENT, PRESENT, AccountsError

# A failed delete's reason: the plain words for the refusal, and the AccountsError kind that says what to do.
_WHY = {'locked': ('it is locked', 'keychain_locked'),
        'timeout': ("it didn't answer in time", 'keychain_slow'),
        'busy': ("it didn't answer in time", 'keychain_slow'),
        'dialog': ('macOS is waiting for you to answer a Keychain dialog', 'keychain_slow'),
        'denied': ('it refused', 'keychain_denied'),
        'no_keychain': ("the login Keychain isn't available to Switchboard here", 'other'),
        'no_tool': ('its tool, /usr/bin/security, is missing', 'other'),
        'test_run': (keychain.TEST_RUN_WORDS, 'other')}
_WHAT = {vault.SERVICE_CURRENT: "%s's saved login", vault.SERVICE_PREVIOUS: "%s's earlier login"}


def _take_out(ctx, service, account):
    """One item out of the Keychain: (was_there, why). was_there: True, False, or None when the look got no answer
    (the delete is tried all the same); why: None when the item is gone now, else why it is not."""
    seen = ctx.keychain.exists(service, account)
    if seen.kind == ABSENT:
        return False, None
    done = ctx.keychain.delete(service, account)
    return (True if seen.kind == PRESENT else None), (None if done.ok else (done.why or 'unknown'))


def _names(failed):
    """What could not be removed, in a few words: "ana@example.com's earlier login and 2 recovery copies"."""
    copies = sum(1 for name in failed if name is None)
    names = [name for name in failed if name is not None]
    if copies:
        names.append('a recovery copy' if copies == 1 else '%d recovery copies' % copies)
    return names[0] if len(names) == 1 else '%s and %s' % (', '.join(names[:-1]), names[-1])


def _email(doc, aid):
    for part in ('accounts', 'removed'):
        a = doc[part].get(aid)
        if isinstance(a, dict) and a.get('email'):
            return a['email']
    return aid


def _sweep(ctx, service, limit=1000):
    """Every item left under one of Switchboard's own services, found by the service alone: one whose record was lost
    (an account list that could not be read, a manifest line that was never written) would stay otherwise. Returns
    (removed, (name or None, why) or None). Claude Code's own items are under other services and never looked at."""
    find = getattr(ctx.keychain, 'first_account', None)
    if find is None:
        return 0, None
    if os.path.realpath(ctx.data_dir) != os.path.realpath(os.path.join(ctx.home, '.switchboard')):
        # the services are the same for every data folder: a Switchboard kept elsewhere (SWITCHBOARD_HOME) may hold
        # the items this would find, so only the usual folder sweeps
        ctx.log('Did not look for Switchboard items without a record: this data folder is not the usual one')
        return 0, None
    removed, tried = 0, set()
    while removed < limit:
        seen = find(service)
        if seen.kind == ABSENT:
            return removed, None
        if seen.kind != PRESENT:
            if seen.why == 'no_keychain':               # as the record pass: 44 outside the login session is no answer
                ctx.log('Could not look for Switchboard items without a record under "%s" here' % service)
                return removed, None
            return removed, (None, seen.why or 'unknown')
        name = _WHAT[service] % seen.value if service in _WHAT else None
        if seen.value in tried:
            return removed, (name, 'unknown')            # removed, yet still found: stop rather than loop
        tried.add(seen.value)
        done = ctx.keychain.delete(service, seen.value)
        if not done.ok:
            return removed, (name, done.why or 'unknown')
        removed += 1
    return removed, (None, 'unknown')


def remove_all(ctx):
    """Every account and every login of Switchboard's in the Keychain. Returns one plain line per kind of thing removed
    ([] when there was nothing); AccountsError when it can't (see the module's notes)."""
    roster._refuse_inside_run(ctx)
    runas.reap_stale_claims(ctx)
    if switcher.read_journal(ctx)[0].kind != ABSENT:
        note = switcher.resolve_journal(ctx)
        if switcher.read_journal(ctx)[0].kind != ABSENT:
            raise AccountsError('other', '%s Nothing was removed; run this again then.'
                                % (note or 'A switch was cut off and is not settled yet.'))
    failed, removed_here, others = [], [], 0
    try:
        with vault.switch_lock(ctx, timeout=30.0), vault.account_lock(ctx, timeout=10.0):
            doc = roster._load(ctx)
            if doc['claims']:
                raise AccountsError('claimed', '%s is in use by a `switchboard run`; run this again once it ends. '
                                               'Nothing was removed.' % _email(doc, sorted(doc['claims'])[0]))
            entries = dict((e['key'], e) for e in vault.stashed(ctx) if isinstance(e.get('key'), str) and e['key'])
            ids = set(doc['accounts']) | set(doc['removed']) | set(doc['refreshing']) | set(doc['upkeep'])
            ids |= set(e['account_id'] for e in entries.values()
                       if isinstance(e.get('account_id'), str) and e['account_id'])
            # each account's own items, then every recovery copy: a successor for each account, and each one listed
            keys = sorted(set('successor:' + aid for aid in ids) | set(entries))
            bad_ids, bad_keys = set(), set()
            for aid in sorted(ids):
                for service in (vault.SERVICE_CURRENT, vault.SERVICE_PREVIOUS):
                    was, why = _take_out(ctx, service, aid)
                    if why:
                        failed.append((_WHAT[service] % _email(doc, aid), why))
                        bad_ids.add(aid)
                    elif was and not (service == vault.SERVICE_CURRENT and aid in doc['accounts']):
                        others += 1
            for key in keys:
                was, why = _take_out(ctx, vault.SERVICE_RECOVERY, key)
                if why:
                    failed.append((None, why))              # a recovery copy
                    bad_keys.add(key)
                    continue
                others += 1 if was else 0
                if key in entries:
                    if os.path.isfile(os.path.join(vault._recovery_dir(ctx), vault._safe(key) + '.json')):
                        others += 1
                    vault.drop_stash(ctx, key)          # its file, and its line in the manifest
            shared_left = False                         # the Mac-wide parts kept for a put-back: a recovery copy too
            for item in vault.shared_services(ctx):
                was, why = _take_out(ctx, vault.SERVICE_SHARED, item)
                if why:
                    failed.append((None, why))
                    shared_left = True
                    continue
                others += 1 if was else 0
            if not shared_left:
                vault.forget_shared(ctx)
            if not failed:                              # (one that failed keeps its record, for the next run)
                for service in (vault.SERVICE_CURRENT, vault.SERVICE_PREVIOUS, vault.SERVICE_RECOVERY,
                                vault.SERVICE_SHARED):
                    n, bad = _sweep(ctx, service)
                    others += n
                    if bad:
                        failed.append(bad)
                        break
            for aid in sorted(ids - bad_ids):
                if 'successor:' + aid in bad_keys:
                    continue
                if aid in doc['accounts']:
                    email = _email(doc, aid)
                    doc['accounts'].pop(aid)
                    doc['removed'][aid] = {'at': ctx.wall(), 'email': email}
                    removed_here.append(email)
                doc['order'] = [x for x in doc['order'] if x != aid]
                for k in ('upkeep', 'refreshing'):
                    doc[k].pop(aid, None)
            vault.save(ctx, doc)
    except vault.LockBusy:
        raise AccountsError('other', 'Switchboard is busy; try again in a moment. Nothing was removed.')
    roster.forget_live(ctx)
    said = []
    for email in removed_here:
        ctx.say('Removed %s from Switchboard' % email, 'info', h='removed', account=email, by='you')
        said.append('Account %s: removed, with its saved login.' % email)
    if others:
        said.append('Earlier logins and recovery copies: %d removed.' % others)
    ctx.log('Removed everything for the uninstaller: %d accounts, %d other logins, %d not removed'
            % (len(removed_here), others, len(failed)))
    if failed:
        words, kind = _WHY.get(failed[0][1], ('it answered %s' % failed[0][1], 'other'))
        raise AccountsError(kind, "Couldn't take %s out of the Keychain: %s." % (_names([n for n, _ in failed]), words))
    return said
