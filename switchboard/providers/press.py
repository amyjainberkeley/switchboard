"""Another provider's row where a person looks (the menu bar's menu and the page): which chat would go, what such a
row holds, and a press on it.

The one rule of these faces: nothing starts in a terminal and nothing is sent. A press makes the note of the chat and
opens the provider's own desktop app on it through the app's documented link (contract.IN_APP): the note is in the
app's message box and the person presses send. Without that app the press gives the one line to run in a terminal
and does nothing else. So a request any program on this Mac could forge can at most open an app with a draft in it.
That is why the press is the person's yes for that one move, with no question, and why no yes is kept for the
folder: `continue`, `hand` and `do` still ask theirs. A stay or a never for the chat's folder is refused in one line.

The chat that would go is the newest chat Claude Code wrote on this Mac: the server hands its newest chats (from
chats.py, which reads every chat) and Claude Code's own reader here picks the first that is a person's chat, not a
run nobody watched. Only its name is shown.
"""
import dataclasses
import io
import os
from typing import List, Optional, Sequence, Tuple

from . import cli, contract, rows, words
from .cli import Refused
from .contract import Hop, ProviderError, Unsupported

CANDIDATES = 5                  # newest chats looked at for one a person had (a run nobody watched is passed over)
NAME_MAX = 60                   # a chat's name, cut to one line
MARK_LEN = 8                    # hex characters of a chat's mark


def _kit_in(kit: cli.Kit, folder: str) -> cli.Kit:
    """The kit for one folder, never at a terminal: a face has no terminal, whatever started Switchboard."""
    reach = dataclasses.replace(kit.reach, tty=lambda: False)
    return dataclasses.replace(kit, reach=reach, cwd=lambda: folder, ask=lambda prompt: '')


def _started_in(prov, path, cwd) -> str:
    """The folder a chat was started in, as its own first lines say (its adapter's started_in: the reader files it
    there); cwd, the folder it last named, when the file cannot tell."""
    try:
        said = prov.started_in(path) if prov is not None else None
    except Exception:  # noqa: BLE001 - a file it cannot read: the folder the chat last named
        said = None
    return said if isinstance(said, str) and os.path.isabs(said) else cwd


def chat_from(candidates: Sequence[Tuple[str, Optional[str], str]], kit: Optional[cli.Kit] = None):
    """The chat that would go: the first of candidates ((chat id, its file, the folder it last named), newest first)
    that Claude Code's own reader finds where it was started and that a person had (not a run nobody watched). (ChatRef,
    its folder) or None."""
    kit = kit if kit is not None else cli.default_kit()
    top = cli._Ctx(kit)
    source = top.prov(top.first())
    for cid, path, cwd in list(candidates)[:CANDIDATES]:
        if not isinstance(cid, str) or not contract.CHAT_ID_RE.match(cid) or not isinstance(cwd, str) or \
                not os.path.isabs(cwd):
            continue
        folder = os.path.realpath(_started_in(source, path, cwd))
        ctx = cli._Ctx(_kit_in(kit, folder))
        prov = ctx.prov(ctx.first())
        got = prov.chats(ctx.folder) if prov is not None else ()
        if isinstance(got, contract.Unsupported):
            continue
        for c in got:
            if c.id == cid and not (c.entrypoint or '').startswith(tuple(kit.headless)):
                return c, folder
    return None


def mark(cid) -> str:
    """A short mark of a chat, not its id: a face sends it back with a press, so the press carries the chat the face
    showed (the first characters of a hash of the id)."""
    import hashlib
    return hashlib.sha256(str(cid).encode('utf-8')).hexdigest()[:MARK_LEN]


def chat_words(chat) -> Optional[dict]:
    """What a face shows of the chat: its name only, on one line ({'name': None} for a chat with no name), and its
    mark; or None."""
    if chat is None:
        return None
    return {'name': words.one_line(chat.name, NAME_MAX) if chat.name else None, 'mark': mark(chat.id)}


def face_rows(found, kit: Optional[cli.Kit] = None, board: Optional[dict] = None) -> List[dict]:
    """rows.from_board for where the chat is (the home folder when no chat would go), each with what a face needs
    besides: 'chat' (chat_words), and 'can' only while a chat would go too (the row's own: the chat's folder lets it go
    there; 'why' says when it does not, also when that folder is too wide to start another app in:
    cli._too_wide). board: the board to derive them from (the server's last one); None builds
    it from files (view.view). [] when none is added or they cannot be worked out."""
    try:
        kit = kit if kit is not None else cli.default_kit()
        chat, folder = found if found else (None, kit.reach.home)
        k = _kit_in(kit, folder)
        if board is None:
            from . import view
            board = view.view(kit=k)
        out = rows.from_board(board, folder=folder, kit=k)
        wide = chat is not None and cli._too_wide(cli._Ctx(k), folder)
        for r in out:
            if wide and r.get('why') is None:
                r['why'] = words.ROW_TOO_WIDE
            r['chat'] = chat_words(chat)
            r['can'] = chat is not None and r.get('why') is None and r.get('can') is True
        return out
    except Exception as e:  # noqa: BLE001 - the faces show the Claude accounts without them
        try:
            from switchboard import core
            core.log('the provider rows for the faces: %s' % type(e).__name__)
        except Exception:  # noqa: BLE001
            pass
        return []


# ---------- the press ----------


def press(pid: str, chat: Optional[dict], number=None, kit: Optional[cli.Kit] = None) -> dict:
    """Continue here on pid's row: {'said': [lines]} when its desktop app is open on the chat's note (problems first,
    one line each, then PRESS_OPEN), or {'said': [line], 'run': the line to run in a terminal} when that app is not on
    this Mac or did not take its link (nothing else is done). chat: {'id', 'folder'} of the chat the faces show (the
    server's, never a request's). number: the row's number in the terminal's list, for the line to run. Raises
    Refused with one line: never, stay, not signed in, not on this Mac, the notice never said (cli.notice_due), no
    chat, too old, a note that could not be written. Never starts a program in a terminal and never sends anything."""
    kit = kit if kit is not None else cli.default_kit()
    fit = isinstance(chat, dict) and isinstance(chat.get('folder'), str) and os.path.isabs(chat['folder']) and \
        isinstance(chat.get('id'), str) and bool(contract.CHAT_ID_RE.match(chat['id']))
    ctx = cli._Quiet(dataclasses.replace(_kit_in(kit, chat['folder'] if fit else kit.reach.home), out=io.StringIO()))
    try:
        if not fit:
            raise Refused(words.PRESS_NO_CHAT)
        return _press(ctx, pid, chat['id'], number)
    except ProviderError as e:
        ctx.ended = 'problem'
        raise Refused(words.DISK if e.kind == 'disk' else words.one_line(str(e), 400))
    except Refused:
        raise
    finally:
        cli._prune(ctx)
        if not ctx.logs:
            ctx.log(words.LOG_PRESS_NOTHING.format(to=ctx.name(pid) if ctx.known(pid) else 'another provider',
                                                   why=words.LOG_WHY.get(ctx.ended, ctx.ended)))


def _desktop_app(found) -> Tuple[bool, Optional[str]]:
    """(is the provider's desktop app on this Mac, its bundle when a copy of the program inside it says where)."""
    inside = [p for p in (found.programs if found is not None else ()) if p.source == 'app']
    for p in inside:
        path = p.path
        while path and path != os.path.dirname(path):
            if path.endswith('.app'):
                return True, path
            path = os.path.dirname(path)
    return bool(inside), None


def _line(ctx, pid, number, why) -> dict:
    """The answer when the app cannot be opened: one line saying so, and the list's key press to run in the chat's
    folder (no question there: at a terminal the press is the yes)."""
    ctx.log(words.LOG_PRESS_LINE.format(to=ctx.name(pid), why=words.LOG_PRESS_LINE_WHY[why]))
    said = (words.PRESS_NO_APP if why == 'missing' else words.PRESS_NOT_OPENED).format(name=ctx.name(pid))
    line = ctx.kit.run.shell_line(['switchboard', str(number)], ctx.folder) if number else ''
    return {'said': [said], 'run': line or 'switchboard'}


def _log_moved(ctx, what, handoff, frm, to):
    ctx.log(words.LOG_PRESS_MOVED.format(to=ctx.name(to), what=words.LOG_MOVED_WHAT[what], frm=ctx.name(frm),
                                         hop=handoff.hop, size=words.size(handoff.bytes),
                                         places=words.places(cli._removed(handoff)), app=ctx.app(to),
                                         version=cli._version(ctx, to)))


def _press(ctx, pid, cid, number) -> dict:
    if not ctx.known(pid) or ctx.prov(pid) is None or pid == ctx.first():
        raise Refused(words.UNKNOWN_PROVIDER.format(word=words.one_line(str(pid), 32), ids=', '.join(ctx.ids())))
    ctx.must_be_added(pid)
    stay = ctx.stays()
    if stay and stay != pid:
        raise Refused(words.PRESS_STAYS.format(basename=ctx.basename, p=ctx.name(stay)))
    if ctx.consent(pid) == 'never':
        raise Refused(words.PRESS_NEVER.format(basename=ctx.basename, to=ctx.name(pid), to_id=pid))
    room = ctx.room(pid)
    if room.state == contract.NOT_INSTALLED:
        raise Refused(words.MOVE_NOT_INSTALLED.format(name=ctx.name(pid), vendor=ctx.vendor(pid)))
    if room.state == contract.SIGNED_OUT:
        raise Refused(words.SIGNED_OUT.format(name=ctx.name(pid), how=ctx.sign_in(pid)))
    if not os.path.isdir(ctx.folder):
        raise Refused(words.PRESS_NO_FOLDER)
    if cli._too_wide(ctx, ctx.folder):
        raise Refused(words.PRESS_TOO_WIDE.format(folder=words.one_line(ctx.folder, 200), name=ctx.name(pid)))
    if cli.notice_due(ctx, pid):                    # the list says the notice first; no note is made here
        raise Refused(words.PRESS_NOTICE_FIRST.format(name=ctx.name(pid), vendor=ctx.vendor(pid)))
    chat = next((c for c in cli._all_chats(ctx) if c.id == cid and c.provider == ctx.first()), None)
    if chat is None:
        raise Refused(words.PRESS_CHAT_GONE)
    has_app, bundle = _desktop_app(ctx.found(pid))
    if not has_app:
        return _line(ctx, pid, number, 'missing')
    ctx.refuse_too_old(pid)
    said = []
    if room.state == contract.OUT:
        said.append(words.MOVE_OUT.format(to=ctx.name(pid), when=words.when(room.back_at, ctx.now)) if room.back_at
                    else words.MOVE_OUT_NOT_KNOWN.format(to=ctx.name(pid)))
    work = cli._work(ctx, chat, pid, ask=False)
    if chat.working:
        said.append(words.STILL_WORKING)
    elif chat.live and chat.stopped:
        said.append(words.MAY_CARRY_ON.format(source=ctx.name(chat.provider)))
    handoff, task = cli._make_note(ctx, chat, pid, words.CONTINUE_WHY.format(to=ctx.name(pid)),
                                   ctx.kit.choices.handoff_budget(), where=work)
    info, part = ctx.read_info, False
    if info is not None and info.degraded:
        said.append(words.one_line(info.degraded, 300))
        part = True
    elif info is not None and info.stopped in ('bytes', 'time'):
        said.append(words.MOVE_PART_READ)
        part = True
    found = ctx.found(pid)
    hop = ctx.kit.tasks.add_hop(Hop(id=handoff.hop, task=task, kind='continue', at=ctx.now, folder=ctx.folder,
                                    from_provider=chat.provider, from_chat=chat.id, to_provider=pid,
                                    handoff=handoff.path, sha256=handoff.sha256, bytes=handoff.bytes,
                                    removed=cli._removed(handoff), state='made',
                                    program_version=found.program.version if found and found.program else None))
    ctx.moved = True
    try:
        launch = ctx.prov(pid).start(work, handoff, contract.IN_APP)
    except ProviderError:
        ctx.kit.tasks.update_hop(hop.id, state='failed')
        raise
    if isinstance(launch, Unsupported) or launch.how != contract.IN_APP or not launch.url:
        ctx.kit.tasks.update_hop(hop.id, state='failed')
        raise Refused(words.one_line(getattr(launch, 'why', '') or words.PRESS_NO_APP.format(name=ctx.name(pid)),
                                     300))
    ran = ctx.reach.open_url(launch.url, launch.app or bundle)
    if ran.code != 0:
        ctx.kit.tasks.update_hop(hop.id, state='failed')
        _log_moved(ctx, 'failed', handoff, chat.provider, pid)
        return _line(ctx, pid, number, 'failed')
    ctx.kit.tasks.update_hop(hop.id, state='in_app')
    cli._history_move(ctx, words.H_OPENED, handoff, pid, chat.provider)
    _log_moved(ctx, 'in_app', handoff, chat.provider, pid)
    said.append((words.PRESS_OPEN_PART if part else words.PRESS_OPEN).format(to=ctx.name(pid),
                                                                              chat=cli._chat_words(ctx, chat)))
    return {'said': said}
