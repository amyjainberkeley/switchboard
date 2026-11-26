"""`switchboard run`'s choices, and the folders that run as one account (accounts/folders.py keeps them).

  switchboard run [NAME] [--bare] [--no-history|--history] [--strict] -- [claude arguments]
  switchboard folder [NAME|off]
  switchboard folders [--check|--prune] [--json]

A run without NAME runs as the account set for this folder or the nearest folder above it; with none, it prints its
usage and exits 2. --no-history shares all of the person's Claude Code folder but the chat history, so the chat
goes when the run ends (runfolder.HISTORY); the `run_history` setting (`shared`, the default, or `apart`) makes that
the usual way, and --history asks for the shared way for one run. --strict never starts Claude Code on the person's
own folder: a run as the account Claude Code is on refuses, in one line, with exit code STRICT_EXIT. --no-history
named for that account refuses the same way, since the chat would be kept; with only the setting, that run goes
ahead and its first line says the chat is kept.

Everything a run says goes to standard error, so a `-p` answer stays alone on standard output. Auto-switch never
reads a folder's account.
"""
import json
import os
import sys
import time

from . import connect, core, engine, names
from .accounts import folders

RUN_USAGE = 'switchboard run [NAME] [--bare] [--no-history|--history] [--strict] -- [claude arguments]'
FOLDER_USAGE = 'switchboard folder [NAME|off]'
FOLDERS_USAGE = 'switchboard folders [--check|--prune] [--json]'
RUN_FLAGS = ('--bare', '--no-history', '--history', '--strict')
SETTING = 'run_history'
SHARED, APART = 'shared', 'apart'
STRICT_EXIT = 6
SCHEMA = 1


def history_setting():
    """The `run_history` setting: APART, or SHARED (the default, and anything else)."""
    return APART if core.settings().get(SETTING) == APART else SHARED


def set_history(value):
    """Set run_history to SHARED or APART. Raises SwitchError for any other value. Returns the line to say."""
    if value not in (SHARED, APART):
        raise core.SwitchError('run_history takes %s or %s.' % (SHARED, APART))
    core.set_setting(SETTING, value)
    if value == APART:
        return "Each switchboard run keeps its chat apart, in the run's own folder."
    return 'Each switchboard run keeps its chat with your other chats.'


def where(folder):
    """A folder as a person reads it: under the home folder with ~."""
    home = os.path.realpath(os.path.expanduser('~'))
    if folder == home:
        return '~'
    if home != os.sep and folder.startswith(home + os.sep):
        return '~' + folder[len(home):]
    return folder


def _here():
    """This folder, or None when it is gone."""
    try:
        return os.getcwd()
    except OSError:
        return None


def _accounts():
    """{account id: its name} of every account Switchboard keeps (its email, or its email and organization when two
    share one: names.of_account), or None when they can't be read."""
    try:
        return names.all_of(engine.accounts())
    except core.SwitchError:
        return None


def _email(entry, known=None):
    return (known or {}).get(entry.get('account')) or entry.get('email') or entry.get('account')


def runs_as_line(kept, entry, known=None):
    return 'This folder runs as %s (set for %s).' % (_email(entry, known), where(kept))


# ---------- switchboard run ----------

def run(args, usage_alone):
    """`switchboard run ...`. usage_alone: what `switchboard run` with nothing after it prints when no folder applies
    (the command's lines of the help). Returns the exit code."""
    head, rest = (args[:args.index('--')], args[args.index('--') + 1:]) if '--' in args else (args, [])
    name = head[0] if head and not head[0].startswith('-') else None
    flags = head[1:] if name is not None else head
    if any(f not in RUN_FLAGS for f in flags) or ('--history' in flags and '--no-history' in flags):
        print(RUN_USAGE)
        return 2
    kept = entry = None
    if name is None:
        here = _here()
        found = folders.nearest(here) if here is not None else None
        if found is None:
            print(usage_alone if not args else RUN_USAGE)
            return 2
        kept, entry = found
    if not connect.claude_bin():
        raise core.SwitchError('Claude Code is not installed on this Mac. Install it first: '
                               'https://claude.com/claude-code')
    bare = '--bare' in flags
    named_apart = '--no-history' in flags
    apart = not bare and (named_apart or (history_setting() == APART and '--history' not in flags))
    target = name if entry is None else entry['account']

    def first_line(plain):
        said = []
        known = _accounts() if entry is not None else None
        if entry is not None:
            said.append('Running as %s: set for %s.' % (_email(entry, known), where(kept)))
        if apart and not plain:
            said.append('This chat is not kept: it goes when Claude Code ends.')
        elif apart:
            email = _email(entry, known) if entry is not None else \
                names.of_account(engine.find(name), engine.accounts())
            said.append('This chat is kept: %s is the account Claude Code is on, so the run uses your own Claude '
                        'Code folder.' % email)
        if said:
            print(' '.join(said), file=sys.stderr)
            sys.stderr.flush()
    try:
        return engine.run_as(target, rest, cwd=os.getcwd(), bare=bare, apart=apart,
                             strict='--strict' in flags or named_apart, before=first_line,
                             say=lambda text: print(text, file=sys.stderr))
    except engine.Refused as e:
        if e.detail == 'in_use':
            print(str(e), file=sys.stderr)
            return STRICT_EXIT
        if e.detail == 'not_found' and entry is not None:
            raise core.SwitchError('This folder runs as %s (set for %s), which Switchboard no longer has. '
                                   'switchboard folders --prune takes it out.' % (_email(entry), where(kept)))
        raise


# ---------- switchboard folder ----------

def folder(args):
    """`switchboard folder [NAME|off]`."""
    if len(args) > 1 or (args and args[0].startswith('-')):
        print(FOLDER_USAGE)
        return 2
    here = _here()
    if not args:
        found = folders.nearest(here) if here is not None else None
        if found is None:
            print('No account is set for this folder. switchboard folder NAME sets one.')
            return 0
        known = _accounts()
        print(runs_as_line(found[0], found[1], known))
        if known is not None and found[1].get('account') not in known:
            print('Switchboard no longer has that account. switchboard folders --prune takes it out.')
        return 0
    if core.DEMO:
        raise core.SwitchError('The demo has made-up accounts; nothing is set.')
    if here is None or not os.path.isdir(here):
        raise core.SwitchError('This folder is gone, so nothing was set.')
    if args[0] == 'off':
        gone = folders.clear(here)
        if gone is None:
            print('This folder has no account of its own.')
        else:
            print('This folder no longer runs as %s.' % _email(gone, _accounts()))
        above = folders.nearest(here)
        if above is not None:
            print('A folder above still applies. %s' % runs_as_line(above[0], above[1], _accounts()))
        return 0
    from . import bearers
    a = bearers.sign_in(args[0], 'folder')
    kept = folders.set_account(here, a.id, a.email, time.time())
    print('This folder (%s) runs as %s, with every folder in it: switchboard run starts Claude Code as it here.'
          % (where(kept), names.of_account(a, engine.accounts())))
    return 0


# ---------- switchboard folders ----------

def _rows():
    known = _accounts()
    rows = []
    for kept, e in folders.entries():
        rows.append({'kept': kept, 'entry': e, 'email': _email(e, known), 'folder_found': os.path.isdir(kept),
                     'account_found': None if known is None else e.get('account') in known})
    return rows


def _gone_words(r):
    gone = [w for w, ok in (('folder', r['folder_found']), ('account', r['account_found'])) if ok is False]
    return ' (%s gone)' % ' and '.join(gone) if gone else ''


def _doc_row(r):
    at = r['entry'].get('at')
    ok = isinstance(at, (int, float)) and not isinstance(at, bool) and 0 < at < 1e11
    return {'folder': r['kept'], 'account': names.email_of(r['email']),
            'organization': names.org_of(r['email']) or None,
            'set_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(round(at))) if ok else None,
            'folder_found': r['folder_found'], 'account_found': r['account_found']}


def _fits(r):
    """An entry fits while its folder is there and its account is not known to be gone."""
    return r['folder_found'] and r['account_found'] is not False


def _say_json(doc):
    print(json.dumps(doc, indent=1, ensure_ascii=False))


def folders_command(args):
    """`switchboard folders [--check|--prune] [--json]`. --check exits 1 when an entry no longer fits."""
    as_json = '--json' in args
    words = [a for a in args if a != '--json']
    if len(words) > 1 or any(w not in ('--check', '--prune') for w in words) or args.count('--json') > 1:
        if as_json:                             # one document on standard output, also for words it does not take
            from . import scripted
            return scripted.wrong_words(FOLDERS_USAGE)
        print(FOLDERS_USAGE)
        return 2
    what = words[0] if words else None
    rows = _rows()
    if what is None:
        if as_json:
            _say_json({'schema': SCHEMA, 'folders': [_doc_row(r) for r in rows]})
            return 0
        if not rows:
            print('No folder runs as an account. switchboard folder NAME sets the one you are in.')
        for r in rows:
            print('%s  %s%s' % (where(r['kept']), r['email'], _gone_words(r)))
        return 0
    bad = [r for r in rows if not _fits(r)]
    if what == '--check':
        if as_json:
            _say_json({'schema': SCHEMA, 'folders': [_doc_row(r) for r in bad]})
        elif not bad:
            print('Every folder here is there, and so is its account.')
        else:
            for r in bad:
                print('%s  %s%s' % (where(r['kept']), r['email'], _gone_words(r)))
            print('switchboard folders --prune takes %s out.' % ('it' if len(bad) == 1 else 'them'))
        return 1 if bad else 0
    try:
        if core.DEMO:
            raise core.SwitchError('The demo has made-up accounts; nothing is taken out.')
        gone = dict(folders.remove([r['kept'] for r in bad]))
    except core.SwitchError as e:
        if not as_json:
            raise
        _say_json({'schema': SCHEMA, 'removed': [], 'refused': str(e)})
        return 1
    out = [r for r in bad if r['kept'] in gone]
    if as_json:
        _say_json({'schema': SCHEMA, 'removed': [_doc_row(r) for r in out]})
        return 0
    if not out:
        print('Nothing to take out: every folder here is there, and so is its account.')
    for r in out:
        print('Took out %s  %s%s' % (where(r['kept']), r['email'], _gone_words(r)))
    return 0


# ---------- what `switchboard status` adds ----------

def this_folder(folder=None):
    """{'account': email, 'folder': the folder that set it} for this folder, or None."""
    here = folder if folder is not None else _here()
    found = folders.nearest(here) if here is not None else None
    if found is None:
        return None
    return {'account': _email(found[1], _accounts()), 'folder': found[0]}


def status_line(folder=None):
    """The one line `switchboard status` adds under the list at a terminal in such a folder, or None."""
    here = folder if folder is not None else _here()
    found = folders.nearest(here) if here is not None else None
    return runs_as_line(found[0], found[1], _accounts()) if found is not None else None
