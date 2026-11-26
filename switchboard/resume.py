"""`switchboard resume CHAT`: a chat opened again as the account it belongs to, with everything it had.

CHAT is a chat's id, or the start of one, among the chats in Claude Code's folder (core.projects()). The chat's own
file says which account it started under and the folder it ran in (chatfile.py, the one reader of those lines; the
saved look at every chat, chats.json, carries a long read on when the page wrote one). It opens as the account it
started under, or as the one --as names; an organization is matched to an account through the engine's own list.

On the account Claude Code is on, that is `claude --resume <id>` in the chat's folder. On another account it is the
same through `switchboard run` (runas.py): a private folder that links to the person's Claude Code folder, so the chat
is found, goes on writing to its own file, and has the same memory, instructions and hooks. Nothing else moves.

Never two writers: a chat that a running Claude Code window holds (Claude Code's own record of its running windows,
<its folder>/sessions/<pid>.json, names the chat and the process is alive; a run's private folder has its own), or
whose file, or one of its helpers' files beside it, was written in the last minute, or whose turn is open (a request
with no reply or a tool call with no result, as accounts/sessions.py tells, written in the last 15 minutes), is not
opened a second time. --fork opens a copy instead (Claude Code's --fork-session).
"""
import json
import os
import re
import sys
import time

from . import awake, chatfile, core, engine
from .accounts.api import PRESENT
from .core import SwitchError

USAGE = 'switchboard resume CHAT [--as NAME] [--fork] -- [claude arguments]'
CHAT_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z')
GIVEN_RE = re.compile(r'^[0-9a-fA-F-]{1,36}\Z')
WRITING = 60.0                  # a chat written this recently is open somewhere
HELPERS_MAX = 2000              # helper files looked at per chat
TAIL = 1 << 20                  # bytes read from a chat's end for its folder, when its account lines don't say
HEAD = 64 << 10                 # bytes read from a chat's start for its first login's account id, when a saved look lacks it
SHORT = 8
NO_CHAT = ('Name the chat to open: switchboard resume CHAT, with its id or the start of it. `switchboard chats` '
           'lists every chat with its id.')
TWO_WRITERS = ('That chat is open and working in another window. Two windows writing one chat can lose messages. '
               'Close it there, or add --fork to continue in a copy.')


class Usage(Exception):
    pass


def parse(args):
    """(chat or None, --as NAME or None, fork, claude arguments). Raises Usage."""
    args = list(args)
    extra = []
    if '--' in args:
        i = args.index('--')
        args, extra = args[:i], args[i + 1:]
    chat, as_name, fork = None, None, False
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--fork':
            fork = True
        elif a == '--as':
            if i + 1 >= len(args) or as_name is not None:
                raise Usage()
            as_name = args[i + 1]
            i += 1
        elif a.startswith('-') or chat is not None:
            raise Usage()
        else:
            chat = a
        i += 1
    return chat, as_name, fork, extra


# ---------- finding the chat ----------

def find(given, root=None):
    """(chat id, its file) for a chat id or the unique start of one. SwitchError: none, or more than one."""
    root = root or core.projects()
    want = (given or '').strip().lower()
    if not GIVEN_RE.match(want):
        raise SwitchError('"%s" is not a chat id. `switchboard chats` lists every chat with its id.' % given)
    hits = {}                                       # id -> [(mtime, path)]
    try:
        with os.scandir(root) as it:
            folders = [e.path for e in it if e.is_dir(follow_symlinks=False)]
    except OSError:
        folders = []
    for folder in folders:
        try:
            with os.scandir(folder) as it:
                for e in it:
                    name = e.name
                    if not name.endswith('.jsonl') or not name.startswith(want):
                        continue
                    cid = name[:-6]
                    if not CHAT_RE.match(cid) or not e.is_file(follow_symlinks=False):
                        continue
                    try:
                        t = e.stat(follow_symlinks=False).st_mtime
                    except OSError:
                        continue
                    hits.setdefault(cid, []).append((t, e.path))
        except OSError:
            continue
    if not hits:
        raise SwitchError('No chat on this Mac has an id that starts with "%s". `switchboard chats` lists every chat '
                          'with its id.' % given)
    if len(hits) > 1:
        ids = sorted(hits)
        raise SwitchError('More than one chat has an id that starts with "%s":\n%s\nGive more of the id.'
                          % (given, '\n'.join('  ' + c for c in ids[:20]) +
                             ('\n  and %d more' % (len(ids) - 20) if len(ids) > 20 else '')))
    cid = next(iter(hits))
    return cid, max(hits[cid])[1]           # the same chat in two folders: its newest file


def look(cid, path, data_dir=None):
    """What the chat's file says (chatfile.Read): carried on from the saved look when it has this chat."""
    saved = chatfile.load_index(chatfile.index_path(data_dir or core.data_dir()))
    entry = (saved.get('chats') or {}).get(cid) if saved else None
    if isinstance(entry, dict) and entry.get('file') == path:
        r = chatfile.resume_from(entry)
        r = chatfile.read(path, start=r.end, into=r)
        if r.owner is not None and r.owner_account is None:    # a saved look from before it kept the account id
            head = chatfile.read(path, max_bytes=HEAD)
            if head.owner == r.owner:
                r.owner_account = head.owner_account
        return r
    return chatfile.read(path)


def _cwd_from_tail(path):
    """The folder the chat's last lines say it was in, or None (only for a file whose account lines don't say)."""
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            f.seek(max(0, size - TAIL))
            lines = f.read(TAIL).split(b'\n')
    except OSError:
        return None
    for raw in reversed(lines[1:] if size > TAIL else lines):
        if b'"cwd"' not in raw or len(raw) > 4 << 20:
            continue
        try:
            d = json.loads(raw.decode('utf-8', 'replace'))
        except ValueError:
            continue
        cwd = d.get('cwd') if isinstance(d, dict) else None
        if isinstance(cwd, str) and os.path.isabs(cwd):
            return cwd
    return None


def _cwd_from_head(path):
    """The folder the chat's first lines say it was in, or None."""
    try:
        with open(path, 'rb') as f:
            lines = f.read(HEAD).split(b'\n')[:-1]
    except OSError:
        return None
    for raw in lines:
        if b'"cwd"' not in raw:
            continue
        try:
            d = json.loads(raw.decode('utf-8', 'replace'))
        except ValueError:
            continue
        cwd = d.get('cwd') if isinstance(d, dict) else None
        if isinstance(cwd, str) and os.path.isabs(cwd):
            return cwd
    return None


def project_name(folder):
    """The name Claude Code gives a folder's project (its folder under projects/, where a chat begun there is kept):
    the path with every character but a-z, A-Z and 0-9 made '-' (2.1.309). Past 200 characters it adds a hash of its
    own, not made here: None."""
    name = re.sub(r'[^a-zA-Z0-9]', '-', folder)
    return name if len(name) <= 200 else None


def folder_of(path, r):
    """The folder the chat ran in, as its file says, or None. Of the folders its lines name (its last lines, its last
    account line, its first lines), the one Claude Code keeps the chat under (the chat file's project folder is that
    folder's project_name), so `claude --resume` finds it there even after the chat moved to another folder; when
    none is, the folder its last lines name, else its last account line's."""
    named = [c for c in (_cwd_from_tail(path), r.cwd) if isinstance(c, str) and os.path.isabs(c)]
    project = os.path.basename(os.path.dirname(path))
    for c in named + [_cwd_from_head(path)]:
        if c and project_name(c) == project:
            return c
    return named[0] if named else None


def last_write(cid, path):
    """When the chat's file, or one of its helpers' files (<project>/<id>/...), was last written."""
    try:
        newest = os.stat(path).st_mtime
    except OSError:
        return None
    seen = 0
    stack = [os.path.join(os.path.dirname(path), cid)]
    while stack and seen < HELPERS_MAX:
        folder = stack.pop()
        try:
            with os.scandir(folder) as it:
                for e in it:
                    seen += 1
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    elif e.name.endswith('.jsonl'):
                        try:
                            newest = max(newest, e.stat(follow_symlinks=False).st_mtime)
                        except OSError:
                            pass
        except OSError:
            continue
    return newest


WINDOW_RE = re.compile(r'^[1-9][0-9]{0,9}\.json\Z')
WINDOW_MAX = 256 << 10           # Claude Code reads at most this much of one (2.1.309)


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (OSError, OverflowError):
        return False


def open_in_window(cid, folders=None):
    """Does a running Claude Code window hold this chat? Claude Code keeps one small file per running window,
    <its folder>/sessions/<pid>.json, with the chat it is on (sessionId) and more (seen in 2.1.309: cwd, startedAt,
    procStart, kind, status, entrypoint), and removes it when the window ends; a crash leaves one whose process is
    gone. Looked for in Claude Code's folder and in each run's private folder."""
    if folders is None:
        folders = [os.path.join(core.claude_dir(), 'sessions')]
        runs = core.data_path('run')
        try:
            folders += [os.path.join(runs, n, 'sessions') for n in sorted(os.listdir(runs))]
        except OSError:
            pass
    for folder in folders:
        try:
            with os.scandir(folder) as it:
                names = [e.name for e in it if WINDOW_RE.match(e.name) and e.is_file(follow_symlinks=False)]
        except OSError:
            continue
        for name in names:
            try:
                with open(os.path.join(folder, name), 'rb') as f:
                    raw = f.read(WINDOW_MAX + 1)
                d = json.loads(raw.decode('utf-8')) if len(raw) <= WINDOW_MAX else None
            except (OSError, ValueError, UnicodeDecodeError):
                continue
            if isinstance(d, dict) and d.get('sessionId') == cid and _alive(int(name[:-5])):
                return True
    return False


def in_use(cid, path, now=None):
    """Is the chat open somewhere: held by a running window, written in the last minute, or its turn open
    (accounts/sessions.py's rule: a turn open longer than its STUCK_CAP with nothing written is stuck, not working)."""
    from .accounts.sessions import STUCK_CAP
    if open_in_window(cid):
        return True
    now = time.time() if now is None else now
    t = last_write(cid, path)
    if t is None:
        return False
    if now - t < WRITING:
        return True
    return now - t < STUCK_CAP and awake.turn_state(path) == awake.OPEN


# ---------- the account ----------

def account_of(r, accounts):
    """(Account, None) for the account the chat started under, or (None, why): 'unknown' (its file doesn't say),
    'not_kept' (Switchboard doesn't keep it) or 'ambiguous' (more than one kept account is in its organization)."""
    if not r.owner:
        return None, 'unknown'
    same = [a for a in accounts if a.org_id and a.org_id == r.owner]
    if r.owner_account:
        exact = [a for a in same if a.id == r.owner_account or a.id.startswith(r.owner_account + '/')]
        if len(exact) == 1:
            return exact[0], None
        if not same:
            loose = [a for a in accounts if not a.org_id and a.id == r.owner_account]
            if len(loose) == 1:
                return loose[0], None
    if len(same) == 1:
        return same[0], None
    return None, 'ambiguous' if same else 'not_kept'


def _short(cid):
    return cid[:SHORT]


def opening_line(cid, email, how, plain, live_email, fork):
    """The one line said before Claude Code starts. how: 'own' (the account it started under) or 'as' (another
    account --as names). plain: Claude Code runs on the login it is on (no private folder)."""
    what = 'a copy of "%s"' % _short(cid) if fork else '"%s"' % _short(cid)
    if how == 'own':
        who = '%s, the account it started under' % email
    else:
        who = email
    if plain:
        return 'Opening %s as %s, which Claude Code is on now.' % (what, who)
    rest = ('Claude Code stays on %s everywhere else.' % live_email if live_email
            else 'Claude Code stays where it is everywhere else.')
    return 'Opening %s as %s. %s' % (what, who, rest)


def main(args, out=None):
    """`switchboard resume CHAT [--as NAME] [--fork] [-- ARGS]`. Returns Claude Code's exit code. SwitchError when
    it can't be opened (the words say why and what to do)."""
    out = out or sys.stderr                    # Claude Code's own output stays alone on stdout
    try:
        given, as_name, fork, extra = parse(args)
    except Usage:
        print(USAGE)
        return 2
    if not given:
        print(NO_CHAT)
        return 2
    cid, path = find(given)
    r = look(cid, path)
    folder = folder_of(path, r)
    if folder is None:
        raise SwitchError('Which folder that chat ran in is not known (its file doesn\'t say), so it can\'t be opened '
                          'where it ran.')
    if not os.path.isdir(folder):
        raise SwitchError('That chat ran in %s, which is gone, so it can\'t be opened there.' % folder)
    if not fork and in_use(cid, path):
        raise SwitchError(TWO_WRITERS)
    if as_name is not None:
        account = engine.find(as_name)
        how = 'as'
        start, _ = account_of(r, engine.accounts())
        if start is not None and start.id == account.id:
            how = 'own'
    else:
        account, why = account_of(r, engine.accounts())
        how = 'own'
        if account is None:
            if why == 'unknown':
                raise SwitchError('Which account that chat started under is not known: its file doesn\'t say. Name '
                                  'one: switchboard resume %s --as NAME' % given)
            if why == 'ambiguous':
                raise SwitchError('More than one account Switchboard keeps is in the organization that chat started '
                                  'under. Name one: switchboard resume %s --as NAME' % given)
            raise SwitchError('That chat started under an account Switchboard doesn\'t keep. Open it as one it does: '
                              'switchboard resume %s --as NAME' % given)
    live = engine.live_now()
    live_email = getattr(live, 'email', None) if live is not None and live.state == PRESENT else None
    argv = ['--resume', cid] + (['--fork-session'] if fork else []) + list(extra)

    def before(plain):
        out.write(opening_line(cid, account.email, how, plain, live_email, fork) + '\n')
        out.flush()

    def say(text):
        out.write(text + '\n')
        out.flush()
    return engine.run_as(account.id, argv, cwd=folder, before=before, say=say)
