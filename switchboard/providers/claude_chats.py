"""Claude Code's chats in a folder, and one chat as a Transcript, read from the chat's own file on this Mac.

Claude Code (2.1.309 to 2.1.330) keeps each chat as <claude dir>/projects/<project name>/<chat id>.jsonl, one JSON
object a line, and one small file a running window, <claude dir>/sessions/<pid>.json. The reader takes only what
chat_facts.py names, from the file's end (lines.back_lines), and never walks the lines' parent links: the file's own
order is the chat's order (no line on the checked versions had two of the person's lines as children).
"""
import dataclasses
import hashlib
import os
import re
from typing import Optional, Tuple

from . import chat_facts as facts
from . import contract, lines, words
from .contract import ChatRef, Event, ReadInfo, ReadLimits, Transcript

APP = 'Claude Code'
_WINDOW_RE = re.compile(facts.WINDOW_RE_TEXT)
# The head of a line skipped for its size, when it is a user line or an attachment (a queued message): the line's own
# "type" comes before its message in every line of 2.1.309-2.1.330, after its folder and ids (lines.SKIPPED_HEAD).
_PERSON_HEAD_RE = re.compile(rb'"type"\s*:\s*"(?:user|attachment)"')


def claude_dir(reach) -> str:
    """Claude's folder: CLAUDE_CONFIG_DIR when the command's environment sets it, else <home>/.claude."""
    given = reach.env.get('CLAUDE_CONFIG_DIR')
    return given if given and os.path.isabs(given) else os.path.join(reach.home, '.claude')


def project_name(folder: str) -> Optional[str]:
    """The name Claude Code gives a folder's project (its folder under projects/): every character outside a-z, A-Z
    and 0-9 made '-' (2.1.309). Past 200 characters it adds a hash of its own, not made here: None."""
    name = re.sub(r'[^a-zA-Z0-9]', '-', folder)
    return name if len(name) <= 200 else None


def _real(path) -> Optional[str]:
    try:
        return os.path.realpath(path) if isinstance(path, str) and os.path.isabs(path) else None
    except (OSError, ValueError):
        return None


# ---------- which chats ----------


def _windows(reach, cdir: str, folder: str) -> dict:
    """Running windows on a chat in folder: chat id -> the keys chat_facts.WINDOW_KEYS takes (and 'pid'). A window
    file of a process that is not alive is passed over. Only those keys are copied out; the loaded file (which holds
    the window's peerToken) is dropped at once. A <pid>.key file is never opened."""
    found = {}
    try:
        entries = list(os.scandir(os.path.join(cdir, 'sessions')))
    except OSError:
        return found
    for e in entries:
        if contract.LOGIN_NAME_RE.match(e.name) or not _WINDOW_RE.match(e.name):
            continue
        pid = int(e.name[:-5])
        if not reach.alive(pid):
            continue
        try:
            st = e.stat(follow_symlinks=False)
            if not e.is_file(follow_symlinks=False) or st.st_size > facts.WINDOW_MAX:
                continue
            with open(e.path, 'rb') as f:
                loaded = lines.loads(f.read(facts.WINDOW_MAX + 1))
        except OSError:
            continue
        if loaded is None:
            continue
        w = {k: loaded.get(k) for k in facts.WINDOW_KEYS}
        del loaded
        cid, cwd = w.get('sessionId'), w.get('cwd')
        if isinstance(cid, str) and contract.CHAT_ID_RE.match(cid) and _real(cwd) == folder:
            w['pid'] = pid
            found[cid] = w
    return found


def _project_dirs(cdir: str, folder: str) -> Tuple[str, ...]:
    projects = os.path.join(cdir, 'projects')
    name = project_name(folder)
    if name is not None:
        return (os.path.join(projects, name),)
    # Past 200 characters Claude Code shortens the name and adds a hash [not checked how]: every project folder that
    # begins with the first 200 characters is looked in, and each chat's own cwd decides.
    head = re.sub(r'[^a-zA-Z0-9]', '-', folder)[:200]
    try:
        return tuple(e.path for e in os.scandir(projects) if e.is_dir(follow_symlinks=False) and e.name.startswith(head))
    except OSError:
        return ()


def _tail_facts(reach, path: str) -> dict:
    """From the chat's last bytes: the cwd of its newest user line, its newest version and entrypoint, its newest
    custom and ai titles, and the error word when its newest reply was an error line."""
    out = {}
    it = lines.back_lines(path, contract.ReadLimits(max_bytes=facts.LISTING_TAIL, max_seconds=1.0), reach.mono)
    try:
        for raw, _ in it:
            d = lines.loads(raw) if raw else None
            if d is None:
                continue
            kind = d.get('type')
            for key, field in (('version', 'version'), ('entrypoint', 'entrypoint')):
                if key not in out and isinstance(d.get(field), str):
                    out[key] = d[field]
            if kind == 'user' and 'cwd' not in out and isinstance(d.get('cwd'), str):
                out['cwd'] = d['cwd']
            elif kind == 'assistant' and 'replied' not in out and d.get('isSidechain') is not True and \
                    (d.get('isApiErrorMessage') is True or not isinstance(d.get('message'), dict) or
                     d['message'].get('model') != facts.SYNTHETIC_MODEL):
                out['replied'] = True
                if d.get('isApiErrorMessage') is True:
                    out['stopped'] = lines.kind_name(d.get('error')) if d.get('error') else 'error'
            elif kind == 'custom-title' and 'custom' not in out and isinstance(d.get('customTitle'), str):
                out['custom'] = d['customTitle']
            elif kind == 'ai-title' and 'ai' not in out and isinstance(d.get('aiTitle'), str):
                out['ai'] = d['aiTitle']
    finally:
        it.close()
    return out


def _head_facts(path: str) -> dict:
    """From the chat's first lines: the cwd of the first that names one (the folder it was started in) and the first
    time stamp. Only the first chat_facts.START_LINES lines are looked at."""
    out = {'cwd': None, 'at': None}
    for raw in lines.head_lines(path, facts.START_BYTES, facts.START_LINES):
        d = lines.loads(raw) or {}
        if out['at'] is None:
            out['at'] = lines.iso_time(d.get('timestamp'))
        if isinstance(d.get('cwd'), str):
            out['cwd'] = d['cwd']
            break
    return out


def _name(value) -> Optional[str]:
    return value[:200] if isinstance(value, str) and value.strip() else None


def chats(reach, folder: str, now: float) -> Tuple[ChatRef, ...]:
    """Claude Code's chats started in folder: those open in a live window, and those written in the last
    contract.CHATS_DAYS days. A chat is kept only when its own lines say it was started in folder (the cwd of the
    first of its first lines that names one; a live window's file names it too), because one project name holds
    several folders (/x/my-site, /x/my_site and /x/my.site share one). work_folder: where its newest user line ran,
    when that is another folder that still exists (the assistant's shell moves). Stopped at a limit first, then live,
    then the most recently written."""
    folder = _real(folder)
    if folder is None:
        return ()
    cdir = claude_dir(reach)
    wins = _windows(reach, cdir, folder)
    files = {}
    for pdir in _project_dirs(cdir, folder):
        try:
            entries = list(os.scandir(pdir))
        except OSError:
            continue
        for e in entries:
            cid = e.name[:-6] if e.name.endswith('.jsonl') else ''
            if contract.CHAT_ID_RE.match(cid) and e.is_file(follow_symlinks=False):
                files.setdefault(cid, e.path)
    for cid in wins:
        if cid not in files:
            # The window's chat began in another folder's project: found one level down.
            try:
                for e in os.scandir(os.path.join(cdir, 'projects')):
                    p = os.path.join(e.path, cid + '.jsonl')
                    if e.is_dir(follow_symlinks=False) and os.path.isfile(p):
                        files[cid] = p
                        break
            except OSError:
                pass
    refs = []
    oldest = now - contract.CHATS_DAYS * 86400
    for cid, path in files.items():
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            continue
        w = wins.get(cid)
        if w is None and mtime < oldest:
            continue
        head = _head_facts(path)
        if w is not None:
            cwd = w['cwd']
        else:
            cwd = head['cwd']
            if _real(cwd) != folder:
                continue
        tail = _tail_facts(reach, path)
        work = _real(tail.get('cwd'))
        refs.append(ChatRef(
            provider='claude', id=cid, folder=cwd, path=path,
            name=_name(w.get('name') if w else None) or _name(tail.get('custom')) or _name(tail.get('ai')),
            started_at=head['at'], last_at=mtime, live=w is not None,
            working=now - mtime <= facts.WORKING_S or bool(w and w.get('status') in facts.WINDOW_BUSY),
            version=tail.get('version') or (w.get('version') if w and isinstance(w.get('version'), str) else None),
            entrypoint=(w.get('entrypoint') if w and isinstance(w.get('entrypoint'), str) else None)
            or tail.get('entrypoint'),
            stopped=tail.get('stopped'), work_folder=work if work and work != folder and os.path.isdir(work) else None))
    refs.sort(key=lambda r: (r.stopped is None, not r.live, -(r.last_at or 0)))
    return tuple(refs)


# ---------- one chat ----------


class _Cand:
    """A message in the person's place, decided once the whole read is known (origin mode or legacy mode)."""
    __slots__ = ('n', 'sub', 'text', 'full', 'whole', 'apps', 'at', 'queued', 'origin', 'turn', 'flags', 'tag',
                 'hop', 'parent', 'before')

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def _blocks_text(blocks) -> Tuple[list, int, int]:
    """(the text blocks' texts, pictures, unknown blocks) of a list of blocks the person or a tool wrote."""
    texts, pictures, odd = [], 0, 0
    for b in blocks:
        kind = b.get('type') if isinstance(b, dict) else None
        if kind in facts.USER_TEXT_BLOCKS and isinstance(b.get('text'), str):
            texts.append(b['text'])
        elif kind in facts.USER_PICTURE_BLOCKS:
            pictures += 1
        else:
            odd += 1
    return texts, pictures, odd


def _split_app_blocks(parts):
    """(the person's text, [(tag, block)] the app wrote): a text block that is one whole <TAG>...</TAG> element and
    not the last one is the app's (chat_facts.app_block); a line of one text block is never split."""
    if not parts or len(parts) < 2:
        return '\n'.join(parts or ()), []
    keep, apps = [], []
    for i, block in enumerate(parts):
        tag = facts.app_block(block) if i < len(parts) - 1 else None
        if tag is not None and tag not in facts.PERSON_BLOCKS:
            apps.append((tag, block))
        else:
            keep.append(block)
    return '\n'.join(keep), apps


class _Reader:
    def __init__(self, chat: ChatRef, limits: ReadLimits, mono):
        self.chat, self.limits, self.mono = chat, limits, mono
        self.g = lines.Gather()
        self.cands = []
        self.saw_origin = False
        self.first_origin = None    # n of the oldest line read that carries an origin dict (n counts back from the end)
        self.version = None
        self.titles = {}
        self.last_prompt = None
        self.calls = {}             # tool_use id -> (name, files, ref, index of the call's event in g.items)
        self.results = []           # (index in g.items, tool_use id)
        self.summary = None         # (n, text, at): the newest summary line seen, not yet paired
        self.boundary_n = None
        self.phase = 'main'
        self.before_bytes = 0
        self.before_people = 0
        self.enqueues = []          # n of each enqueue line without content
        self.lines = 0

    # --- the decision, once the mode is known ---

    def _person(self, c: _Cand, origin_mode: bool) -> Tuple[bool, Optional[str]]:
        """(PERSON or not, the NOTICE's origin word)."""
        if c.hop:
            return False, 'switchboard-note'
        first = facts.origin_word(c.origin) or facts.origin_word(c.turn)
        if c.tag:
            # A tag says more than a human or headless origin on the same line would.
            if first in facts.HUMAN_ORIGINS + facts.LEGACY_PERSON_TURN_ORIGINS:
                first = None
            return False, first or facts.origin_word(c.tag) or 'unknown'
        if origin_mode and self.first_origin is not None and c.n <= self.first_origin:
            if c.origin in facts.HUMAN_ORIGINS:
                return True, None
            return False, first or 'unknown'
        if c.flags:
            return False, first or 'unknown'
        if c.text.startswith(facts.LEGACY_APP_TEXTS):
            return False, 'app-message'
        return True, None

    def _add_cand(self, c: _Cand):
        if self.phase == 'before':
            if not self._person(c, self.saw_origin)[0]:
                return
            c.before = True
            self.before_people += 1
        self.cands.append(c)

    # --- one line ---

    def line(self, n: int, d: dict, size: int):
        kind = d.get('type')
        if self.version is None and isinstance(d.get('version'), str):
            self.version = d['version']
        if self.phase == 'before':
            self.before_bytes += size + 1
            if kind == 'user':
                self._user(n, d)
            elif kind == 'attachment':
                self._attachment(n, d)
            elif kind == 'system' and d.get('subtype') == facts.COMPACT_BOUNDARY:
                return 'stop'           # an older summary holds everything before it
            else:
                self.g.census.add('before-boundary', 'skipped')
            if self.before_people >= self.limits.keep_person_before_boundary or \
                    self.before_bytes >= self.limits.before_boundary_bytes:
                return 'stop'
            return None
        if kind == 'user':
            self.g.main += 1
            self._user(n, d)
        elif kind == 'assistant':
            self.g.main += 1
            self._assistant(n, d)
        elif kind == 'system':
            return self._system(n, d)
        elif kind == 'attachment':
            self._attachment(n, d)
        elif kind == 'queue-operation':
            op = lines.kind_name(d.get('operation'))
            self.g.census.add('queue-operation/' + op, 'skipped')
            if op == 'enqueue' and d.get('content') is None:
                self.enqueues.append(n)
        elif kind == 'last-prompt':
            if self.last_prompt is None and isinstance(d.get('lastPrompt'), str):
                self.last_prompt = (n, d['lastPrompt'])
                self.g.census.add('last-prompt', 'taken')
            else:
                self.g.census.add('last-prompt', 'skipped')
        elif kind in ('custom-title', 'ai-title'):
            key = 'customTitle' if kind == 'custom-title' else 'aiTitle'
            if kind not in self.titles and isinstance(d.get(key), str):
                self.titles[kind] = d[key]
                self.g.census.add(kind, 'taken')
            else:
                self.g.census.add(kind, 'skipped')
        elif kind in facts.CLAUDE_SKIPPED:
            self.g.census.add(kind, 'skipped')
        else:
            self.g.unknown('line:' + lines.kind_name(kind))
        return None

    def _saw_origin(self, n: int):
        self.saw_origin = True
        self.first_origin = n if self.first_origin is None else max(self.first_origin, n)

    def _flag(self, d: dict, key: str) -> bool:
        """A line's true-or-false mark. Any value but true, false or none is a shape the table does not know:
        counted, and read as not set."""
        v = d.get(key)
        if v is None or isinstance(v, bool):
            return v is True
        self.g.unknown('flag:' + key)
        return False

    def _user(self, n: int, d: dict):
        if self._flag(d, 'isSidechain'):
            self.g.census.add('user/sidechain', 'skipped')
            return
        if self._flag(d, 'isMeta'):
            self.g.census.add('user/meta', 'skipped')
            return
        msg = d.get('message')
        content = msg.get('content') if isinstance(msg, dict) else None
        if not isinstance(content, (str, list)):
            self.g.main_unknown += self.phase == 'main'
            self.g.unknown('user/shape')
            return
        at = lines.iso_time(d.get('timestamp'))
        if self._flag(d, 'isCompactSummary'):
            if self.phase == 'main' and self.summary is None:
                text = content if isinstance(content, str) else '\n'.join(_blocks_text(content)[0])
                self.summary = (n, text, at)
                self.g.census.add('user/compact-summary', 'taken')
            else:
                self.g.census.add('user/compact-summary', 'skipped')
            return
        origin = d.get('origin')
        if isinstance(origin, dict):
            self._saw_origin(n)
        text, pictures, sub, parts = content, 0, 0, None
        if isinstance(content, list):
            texts, odd = [], False
            for b in content:
                bkind = b.get('type') if isinstance(b, dict) else None
                if bkind == facts.TOOL_RESULT_BLOCK:
                    if self.phase == 'main':
                        self._tool_result(n, sub, b, at)
                    sub += 1
                elif bkind in facts.USER_TEXT_BLOCKS and isinstance(b.get('text'), str):
                    texts.append(b['text'])
                    self.g.census.add('block:text', 'taken')
                elif bkind in facts.USER_PICTURE_BLOCKS:
                    pictures += 1
                    self.g.census.add('block:' + bkind, 'counted')
                else:
                    odd = True
                    self.g.unknown('block:' + lines.kind_name(bkind), line=False)
            if odd:
                self.g.unknown_lines += 1
                self.g.main_unknown += self.phase == 'main'
            text, parts = '\n'.join(texts), texts
        self.g.images += pictures
        if not text and not pictures:
            self.g.census.add('user/empty', 'skipped')
            return
        flags = any(d.get(k) is True for k in facts.LEGACY_NOT_PERSON_FLAGS) or 'toolUseResult' in d or \
            (d.get('turnOrigin') is not None and d.get('turnOrigin') not in facts.LEGACY_PERSON_TURN_ORIGINS) or \
            d.get('promptSource') == 'system'
        self._text_cand(n, sub, text, pictures, at, queued=False,
                        origin=origin.get('kind') if isinstance(origin, dict) else None,
                        turn=d.get('turnOrigin'), flags=flags, parent=d.get('parentUuid'), parts=parts)
        self.g.census.add('user', 'taken')

    def _text_cand(self, n, sub, text, pictures, at, queued, origin, turn, flags, parent=None, parts=None):
        """A message in the person's place. Blocks the app wrote are split off first (they are used only when the
        message turns out to be the person's), so the machine-tag test looks at the person's own part."""
        own, apps = _split_app_blocks(parts) if parts else (text, [])
        if not own and pictures:
            own = words.PICTURE_ONLY
        if not text and pictures:
            text = words.PICTURE_ONLY
        kept, full = lines.keep(own, contract.KEEP_PERSON, both_ends=True)
        whole = text if apps else None
        self._add_cand(_Cand(n=n, sub=sub, text=kept, full=full, whole=whole,
                             apps=[(t, lines.keep(b, contract.KEEP_TOOL_RESULT)[0]) for t, b in apps],
                             at=at, queued=queued, origin=origin, turn=turn, flags=flags, tag=facts.tag_of(own),
                             hop=facts.note_hop(own), parent=parent if isinstance(parent, str) else None,
                             before=False))

    def _attachment(self, n: int, d: dict):
        a = d.get('attachment')
        if not isinstance(a, dict) or not isinstance(a.get('type'), str):
            self.g.unknown('attachment/shape')
            return
        if a.get('type') != facts.QUEUED_COMMAND:
            self.g.census.add('attachment/other', 'skipped')
            return
        origin = a.get('origin')
        if isinstance(origin, dict):
            self._saw_origin(n)
        prompt = a.get('prompt')
        if isinstance(prompt, str):
            text, pictures, parts = prompt, 0, None
        elif isinstance(prompt, list):
            parts, pictures, odd = _blocks_text(prompt)
            text = '\n'.join(parts)
            if odd:
                self.g.unknown('attachment/queued_command-block', line=False)
        else:
            self.g.unknown('attachment/queued_command-shape')
            return
        self.g.images += pictures
        if not text and not pictures:
            self.g.census.add('attachment/queued_command', 'skipped')
            return
        flags = a.get('commandMode') != 'prompt' or a.get('isMeta') is True or d.get('isMeta') is True
        self._text_cand(n, 0, text, pictures, lines.iso_time(a.get('timestamp') or d.get('timestamp')),
                        queued=True, origin=origin.get('kind') if isinstance(origin, dict) else None,
                        turn=None, flags=flags, parts=parts)
        self.g.census.add('attachment/queued_command', 'taken')

    def _tool_result(self, n, sub, b, at):
        content = b.get('content')
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            texts = []
            for inner in content:
                ikind = inner.get('type') if isinstance(inner, dict) else None
                if ikind == 'text' and isinstance(inner.get('text'), str):
                    texts.append(inner['text'])
                elif ikind in facts.RESULT_PICTURE_BLOCKS:
                    self.g.images += 1
                    self.g.census.add('result:' + ikind, 'counted')
                else:
                    self.g.unknown('result:' + lines.kind_name(ikind), line=False)
            text = '\n'.join(texts)
        else:
            self.g.unknown('block:tool_result-shape', line=False)
            return
        err = b.get('is_error')
        kept, full = lines.keep(text, contract.KEEP_TOOL_RESULT)
        self.results.append((len(self.g.items), b.get('tool_use_id')))
        self.g.add(n, sub, Event(contract.TOOL_RESULT, text=kept, at=at, full_len=full,
                                 ok=(not err) if isinstance(err, bool) else None,
                                 call=facts.call_id(b.get('tool_use_id'))))
        self.g.census.add('block:tool_result', 'taken')

    def _assistant(self, n: int, d: dict):
        if self._flag(d, 'isSidechain'):
            self.g.census.add('assistant/sidechain', 'skipped')
            return
        at = lines.iso_time(d.get('timestamp'))
        if self._flag(d, 'isApiErrorMessage'):
            word = d.get('error')
            if not isinstance(word, str) or not word:
                self.g.unknown('assistant/api-error-shape')
                word = 'error'
            self.g.add(n, 0, Event(contract.ERROR, text=lines.kind_name(word), at=at))
            self.g.census.add('assistant/api-error', 'taken')
            return
        msg = d.get('message')
        if isinstance(msg, dict) and msg.get('model') == facts.SYNTHETIC_MODEL:
            self.g.census.add('assistant/synthetic', 'skipped')     # the app's own filler, not the assistant's words
            return
        content = msg.get('content') if isinstance(msg, dict) else None
        if not isinstance(content, list):
            self.g.main_unknown += 1
            self.g.unknown('assistant/shape')
            return
        odd = False
        for sub, b in enumerate(content):
            bkind = b.get('type') if isinstance(b, dict) else None
            if bkind == 'text':
                text = b.get('text')
                if isinstance(text, str) and text.strip():
                    kept, full = lines.keep(text, contract.KEEP_PERSON, both_ends=True)
                    self.g.add(n, sub, Event(contract.ASSISTANT, text=kept, at=at, full_len=full))
                    self.g.census.add('block:text', 'taken')
                elif isinstance(text, str):
                    self.g.census.add('block:text-empty', 'skipped')
                else:
                    self.g.text_misses += 1
                    odd = True
                    self.g.unknown('block:text-shape', line=False)
            elif bkind == facts.TOOL_USE_BLOCK:
                if not self._tool_use(n, sub, b, at):
                    odd = True
            elif bkind in facts.ASSISTANT_REASONING_BLOCKS:
                self.g.reasoning += 1
                self.g.census.add('block:' + bkind, 'counted')
            else:
                odd = True
                self.g.unknown('block:' + lines.kind_name(bkind), line=False)
        if odd:
            self.g.unknown_lines += 1
            self.g.main_unknown += 1
        self.g.census.add('assistant', 'taken')

    def _tool_use(self, n, sub, b, at) -> bool:
        name, inp = b.get('name'), b.get('input')
        if not isinstance(name, str) or not isinstance(inp, dict):
            self.g.unknown('block:tool_use-shape', line=False)
            return False
        files = [inp[k] for k in facts.FILE_KEYS if isinstance(inp.get(k), str) and inp[k]]
        if name in facts.SHELL_TOOLS and isinstance(inp.get('command'), str):
            files.extend(f for f in lines.secret_words(_split(inp['command'])) if f not in files)
        ref = facts.web_ref(name, inp) if facts.is_web_tool(name) else None
        if isinstance(b.get('id'), str):
            self.calls[b['id']] = (name, tuple(files), ref, len(self.g.items))
        if name in facts.PLAN_TOOLS and isinstance(inp.get('todos'), list):
            rows = ['[%s] %s' % (t.get('status'), t.get('content')) for t in inp['todos']
                    if isinstance(t, dict) and isinstance(t.get('content'), str)]
            kept, full = lines.keep('\n'.join(rows), contract.KEEP_TOOL_INPUT)
            self.g.add(n, sub, Event(contract.PLAN, text=kept, at=at, name=name, full_len=full))
            self.g.census.add('block:tool_use-plan', 'taken')
            return True
        kept, full = lines.keep(lines.compact(inp), contract.KEEP_TOOL_INPUT)
        self.g.add(n, sub, Event(contract.TOOL_CALL, text=kept, at=at, name=name, files=tuple(files), ref=ref,
                                 full_len=full, call=facts.call_id(b.get('id'))))
        self.g.census.add('block:tool_use', 'taken')
        return True

    def _system(self, n: int, d: dict):
        if not isinstance(d.get('subtype'), str):
            self.g.unknown('system/shape')
            return None
        if d.get('subtype') != facts.COMPACT_BOUNDARY:
            self.g.census.add('system/other', 'skipped')
            return None
        self.g.census.add('system/compact_boundary', 'taken')
        at = lines.iso_time(d.get('timestamp'))
        self.g.add(n, 0, Event(contract.BOUNDARY, at=at))
        if self.summary is not None:
            sn, text, sat = self.summary
            if n - sn <= facts.SUMMARY_WITHIN:
                kept, full = lines.keep(text, contract.KEEP_PERSON, both_ends=True)
                self.g.add(sn, 0, Event(contract.SUMMARY, text=kept, at=sat, full_len=full))
            else:
                self.g.unknown('user/compact-summary-far')
            self.summary = None
        if self.limits.since_boundary:
            self.boundary_n = n
            self.phase = 'before'
        return None

    # --- the whole read ---

    def run(self, it) -> Transcript:
        first = True
        try:
            for raw, _ in it:
                n = self.lines
                self.lines += 1
                if not raw:
                    self.g.skipped_large += 1
                    # A message of the person's with its pictures can be this large: its head says so, and the
                    # census row's name lets the note say a message may be in it (the line itself is never read).
                    self.g.census.add('user/too-large' if _PERSON_HEAD_RE.search(it.skipped_head or b'')
                                      else 'too-large', 'counted')
                    first = False
                    continue
                d = lines.loads(raw)
                if d is None:
                    if first and it.unterminated:
                        self.g.torn_tail += 1
                        self.g.census.add('torn-tail', 'counted')
                    else:
                        self.g.unknown('unparsed')
                    first = False
                    continue
                first = False
                if self.line(n, d, len(raw)) == 'stop':
                    it.stopped = 'boundary'
                    break
        finally:
            it.close()
        return self.finish(it)

    def finish(self, it) -> Transcript:
        g = self.g
        if self.summary is not None:        # the read ended before its boundary (a cap): still the newest summary
            sn, text, sat = self.summary
            kept, full = lines.keep(text, contract.KEEP_PERSON, both_ends=True)
            g.add(sn, 0, Event(contract.SUMMARY, text=kept, at=sat, full_len=full))
        origin_mode = self.saw_origin
        g.census.add('mode/origin' if origin_mode else 'mode/legacy', 'taken')
        persons = []
        for c in self.cands:
            if origin_mode and c.n > self.first_origin:
                g.census.add('mode/legacy-before-origin', 'counted')
            person, origin = self._person(c, origin_mode)
            if person:
                persons.append(c)
            elif c.hop:
                text = words.NOTE_MARK_TEXT.format(hop=c.hop)
                g.add(c.n, c.sub, Event(contract.NOTICE, text=text, at=c.at, full_len=len(text), origin=origin))
            else:
                whole = c.whole if c.whole is not None else c.text
                text, full = lines.keep(whole, contract.KEEP_TOOL_RESULT)
                g.add(c.n, c.sub, Event(contract.NOTICE, text=text, at=c.at,
                                        full_len=c.full if c.whole is None else full, origin=origin))
        persons = _one_of_each_pair(persons)
        parents = {}
        for c in persons:
            for i, (tag, block) in enumerate(c.apps or ()):
                if tag in facts.APP_BLOCKS_SKIPPED:
                    g.census.add('user/app-block-skipped', 'counted')
                    continue
                if tag not in facts.APP_BLOCKS_CONTEXT:
                    g.census.add('user/app-block-unknown', 'counted')
                g.add(c.n, c.sub - 0.5 + i * 1e-3, Event(
                    contract.NOTICE, text=block, at=c.at, full_len=len(block),
                    origin='ide' if tag in facts.APP_BLOCKS_CONTEXT else 'app-block'))
            g.add(c.n, c.sub, Event(contract.PERSON, text=c.text, at=c.at, full_len=c.full, queued=bool(c.queued),
                                    before_boundary=bool(c.before)))
            if c.parent and not c.queued:
                parents[c.parent] = parents.get(c.parent, 0) + 1
        siblings = sum(1 for v in parents.values() if v > 1)
        if siblings:        # a rewind left a branch [not seen]: file order is kept, and it is counted
            g.census.add('user/sibling-person', 'counted', siblings)
        newest_person = min((c.n for c in persons), default=None)
        late = sum(1 for e in self.enqueues if newest_person is None or e < newest_person)
        if late:
            g.census.add('queue-operation/enqueue-after-last-person', 'counted', late)
        people = bool(persons)
        if not people and self.last_prompt is not None:
            ln, lp = self.last_prompt
            if lp.strip() and not facts.tag_of(lp) and not facts.note_hop(lp):
                kept, full = lines.keep(lp, contract.KEEP_PERSON, both_ends=True)
                g.add(ln, 0, Event(contract.PERSON, text=kept, full_len=full, before_boundary=True))
                g.census.add('last-prompt/used', 'taken')
        # The tool results get their call's name, files and address; a web tool's text is never kept.
        for idx, tid in self.results:
            n_, sub, e = g.items[idx]
            name, files, ref, at_call = self.calls.get(tid, ('', (), None, None)) if isinstance(tid, str) else \
                ('', (), None, None)
            if name in facts.CHANGING_TOOLS and e.ok is False:
                # The change was not made: no file of it is listed as changed, but one that usually holds a secret
                # stays named, so the note still leaves the call's text out.
                files = lines.secret_words(files)
                if at_call is not None:
                    cn, csub, call = g.items[at_call]
                    g.items[at_call] = (cn, csub, dataclasses.replace(call, files=files))
            if ref is not None or facts.is_web_tool(name):
                e = dataclasses.replace(e, text='', name=name, files=files, ref=ref or name)
            else:
                e = dataclasses.replace(e, name=name, files=files)
            g.items[idx] = (n_, sub, e)
        side = facts.version_side(self.version, facts.CLAUDE_CHECKED)
        if side:
            g.census.add('version/' + side, 'counted')
        degraded = None
        # A chat begun on Switchboard's note in which nothing was typed since has no person message, and is the usual
        # shape of a chat carried on here: the note's own first message stands for the person's (rule I2).
        began_on_note = any(c.hop for c in self.cands)
        if g.shape_unknown(facts.UNKNOWN_SHARE, facts.NEW_KIND_MIN, people or began_on_note,
                           self.boundary_n is not None, self.lines > 0):
            degraded = words.SHAPE_UNKNOWN.format(app=APP, version=self.version or 'unknown version')
        title = self.titles.get('custom-title') or self.titles.get('ai-title')
        info = ReadInfo(version=self.version, bytes_read=it.bytes_read, lines=self.lines,
                        from_end=it.stopped != 'start', stopped=it.stopped, unknown_lines=g.unknown_lines,
                        torn_tail=g.torn_tail, skipped_large=g.skipped_large, images=g.images,
                        reasoning=g.reasoning, title=title[:200] if title else None, degraded=degraded,
                        census=g.census.rows(), new_kinds=g.new_kinds(facts.NEW_KIND_MIN), reader=facts.CLAUDE_READER)
        return Transcript(chat=self.chat, events=g.events(), info=info)


def _one_of_each_pair(persons):
    """The person's messages in file order, with each queued_command whose text (sha256) is also a user line of the
    person's taken once: the queued copy is kept (it says when it was typed) and the user line is dropped, but only
    when it is the person's very next or very last message beside it (no other message of theirs between them) and
    lies within chat_facts.COPY_WITHIN lines of it. "yes" typed again later is a new message, and so is a picture
    sent alone (its text is only words.PICTURE_ONLY). Two user lines of the same text both stay."""
    persons = sorted(persons, key=lambda c: (-c.n, c.sub))
    digest = [hashlib.sha256(c.text.encode('utf-8', 'replace')).hexdigest() for c in persons]
    dropped = set()
    for i, c in enumerate(persons):
        if not c.queued or c.text == words.PICTURE_ONLY:
            continue
        for j in (i + 1, i - 1):
            if 0 <= j < len(persons) and j not in dropped and not persons[j].queued and \
                    digest[j] == digest[i] and abs(persons[j].n - c.n) <= facts.COPY_WITHIN:
                dropped.add(j)
                break
    return [c for i, c in enumerate(persons) if i not in dropped]


def _split(command: str):
    return lines.shell_words(command)


def read(reach, chat: ChatRef, limits: ReadLimits = ReadLimits()) -> Transcript:
    """One chat as a Transcript, from its file's end within limits: events oldest first, each from a row of
    chat_facts' table, and what the reader saw (ReadInfo). Never raises for a file it cannot read: an empty
    Transcript says so (degraded)."""
    it = lines.back_lines(chat.path, limits, reach.mono)
    return _Reader(chat, limits, reach.mono).run(it)
