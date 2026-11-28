"""The Memory page: one search box over every past chat on this Mac (packs/memory.py).

The index is brought up to date in the background, the way History's Work reads the history: the first visit
starts it and gets an answer at once ("reading"), never a wait. After that a search runs on the index as it is,
and the index is read again at most every five minutes (every half minute when the page asks for fresh results).
Only the chats' own words are indexed (your prompts and the text replies); the index is Switchboard's own file,
readable only by you, and nothing leaves the Mac.

Each result can be reopened in Claude Code with `cd <folder> && claude --resume <session id>`. Claude Code's CLI
reference (https://code.claude.com/docs/en/cli-reference, checked 2026-11-13), `--resume`, `-r`: "Resume a specific
session by ID or name ... When you pass a session ID, Claude Code searches the current project directory and its
git worktrees, then every other project on this machine. Before v2.1.223, the ID search covered only the current
project directory and its git worktrees." Starting in the chat's own folder works on both, and the chat reopens
where it was made. A result whose folder or id is not known gets no command.
"""
import os
import re
import shlex
import threading
import time

from . import core
from .packs import memory

EVERY = 300             # seconds: the index is brought up to date at most this often on its own
ASKED = 30              # and at most this often when the page asks
LIMIT = 20              # results shown
MARKS = ('\x02', '\x03')   # around a matching word in a snippet; the page makes them bold
SESSION_RE = re.compile(r'^[0-9A-Za-z][0-9A-Za-z._-]{0,127}\Z')
STATE = {'at': 0, 'running': False, 'failed': False, 'chats': None}
LOCK = threading.Lock()
KW = {}                 # tests point claude_dir at a made-up history


def _update():
    failed = False
    try:
        memory.update_index(memory.projects_root(KW.get('claude_dir')))
    except Exception as e:  # noqa: BLE001 - the page says it could not read the chats
        core.log('memory index: %r' % e)
        failed = True
    with LOCK:
        STATE.update(at=time.time(), running=False, failed=failed)


def ensure(fresh=False):
    """Start bringing the index up to date when it is due, or at once when its file is gone (erased by
    `python3 -m switchboard.packs.memory erase` in another process) and the last run did not fail, or failed over
    ASKED seconds ago (an erase while a run was writing fails that run); never waits."""
    with LOCK:
        age = time.time() - STATE['at']
        gone = (not STATE['failed'] or age >= ASKED) and not os.path.exists(memory.index_path())
        if not STATE['running'] and (not STATE['at'] or age >= EVERY or (fresh and age >= ASKED) or gone):
            STATE['running'] = True
            threading.Thread(target=_update, name='switchboard-memory-index', daemon=True).start()
        return dict(STATE)


def resume_command(folder, session):
    """The command that reopens one chat in Claude Code, or None when its folder or id is not known."""
    if not isinstance(folder, str) or not os.path.isabs(folder) or not isinstance(session, str) \
            or not SESSION_RE.match(session):
        return None
    return 'cd %s && claude --resume %s' % (shlex.quote(folder), session)


def short(path):
    h = os.path.normpath(core.home())
    if path and (path == h or path.startswith(h + os.sep)):
        return '~' + path[len(h):]
    return path


def _project_name(f):
    """A memory file's project in a few words: its folder name under projects/, without this Mac's home part."""
    name = f.get('project') or ''
    prefix = memory.project_folder_name(core.home()) + '-'
    return name[len(prefix):] if name.startswith(prefix) else (name or short(f.get('path') or ''))


def health_line():
    """One quiet line, only when a MEMORY.md is over what Claude Code loads at the start of a chat; else None."""
    try:
        h = memory.health(KW.get('claude_dir'))
    except Exception as e:  # noqa: BLE001 - a health line is never worth a broken page
        core.log('memory health: %r' % e)
        return None
    over = [f for f in h['files'] if f['state'] == 'over']
    if not over:
        return None
    f = over[0]
    lost = f.get('lines_not_loaded') or f.get('over_lines') or 0
    what = ('%d line%s of it do not load' % (lost, '' if lost == 1 else 's')) if lost else 'part of it does not load'
    more = ' (and %d more)' % (len(over) - 1) if len(over) > 1 else ''
    return ("Claude Code's memory for %s is over its limit: %s at the start of a chat%s."
            % (_project_name(f), what, more))


def view(query='', fresh=False):
    """What the Memory page draws: {'state': 'reading'|'ready'|'failed', 'updating', 'results', 'health'}."""
    if core.DEMO:
        from . import demo
        return demo.memory(query)
    st = ensure(fresh)
    # Until the first update in this run is done, the file may be a half-made index: say "reading" instead.
    have = os.path.exists(memory.index_path()) and (bool(st['at']) or not st['running'])
    out = {'state': 'failed' if st['failed'] and not st['running'] else ('ready' if have else 'reading'),
           'updating': st['running'], 'results': [], 'health': health_line(), 'query': query}
    q = (query or '').strip()[:200]
    if have and q:
        try:
            found = memory.search(q, limit=LIMIT, marks=MARKS)
        except Exception as e:  # noqa: BLE001 - the page says it can't read the chats, and the next read tries again
            core.log('memory search: %r' % e)
            out['state'] = 'failed'
            found = []
        # matched: the date of the best matching message (in a long chat, where the match is); alike: how many
        # look-alike chats (same title, same folder, such as a run repeated every day) were folded into this row.
        out['results'] = [{'title': r['title'], 'date': r['last'] or r['date'], 'folder': short(r['folder'] or ''),
                           'snippets': r['snippets'][:2], 'resume': resume_command(r['folder'], r['session']),
                           'matched': r.get('matched'), 'alike': r.get('alike') or 0, 'alike_last': r.get('alike_last')}
                          for r in found]
    return out
