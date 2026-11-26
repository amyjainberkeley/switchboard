"""The one way Switchboard's other assistants reach the rest of Switchboard beyond core and the account code: the
session hooks (a note for a Claude chat's next message) and the account code's check of whether a chat is open.

Each is imported here, when called, inside a try: on a Switchboard that lacks it (or whose hooks predate the note)
the answer is "not here" (False or None), never an error, so the command prints the line to paste instead. No other
module of this package imports switchboard.session_hooks or switchboard.resume.

The note file is the chat's own: <data>/hooks/notes/<session id>.json, mode 0600 in a 0700 folder, holding
{"v": 1, "text", "path", "hop", "made_at"}. Its text is fixed words, Switchboard's own times, the hop id and the
note's path (handoff.back_note): never a word of any chat, because the hooks put it above the chat's own messages.
The session hooks read it with the person's next message there, say it once and remove it (session_hooks.py).
"""
import importlib
import os
import re
import stat
import time
from typing import Optional

from switchboard import core

from .contract import CHAT_ID_RE, NOTE_DIR, NOTE_TEXT_MAX, NOTE_V

HOP_RE = re.compile(r'^h-[0-9a-f]{8}\Z')
PATH_MAX = 4096


def _module(name: str):
    """switchboard.<name>, or None when this Switchboard does not carry it or it fails to load. Through
    importlib, so a stand-in a test puts in sys.modules (or None there) is what answers."""
    try:
        return importlib.import_module('switchboard.' + name)
    except Exception:                   # any failure to load means "not here"
        return None


def hooks_can_deliver() -> Optional[bool]:
    """True when the session hooks deliver a provider's note (PROVIDER_NOTES), are on in Switchboard's settings, are
    in Claude Code's settings and can run, are this copy of Switchboard's own (they name this copy's folder), and
    Claude Code runs hooks at all. False when any of these says no (an older Switchboard's hooks lack
    PROVIDER_NOTES; the hooks installed belong to another copy). None when it cannot tell: the hooks did not load,
    or their own checks could not read what they read."""
    hooks = _module('session_hooks')
    if hooks is None:
        return None
    if getattr(hooks, 'PROVIDER_NOTES', False) is not True:
        return False
    try:
        if not hooks.hooks_on():
            return False
        st = hooks.status()
    except Exception:                   # their own failure: this side cannot tell
        return None
    if not isinstance(st, dict) or st.get('in') is None:
        return None
    if st.get('in') is False or not st.get('works') or st.get('off_by'):
        return False
    # The hooks Claude Code runs must be this copy's own: a command run from another copy of Switchboard (a build's
    # own folder beside the installed one) would leave a note where the installed hooks never look.
    from_here = getattr(hooks, 'hooks_run_from_here', None)
    if callable(from_here):
        try:
            mine = from_here()
        except Exception:               # its own failure: this side cannot tell
            return None
        if mine is not True:
            return False if mine is False else None
    return True


def put_note(session_id, text, path, hop, reach=None) -> bool:
    """Leave the note for the Claude chat session_id: its next message's hook says text once. False, and nothing
    written, when session_id is not a chat id, hop not a hop id, text or path not text, or the file cannot be
    written. reach (optional): its data folder and clock, else Switchboard's own."""
    if not isinstance(session_id, str) or not CHAT_ID_RE.match(session_id):
        return False
    if not isinstance(hop, str) or not HOP_RE.match(hop) or not isinstance(text, str) or not isinstance(path, str):
        return False
    try:
        data = reach.data_dir if reach is not None else core.data_dir()
        made_at = float(reach.wall() if reach is not None else time.time())
        folder = os.path.join(data, NOTE_DIR)
        for f in (os.path.dirname(folder), folder):
            os.makedirs(f, mode=0o700, exist_ok=True)
        if stat.S_IMODE(os.stat(folder).st_mode) != 0o700:
            os.chmod(folder, 0o700)
        target = os.path.join(folder, session_id + '.json')
        core.write_json(target, {'v': NOTE_V, 'text': text[:NOTE_TEXT_MAX], 'path': path[:PATH_MAX], 'hop': hop,
                                 'made_at': made_at})
        os.utime(target, (made_at, made_at))            # dated by its own clock, as the hooks' prune reads it
    except (OSError, ValueError, TypeError, RuntimeError):
        return False
    return True


def chat_in_use(cid, path) -> Optional[bool]:
    """Is the chat open somewhere, by the account code's own check (resume.in_use: a running window holds it, it
    was written in the last minute, or its turn is open)? None when this Switchboard has no such check, it fails,
    or cid is not a chat id; the adapter's own window check stands then."""
    if not isinstance(cid, str) or not CHAT_ID_RE.match(cid):
        return None
    resume = _module('resume')
    in_use = getattr(resume, 'in_use', None) if resume is not None else None
    if in_use is None:
        return None
    try:
        return bool(in_use(cid, path))
    except Exception:                   # its failure: this side cannot tell
        return None
