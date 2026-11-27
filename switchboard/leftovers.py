"""`switchboard leftovers`: the logins Switchboard keeps aside, its recovery copies (accounts/recovery.py), none of them
an account's login in use. Each with a short id to type, the account it was kept for (its whole email) when that is
known, why, when, where (the Keychain, or a file), and what to do with it: "safe to clear", or what needs it and what
would free it. Never a secret: the engine's list holds none.

  switchboard leftovers [--json]
  switchboard leftovers clear ID... [--yes]      (an id from the list; it asks first, --yes answers)
  switchboard leftovers clear --all [--yes]      (every one that is safe to clear)
  switchboard leftovers clear ID... --only-copy [--yes]
                                                 (also one that is the only copy of a login that was in use)

A copy still needed is never cleared: the engine judges each again as it clears, under its own locks. The one
exception is a copy that is the only copy of a login that was in use, no saved account's and not Claude Code's own:
it goes only when it is named by id with --only-copy, after a question that says the login is then gone for good
(--yes answers that question; it never stands in for --only-copy). Exit codes of
`clear`: 0 every one asked for is cleared (with --all: every safe one; the needed ones stay, and are said); 1 one named
is still needed, names none, or could not be removed, or the answer was no; 2 wrong words.

  {"schema": 1, "leftovers": [{"id": "3f9a1c", "account": "maya@northwind.example",
    "why": "a renewed login that could not be saved", "kept_at": "2026-11-24T22:05:00Z", "kept_in": "keychain",
    "needed": true, "needed_by": "maya@northwind.example",
    "what_to_do": "needed: the only copy of the login for maya@northwind.example until that account signs in again"}]}
"""
import json
import time

from . import core, engine
from .accounts import api
from .core import SwitchError

FORMS = ('switchboard leftovers [--json]\nswitchboard leftovers clear ID...|--all [--yes]\n'
         'switchboard leftovers clear ID... --only-copy [--yes]')
FLAGS = ('--all', '--yes', '--only-copy')
SCHEMA = 1
INDENT = ' ' * 8
_KEYCHAIN = {'locked': 'the Keychain is locked. Unlock the Mac, then try again',
             'timeout': "the Keychain didn't answer in time; try again in a moment",
             'busy': "the Keychain didn't answer in time; try again in a moment",
             'dialog': 'macOS is waiting for you to answer a Keychain dialog',
             'denied': 'the Keychain refused',
             'file': 'its file could not be removed'}


def keychain_said(why):
    """What the Keychain's answer `why` (an engine word: locked, timeout, ...) means, as the end of a sentence."""
    return _KEYCHAIN.get(why, "the Keychain didn't keep it (it answered %s)" % why)


def rows():
    """The engine's list of recovery copies (api.leftovers)."""
    try:
        return api.leftovers()
    except api.AccountsError as e:
        raise engine.refused(e)


def _only_copy(r):
    """What an only copy is, as the end of "it is ..." (None while Claude Code's login can't be read to tell)."""
    if r.get('unsure'):
        return None
    if r.get('token'):                  # no account's sign-in: a one-year token, copied aside by an older switch
        return 'the only copy of a token that was in use, with no refresh token; Switchboard keeps no other copy of it'
    if r['needed_by']:
        return 'the only copy of a login %s was using' % r['needed_by']
    return 'the only copy of a login that was in use; its account is not known'


def what_to_do(r):
    """"safe to clear", or "needed: " and why, in a few words."""
    if not r['needed']:
        return 'safe to clear'
    if r['need'] == 'only_login':
        return 'needed: the only copy of the login for %s until that account signs in again' % (
            r['needed_by'] or 'an account')
    if r['need'] == 'only_copy':
        return 'needed: ' + (_only_copy(r) or "it may be the only copy of a login that was in use (Claude Code's "
                                               "login can't be read now)")
    if r['need'] == 'switch':
        return "needed: a switch that was cut off may put it back in Claude Code, until Switchboard settles that switch"
    if r['need'] == 'journal':
        return "needed: the record of a switch can't be read, and it may name this copy"
    return "needed: Switchboard's account list can't be read, so this can't be told"


def kept_said(r):
    """Why `clear` kept a needed copy, and what would free it: whole sentences, one form for each kind of need."""
    i = r['id']
    give_up = 'To give this copy up for good: switchboard leftovers clear %s --only-copy.' % i
    if r['need'] == 'only_login':
        return 'Kept %s: it is %s. %s' % (i, what_to_do(r)[len('needed: '):], (
            'To free it, sign in to %s again: switchboard add %s' % (r['needed_by'], r['needed_by']) if r['needed_by']
            else 'Run switchboard doctor.'))
    if r['need'] == 'only_copy':
        if r.get('unsure'):
            return ("Kept %s: Claude Code's login can't be read now, so whether this is the only copy of a login that "
                    "was in use can't be told. List it again once the Keychain answers. %s" % (i, give_up))
        if r.get('token'):
            return 'Kept %s: it is %s. %s' % (i, _only_copy(r), give_up)
        if r['needed_by']:
            return ('Kept %s: it is %s. To free it, sign in to %s again: switchboard add %s. %s'
                    % (i, _only_copy(r), r['needed_by'], r['needed_by'], give_up))
        return ('Kept %s: it is the only copy of a login that was in use, and its account is not known. To keep that '
                'account, add it with switchboard add. %s' % (i, give_up))
    if r['need'] == 'switch':
        return ('Kept %s: a switch that was cut off may still put it back in Claude Code. It is free once Switchboard '
                'settles that switch, at its next start or switch.' % i)
    if r['need'] == 'journal':
        return ("Kept %s: the record of a switch can't be read, and it may name this copy. It is free once "
                'Switchboard puts that record away, at its next start or switch.' % i)
    return ("Kept %s: Switchboard's account list can't be read, so whether anything needs it can't be told. Run "
            'switchboard doctor.' % i)


def _gone_said(gone):
    """The question before clearing only copies by name: what is given up, plainly."""
    if len(gone) == 1:
        r = gone[0]
        what = _only_copy(r)
        first = ('%s is %s.' % (r['id'], what) if what else
                 "%s may be the only copy of a login that was in use: Claude Code's login can't be read now to tell."
                 % r['id'])
        return first + ' Once it is cleared, that login is gone from this Mac for good.'
    return ('%s may each be the only copy of a login that was in use. Once they are cleared, those logins are gone '
            'from this Mac for good.' % ', '.join(r['id'] for r in gone))


def _when(t):
    return time.strftime('%Y-%m-%d %H:%M', time.localtime(t)) if t is not None else 'at a time not known'


def _iso(t):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t)) if t is not None else None


def document(listed):
    """`switchboard leftovers --json`."""
    return {'schema': SCHEMA, 'leftovers': [
        {'id': r['id'], 'account': r['email'], 'why': r['why'], 'kept_at': _iso(r['at']), 'kept_in': r['where'],
         'needed': r['needed'], 'needed_by': r['needed_by'], 'what_to_do': what_to_do(r)} for r in listed]}


def show(listed):
    if not listed:
        print('Switchboard holds no leftover logins.')
        return 0
    print('%d leftover login%s: copies Switchboard kept aside, none of them a login in use.'
          % (len(listed), '' if len(listed) == 1 else 's'))
    for r in listed:
        print()
        print('%s  %s' % (r['id'], r['email'] or 'account not known'))
        print('%s%s; kept %s, in %s' % (INDENT, r['why'], _when(r['at']),
                                         'the Keychain' if r['where'] == 'keychain' else 'a file'))
        print(INDENT + what_to_do(r))
    if any(not r['needed'] for r in listed):
        print()
        print('To clear one: switchboard leftovers clear ID (--all clears every one that is safe to clear).')
    return 0


def _ask(cli, text):
    if not cli.terminal():
        raise SwitchError('Add --yes to clear without being asked.')
    return input(text + ' [y/N] ').strip().lower() in ('y', 'yes')


def clear(words, cli):
    everything, yes, only_copy = '--all' in words, '--yes' in words, '--only-copy' in words and '--all' not in words
    ids = [w for w in words if w not in FLAGS]
    listed = rows()
    if everything:
        if not listed:
            print('Switchboard holds no leftover logins.')
            return 0
        picked = listed
    else:
        picked = []
        for w in ids:
            hit = next((r for r in listed if r['id'] == w.strip().lower()), None)
            if hit is None:
                print('No leftover login has the id %s; switchboard leftovers lists them.' % w)
                return 1
            if hit not in picked:
                picked.append(hit)
    gone = [r for r in picked if r['needed'] and only_copy and r['need'] == 'only_copy']   # given up by name
    safe = [r for r in picked if not r['needed']]
    kept = [r for r in picked if r['needed'] and r not in gone]
    res = {'cleared': [], 'kept': [], 'failed': [], 'unknown': []}
    if safe or gone:
        if not yes:
            if gone:
                question = _gone_said(gone) + (' Clear it?' if len(safe + gone) == 1 else
                                               ' Clear %d leftover logins?' % len(safe + gone))
            elif len(safe) == 1 and not everything:
                question = 'Clear %s (%s)?' % (safe[0]['id'], safe[0]['why'])
            else:
                question = 'Clear %d leftover login%s?' % (len(safe), '' if len(safe) == 1 else 's')
            if kept and (gone or len(safe) > 1 or everything):
                question += ' %d more %s still needed and stays.' % (len(kept), 'is' if len(kept) == 1 else 'are')
            if not _ask(cli, question):
                print('Nothing cleared.')
                return 1
        try:
            res = api.clear_leftovers([r['id'] for r in safe + gone], only_copy=bool(gone))
        except api.AccountsError as e:
            raise engine.refused(e)
    done = res['cleared']
    if len(done) == 1:
        print('Cleared %s.' % done[0]['id'])
    elif done:
        print('Cleared %d leftover logins: %s.' % (len(done), ', '.join(r['id'] for r in done)))
    for r in kept + res['kept']:                    # (the engine judged again: a copy can be needed by now)
        print(kept_said(r))
    for i in res['unknown']:                        # cleared meanwhile, by another command: nothing was cleared
        print('No leftover login has the id %s now; switchboard leftovers lists them.' % i)
    for r, why in res['failed']:
        print('Could not clear %s: %s.' % (r['id'], _KEYCHAIN.get(why, 'the Keychain answered %s' % why)))
    if res['failed'] or res['unknown'] or res['kept'] or (kept and not everything):
        return 1
    return 0


def main(args, cli):
    """`switchboard leftovers ...`; `cli` is the command's own module (its terminal())."""
    if core.DEMO:
        raise SwitchError('The demo has made-up accounts; it keeps no logins aside.')
    if args in ([], ['--json']):
        listed = rows()
        if args:
            print(json.dumps(document(listed), indent=1, ensure_ascii=False))
            return 0
        return show(listed)
    if args[:1] == ['clear'] and len(args) > 1:
        words = args[1:]
        ids = [w for w in words if w not in FLAGS]
        if all(words.count(f) <= 1 for f in FLAGS) and ('--all' in words) != bool(ids) and \
                not ('--all' in words and '--only-copy' in words) and not any(w.startswith('-') for w in ids):
            return clear(words, cli)
    print(FORMS)
    return 2


def doctor_line():
    """For `switchboard doctor`: "2 leftover logins (switchboard leftovers)", or None when there are none or they
    can't be read. Never raises."""
    try:
        n = len(api.leftovers())
    except Exception as e:  # noqa: BLE001 - a line for doctor: it never stops doctor
        core.log('reading the leftover logins: %r' % e)
        return None
    return '%d leftover login%s (switchboard leftovers)' % (n, '' if n == 1 else 's') if n else None
