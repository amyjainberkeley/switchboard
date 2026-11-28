"""Codex's chats in a folder, one chat as a Transcript, its saved numbers, and the newest version that wrote its files.

Codex (0.153.4 to 0.169.1) keeps each thread as <codex home>/sessions/YYYY/MM/DD/rollout-<time>-<thread id>.jsonl
(older ones compressed as .jsonl.zst), in the folder of the day the thread began however long it goes on, one JSON
object a line ({timestamp, type, payload}); and an index of threads, <codex home>/session_index.jsonl ({id,
thread_name, updated_at}), read for the names only. The reader takes only what chat_facts.py names, from the file's
end. Nothing here starts Codex, reads its login, or opens its settings.
"""
import dataclasses
import hashlib
import json
import os
import platform
import re
import shlex
from typing import Optional, Tuple

from . import chat_facts as facts
from . import contract, lines, place, run, words
from .contract import ChatRef, Event, Limits, ReadInfo, ReadLimits, Transcript, Window

APP = 'Codex'
_ROLLOUT_RE = re.compile(r'^rollout-.*-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl(\.zst)?\Z')
_DIGITS_RE = re.compile(r'^[0-9]{2,4}\Z')
_TAG_RE = re.compile(r'^<([A-Za-z_][A-Za-z0-9_-]{0,63})')
# The head of a line skipped for its size: its first "type" key is the line's own (Codex writes {timestamp, type,
# payload} in that order: "type":"compacted" is inside the first 200 bytes of every compacted line seen, 0.169.1).
_HEAD_TYPE_RE = re.compile(rb'"type"\s*:\s*"([^"\\]{0,64})"')
_HEAD_TIME_RE = re.compile(rb'"timestamp"\s*:\s*"([^"\\]{1,64})"')
# ... and a person's message skipped for its size (a UserMessage item, or a user-role message) says so in its head.
_USER_ROLE_RE = re.compile(rb'"role"\s*:\s*"user"')


def codex_home(reach) -> str:
    """Codex's folder: CODEX_HOME when the command's environment sets it, else <home>/.codex."""
    given = reach.env.get('CODEX_HOME')
    return given if given and os.path.isabs(given) else os.path.join(reach.home, '.codex')


def _real(path) -> Optional[str]:
    try:
        return os.path.realpath(path) if isinstance(path, str) and os.path.isabs(path) else None
    except (OSError, ValueError):
        return None


def _tag(text: str) -> Optional[str]:
    m = _TAG_RE.match(text.lstrip())
    return m.group(1) if m else None


# ---------- where the files are ----------


def _scan(path):
    try:
        return list(os.scandir(path))
    except OSError:
        return []


def _rollouts(sessions: str) -> list:
    """Every rollout file under sessions/YYYY/MM/DD/, plain and compressed, with when it was last written:
    [(mtime, path, thread id)]. One listing a folder and one stat a file; no file is opened. A thread stays in the
    folder of the day it began, so the file written last can be under a folder weeks old (0.169.1: 26 days)."""
    out = []
    for y in _scan(sessions):
        if not (_DIGITS_RE.match(y.name) and y.is_dir(follow_symlinks=False)):
            continue
        for m in _scan(y.path):
            if not (_DIGITS_RE.match(m.name) and m.is_dir(follow_symlinks=False)):
                continue
            for d in _scan(m.path):
                if not (_DIGITS_RE.match(d.name) and d.is_dir(follow_symlinks=False)):
                    continue
                for e in _scan(d.path):
                    hit = _ROLLOUT_RE.match(e.name)
                    if not hit:
                        continue
                    try:
                        if not e.is_file(follow_symlinks=False):
                            continue
                        mtime = e.stat(follow_symlinks=False).st_mtime
                    except OSError:
                        continue
                    out.append((mtime, e.path, hit.group(1)))
    return out


def _names(reach) -> dict:
    """session_index.jsonl, newest line per thread: id -> its thread_name (None when it has none). Its updated_at is
    never used: it moves when a thread is named, not when it is written (0.169.1: the most used thread's was 26 days
    old while its file was written that day)."""
    path = os.path.join(codex_home(reach), 'session_index.jsonl')
    if not os.path.isfile(path):
        return {}
    out = {}
    it = lines.back_lines(path, ReadLimits(max_bytes=facts.INDEX_BYTES, max_seconds=1.0), reach.mono)
    try:
        for raw, _ in it:
            d = lines.loads(raw) if raw else None
            tid = d.get('id') if d else None
            if isinstance(tid, str) and contract.CHAT_ID_RE.match(tid) and tid not in out:
                out[tid] = d.get('thread_name') if isinstance(d.get('thread_name'), str) else None
    finally:
        it.close()
    return out


def _meta(path: str) -> Optional[dict]:
    """The facts of the file's first line when it is a session_meta line: only chat_facts.CODEX_META_KEYS (never the
    instructions, the account ids or the git facts)."""
    d = lines.loads(lines.head_line(path, facts.HEAD_MAX) or b'')
    if d is None or d.get('type') != 'session_meta' or not isinstance(d.get('payload'), dict):
        return None
    p = d['payload']
    meta = {k: p.get(k) for k in facts.CODEX_META_KEYS}
    meta['line_timestamp'] = d.get('timestamp')
    return meta


def _is_chat(meta: Optional[dict]) -> bool:
    """A thread a person started (thread_source 'user', source a word), with an id an argument list may carry."""
    return bool(meta) and meta.get('thread_source') == facts.CODEX_PERSON_THREAD and \
        isinstance(meta.get('source'), str) and isinstance(meta.get('id'), str) and \
        bool(contract.CHAT_ID_RE.match(meta['id']))


def _candidates(reach, now: float) -> list:
    """[(mtime, path)] of every thread file written in the last CHATS_DAYS days, wherever it is filed; one a thread
    (a plain file before a compressed one)."""
    oldest = now - contract.CHATS_DAYS * 86400
    best = {}
    for mtime, path, tid in _rollouts(os.path.join(codex_home(reach), 'sessions')):
        if mtime < oldest:
            continue
        have = best.get(tid)
        if have is None or (have[1].endswith('.zst') and not path.endswith('.zst')):
            best[tid] = (mtime, path)
    return list(best.values())


def _work_folder(reach, path: str, folder: str) -> Optional[str]:
    """The real path of the newest turn_context's cwd in the file's last bytes, when it is another folder than the
    chat's own (folder, a real path) and still a folder; else None. A compressed file is not read for it."""
    if path.endswith('.zst'):
        return None
    it = lines.back_lines(path, ReadLimits(max_bytes=facts.LISTING_TAIL, max_seconds=1.0), reach.mono)
    cwd = None
    try:
        for raw, _ in it:
            if b'turn_context' not in raw:
                continue
            d = lines.loads(raw)
            p = d.get('payload') if d and d.get('type') == 'turn_context' else None
            if isinstance(p, dict):
                cwd = p.get('cwd')
                break
    finally:
        it.close()
    real = _real(cwd)
    return real if real and real != folder and os.path.isdir(real) else None


def chats(reach, folder: str, now: float) -> Tuple[ChatRef, ...]:
    """Codex's chats that ran in folder (their own cwd), started by a person (never a helper's or a reviewer's
    thread), whose file was written in the last contract.CHATS_DAYS days, most recently written first. work_folder:
    the newest turn's folder when it is another one. A compressed one is listed only on a Python that can read it
    (see unreadable_compressed)."""
    folder = _real(folder)
    if folder is None:
        return ()
    refs, names = [], None
    for mtime, path in _candidates(reach, now):
        if path.endswith('.zst') and lines.zstd_module() is None:
            continue
        meta = _meta(path)
        if not _is_chat(meta) or _real(meta.get('cwd')) != folder:
            continue
        if names is None:
            names = _names(reach)
        name = names.get(meta['id'])
        refs.append(ChatRef(
            provider='codex', id=meta['id'], folder=meta['cwd'], path=path,
            name=name[:200] if isinstance(name, str) and name.strip() else None,
            started_at=lines.epoch(meta.get('timestamp')) or lines.epoch(meta.get('line_timestamp')),
            last_at=mtime, live=False, working=now - mtime <= facts.WORKING_S,
            version=meta.get('cli_version') if isinstance(meta.get('cli_version'), str) else None,
            entrypoint=meta['source'], work_folder=_work_folder(reach, path, folder)))
    refs.sort(key=lambda r: -(r.last_at or 0))
    return tuple(refs)


def unreadable_compressed(reach, now: float) -> int:
    """How many recent threads are kept compressed (.jsonl.zst) where this Python cannot read them (no
    compression.zstd): they are not listed, and the command says words.COMPRESSED."""
    if lines.zstd_module() is not None:
        return 0
    return sum(1 for _, p in _candidates(reach, now) if p.endswith('.zst'))


# ---------- one chat ----------


class _Msg:
    __slots__ = ('n', 'sub', 'text', 'full', 'at', 'client', 'tag', 'hop', 'before', 'retained')

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def _parts_text(parts, kind):
    """(the text of the parts of type kind joined by a newline, pictures, unknown parts)."""
    texts, pictures, odd = [], 0, 0
    for p in parts:
        ptype = p.get('type') if isinstance(p, dict) else None
        if ptype == kind and isinstance(p.get('text'), str):
            texts.append(p['text'])
        elif ptype in ('local_image', 'input_image', 'image'):
            pictures += 1
        else:
            odd += 1
    return '\n'.join(texts), pictures, odd


def _command_words(command):
    if isinstance(command, str):
        return _split(command)
    if isinstance(command, list) and all(isinstance(w, str) for w in command):
        return list(command)
    return None


def _shell_files(argv) -> Tuple[str, ...]:
    """The words of a command (and of the shell line after -c or -lc) that name a file a secret is usually in."""
    words_ = list(argv)
    for i, w in enumerate(argv[:-1]):
        if w in facts.CODEX_SHELL_FLAGS:
            words_.extend(_split(argv[i + 1]))
    return lines.secret_words(words_)


def _split(text: str):
    return lines.shell_words(text)


class _Reader:
    def __init__(self, chat: ChatRef, limits: ReadLimits):
        self.chat, self.limits = chat, limits
        self.g = lines.Gather()
        self.items_mode = False
        self.ums = []               # UserMessage items
        self.ums_retained = []      # the person's messages a compaction kept
        self.users = []             # response_item user-role messages
        self.responses = []         # response-mode tool events: (n, sub, Event, call id, is output)
        self.finals = []            # task_complete: (n, text, at)
        self.said = set()           # sha256 of every assistant text seen
        self.boundary = False       # a compaction with a readable summary was met
        self.marked = None          # n of the newest compaction with none: every event older is before_boundary
        self.phase = 'main'
        self.before_bytes = 0
        self.before_people = 0
        self.saw_client = False
        self.first_client = None    # n of the oldest UserMessage read that has client_id (n counts back from the end)
        self.lines = 0
        self.version = None

    def _no_client_rule(self, m: _Msg) -> bool:
        """A UserMessage with no client_id is the person's when the file wrote no client_id at or before it (so the
        app did not write one then) and it begins plain or with one of CODEX_PERSON_TAGS."""
        return (self.first_client is None or m.n > self.first_client) and \
            (m.tag is None or m.tag in facts.CODEX_PERSON_TAGS)

    def _likely_person(self, m: _Msg) -> bool:
        if m.hop:
            return False
        return bool(m.client) or self._no_client_rule(m)

    def _msg(self, n, sub, text, pictures, at, client, store):
        if not text and pictures:
            text = words.PICTURE_ONLY
        kept, full = lines.keep(text, contract.KEEP_PERSON, both_ends=True)
        m = _Msg(n=n, sub=sub, text=kept, full=full, at=at, client=client, tag=_tag(text),
                 hop=facts.note_hop(text), before=self.phase == 'before', retained=False)
        if self.phase == 'before':
            # A user-role message is only ever taken in a file with no UserMessage item: once one is seen, the
            # copies do not count toward the messages kept before the summary.
            if not self._likely_person(m) or (store is self.users and self.ums):
                return
            self.before_people += 1
        store.append(m)

    # --- one line ---

    def line(self, n: int, d: dict, offset: int, size: int):
        kind = d.get('type')
        p = d.get('payload')
        at = lines.iso_time(d.get('timestamp'))
        if self.phase == 'before':
            self.before_bytes += size + 1
            if kind == 'compacted':
                return 'stop'           # an older summary holds everything before it
            item = p.get('item') if isinstance(p, dict) and p.get('type') == 'item_completed' else None
            if kind == 'event_msg' and isinstance(item, dict) and item.get('type') == 'UserMessage':
                self._user_item(n, item, at)
            elif kind == 'response_item' and isinstance(p, dict) and p.get('type') == 'message' and \
                    p.get('role') == 'user':
                self._user_message(n, p, at)
            else:
                self.g.census.add('before-boundary', 'skipped')
            if self.before_people >= self.limits.keep_person_before_boundary or \
                    self.before_bytes >= self.limits.before_boundary_bytes:
                return 'stop'
            return None
        if kind == 'session_meta':
            if offset == 0 and isinstance(p, dict):
                self.g.census.add('session_meta', 'taken')
            else:
                self.g.census.add('session_meta/later', 'skipped')     # a helper's file holds its parent's too
        elif kind == 'turn_context':
            self.g.census.add('turn_context', 'taken')
        elif kind == 'event_msg':
            self.g.main += 1
            self._event(n, p, at)
        elif kind == 'response_item':
            self.g.main += 1
            self._response(n, p, at)
        elif kind == 'compacted':
            self._compacted(n, p, at)
        elif kind in facts.CODEX_SKIPPED_LINES:
            self.g.census.add(kind, 'skipped')
        else:
            self.g.unknown('line:' + lines.kind_name(kind))
        return None

    def _bad(self, kind):
        self.g.main_unknown += 1
        self.g.unknown(kind)

    def _event(self, n, p, at):
        if not isinstance(p, dict):
            return self._bad('event_msg/shape')
        ptype = p.get('type')
        if ptype == 'item_completed':
            item = p.get('item')
            if not isinstance(item, dict):
                return self._bad('event_msg/item-shape')
            return self._item(n, item, at)
        if ptype == 'task_complete':
            if 'last_agent_message' not in p or not isinstance(p['last_agent_message'], (str, type(None))):
                return self._bad('event_msg/task_complete-shape')
            text = p['last_agent_message']
            if isinstance(text, str) and text.strip():
                self.finals.append((n, text, at))
                self.g.census.add('event_msg/task_complete', 'taken')
            else:
                self.g.census.add('event_msg/task_complete', 'skipped')
            return None
        if ptype in facts.CODEX_SKIPPED_EVENTS:
            self.g.census.add('event_msg/' + ptype, 'skipped')
            return None
        return self._bad('event:' + lines.kind_name(ptype))

    def _user_item(self, n, item, at):
        parts = item.get('content')
        if not isinstance(parts, list):
            return self._bad('item:UserMessage-shape')
        text, pictures, odd = _parts_text(parts, 'text')
        if odd:
            self.g.unknown('part:UserMessage', line=False)
        self.g.images += pictures
        if not text and not pictures:
            self.g.census.add('item:UserMessage', 'skipped')
            return None
        client = isinstance(item.get('client_id'), str)
        self.saw_client = self.saw_client or client
        if client:
            self.first_client = n if self.first_client is None else max(self.first_client, n)
        self._msg(n, 0, text, pictures, at, client, self.ums)
        self.g.census.add('item:UserMessage', 'taken')
        return None

    def _item(self, n, item, at):
        itype = item.get('type')
        if itype == 'UserMessage':
            return self._user_item(n, item, at)
        if itype == 'CommandExecution':
            argv = _command_words(item.get('command'))
            if argv is None:
                return self._bad('item:CommandExecution-shape')
            self.items_mode = True
            cid = facts.call_id(item.get('id'))
            kept, full = lines.keep(shlex.join(argv), contract.KEEP_TOOL_INPUT)
            files = _shell_files(argv)
            self.g.add(n, 0, Event(contract.TOOL_CALL, text=kept, at=at, name='shell', files=files, full_len=full,
                                   call=cid))
            out = item.get('aggregated_output')
            if not isinstance(out, str):
                self.g.unknown('item:CommandExecution-output', line=False)
                out = ''
            code = item.get('exit_code')
            kept, full = lines.keep(out, contract.KEEP_TOOL_RESULT)
            ok = (code == 0) if isinstance(code, int) and not isinstance(code, bool) else None
            self.g.add(n, 1, Event(contract.TOOL_RESULT, text=kept, at=at, name='shell', ok=ok, files=files,
                                   full_len=full, call=cid))
            self.g.census.add('item:CommandExecution', 'taken')
            return None
        if itype == 'FileChange':
            changes = item.get('changes')
            if not isinstance(changes, dict):
                return self._bad('item:FileChange-shape')
            self.items_mode = True
            rows, files = [], []
            for path, change in changes.items():
                if not isinstance(path, str):
                    continue
                how = change.get('type') if isinstance(change, dict) else None
                rows.append('%s %s' % (how if how in ('add', 'update', 'delete') else 'change', path))
                files.append(path)
            if item.get('status') != 'completed':
                files = []              # a change not made (failed, declined) changed no file
                self.g.census.add('item:FileChange-not-made', 'counted')
            kept, full = lines.keep('\n'.join(rows), contract.KEEP_TOOL_INPUT)
            self.g.add(n, 0, Event(contract.TOOL_CALL, text=kept, at=at, name='file_change', files=tuple(files),
                                   full_len=full, call=facts.call_id(item.get('id'))))
            self.g.census.add('item:FileChange', 'taken')
            return None
        if itype == 'McpToolCall':
            server, tool = item.get('server'), item.get('tool')
            if not isinstance(server, str) or not isinstance(tool, str):
                return self._bad('item:McpToolCall-shape')
            self.items_mode = True
            name = '%s.%s' % (server, tool)
            args = item.get('arguments')
            if not isinstance(args, dict):
                self.g.unknown('item:McpToolCall-arguments', line=False)
                args = {}
            cid = facts.call_id(item.get('id'))
            ref = facts.web_ref(name, args) if facts.is_web_tool(name) else None
            kept, full = lines.keep(lines.compact(args), contract.KEEP_TOOL_INPUT)
            self.g.add(n, 0, Event(contract.TOOL_CALL, text=kept, at=at, name=name, ref=ref, full_len=full,
                                   call=cid))
            result = item.get('result')
            content = result.get('content') if isinstance(result, dict) else None
            error = item.get('error')
            if result is None and isinstance(error, dict) and isinstance(error.get('message'), str):
                # a call that failed before it had a result (0.169.1): the reason it gives stands for the result
                result, content = {}, [{'type': 'text', 'text': error['message']}]
                self.g.census.add('item:McpToolCall-failed', 'taken')
            elif not isinstance(content, list):
                self.g.unknown('item:McpToolCall-result', line=False)
                result, content = result if isinstance(result, dict) else {}, []
            text, pictures, odd = _parts_text(content, 'text')
            self.g.images += pictures
            if odd:
                self.g.unknown('part:McpToolCall-result', line=False)
            kept, full = lines.keep(text, contract.KEEP_TOOL_RESULT)
            if ref is not None:
                kept = ''               # a web or browser tool's text is never kept: only its address or name
            ok = item.get('status') == 'completed' and result.get('isError') is not True
            self.g.add(n, 1, Event(contract.TOOL_RESULT, text=kept, at=at, name=name, ok=ok, ref=ref, full_len=full,
                                   call=cid))
            self.g.census.add('item:McpToolCall', 'taken')
            return None
        if itype == 'ImageView':
            self.g.images += 1
            self.g.census.add('item:ImageView', 'counted')
            return None
        if itype == 'Reasoning':
            self.g.reasoning += 1
            self.g.census.add('item:Reasoning', 'counted')
            return None
        if itype in facts.CODEX_SKIPPED_ITEMS:
            self.g.census.add('item:' + itype, 'skipped')
            return None
        return self._bad('item:' + lines.kind_name(itype))

    def _user_message(self, n, p, at):
        parts = p.get('content')
        if not isinstance(parts, list):
            return self._bad('response_item/message-shape')
        text, pictures, _ = _parts_text(parts, 'input_text')
        if facts.codex_instructions(text):
            self.g.census.add('response_item/message-user-instructions', 'skipped')
            return None
        self.g.images += pictures
        if text or pictures:
            self._msg(n, 0, text, pictures, at, False, self.users)
        self.g.census.add('response_item/message-user', 'taken' if text or pictures else 'skipped')
        return None

    def _response(self, n, p, at):
        if not isinstance(p, dict):
            return self._bad('response_item/shape')
        ptype = p.get('type')
        if ptype == 'message':
            role = p.get('role')
            if role == 'user':
                return self._user_message(n, p, at)
            if role == 'assistant':
                parts = p.get('content')
                if not isinstance(parts, list):
                    return self._bad('response_item/message-shape')
                for sub, part in enumerate(parts):
                    if not isinstance(part, dict) or part.get('type') != 'output_text':
                        self.g.unknown('part:' + lines.kind_name(part.get('type') if isinstance(part, dict)
                                                                  else None), line=False)
                        continue
                    text = part.get('text')
                    if not isinstance(text, str):
                        self.g.text_misses += 1
                        self.g.unknown('part:output_text-shape', line=False)
                        continue
                    if text.strip():
                        self.said.add(hashlib.sha256(text.strip().encode('utf-8', 'replace')).hexdigest())
                        kept, full = lines.keep(text, contract.KEEP_PERSON, both_ends=True)
                        self.g.add(n, sub, Event(contract.ASSISTANT, text=kept, at=at, full_len=full))
                self.g.census.add('response_item/message-assistant', 'taken')
                return None
            if role in facts.CODEX_SKIPPED_ROLES:
                self.g.census.add('response_item/message-' + role, 'skipped')
                return None
            return self._bad('response_item/message-role')
        if ptype in ('function_call', 'custom_tool_call'):
            return self._call(n, p, at, ptype)
        if ptype in ('function_call_output', 'custom_tool_call_output'):
            return self._output(n, p, at, ptype)
        if ptype == 'reasoning':
            self.g.reasoning += 1
            self.g.census.add('response_item/reasoning', 'counted')
            return None
        if ptype in facts.CODEX_SKIPPED_RESPONSES:
            self.g.census.add('response_item/' + ptype, 'skipped')
            return None
        return self._bad('response:' + lines.kind_name(ptype))

    def _call(self, n, p, at, ptype):
        name = p.get('name')
        body = p.get('arguments') if ptype == 'function_call' else p.get('input')
        if not isinstance(name, str) or not isinstance(body, str):
            return self._bad('response_item/%s-shape' % ptype)
        files = ()
        kind = contract.TOOL_CALL
        text = body
        if ptype == 'function_call' and (name in facts.CODEX_PLAN_TOOLS or name in facts.CODEX_SHELL_CALLS):
            try:
                args = json.loads(body)
            except ValueError:
                args = None
            if isinstance(args, dict) and name in facts.CODEX_PLAN_TOOLS and isinstance(args.get('plan'), list):
                kind = contract.PLAN
                text = '\n'.join('[%s] %s' % (s.get('status'), s.get('step')) for s in args['plan']
                                 if isinstance(s, dict) and isinstance(s.get('step'), str))
            elif isinstance(args, dict) and name in facts.CODEX_SHELL_CALLS:
                argv = _command_words(args.get('command') if 'command' in args else args.get('cmd'))
                files = _shell_files(argv) if argv else ()
        elif ptype == 'custom_tool_call' and name in facts.CODEX_PATCH_TOOLS:
            found = []
            for row in body.splitlines():
                for mark in facts.CODEX_PATCH_MARKS:
                    if row.startswith(mark) and row[len(mark):].strip() not in found:
                        found.append(row[len(mark):].strip())
            files = tuple(found)
        ref = None
        if kind == contract.TOOL_CALL and facts.is_web_tool(name):
            try:
                args = json.loads(body) if ptype == 'function_call' else None
            except ValueError:
                args = None
            ref = facts.web_ref(name, args)
        kept, full = lines.keep(text, contract.KEEP_TOOL_INPUT)
        self.responses.append((n, 0, Event(kind, text=kept, at=at, name=name, files=files, ref=ref, full_len=full,
                                           call=facts.call_id(p.get('call_id'))),
                               p.get('call_id'), False, ptype))
        return None

    def _output(self, n, p, at, ptype):
        out = p.get('output')
        if isinstance(out, str):
            text = out
        elif isinstance(out, list):
            text, pictures, odd = _parts_text(out, 'input_text')
            self.g.images += pictures
            if odd:
                self.g.unknown('part:%s' % ptype, line=False)
        else:
            return self._bad('response_item/%s-shape' % ptype)
        kept, full = lines.keep(text, contract.KEEP_TOOL_RESULT)
        self.responses.append((n, 0, Event(contract.TOOL_RESULT, text=kept, at=at, ok=None, full_len=full,
                                           call=facts.call_id(p.get('call_id'))),
                               p.get('call_id'), True, ptype))
        return None

    def _marked(self, n, at):
        """A compaction with no readable summary: one BOUNDARY, and the read goes on past it, since the lines above it
        are still in the file as they were and nothing else stands for them. Its kept messages are not taken (they
        are those lines' own words: taking both would say them twice)."""
        self.g.add(n, 0, Event(contract.BOUNDARY, at=at))
        if self.marked is None:
            self.marked = n

    def _too_large(self, n, head: bytes) -> bool:
        """Whether a line skipped for its size is a compaction (12 of 21 seen were 5.4 to 7.8 MiB, all message '')."""
        m = _HEAD_TYPE_RE.search(head or b'')
        if not m or m.group(1) != b'compacted':
            return False
        self.g.census.add('compacted/too-large', 'counted')
        if self.phase == 'before':
            return True                 # an older compaction: the run stops there, as at any older one
        t = _HEAD_TIME_RE.search(head)
        self._marked(n, lines.iso_time(t.group(1).decode('ascii', 'replace')) if t else None)
        return True

    def _compacted(self, n, p, at):
        # Every compacted line seen (0.153.4 to 0.169.1, 21 in person-started threads) has message '': the app keeps
        # its summary encrypted (a response_item 'compaction' line). Only a readable one stops the read.
        if not isinstance(p, dict):
            p = {}
        message = p.get('message')
        if not isinstance(message, str) or not message.strip():
            if not isinstance(message, str):
                self.g.unknown('compacted/shape')
            else:
                self.g.census.add('compacted/empty', 'taken')
            if isinstance(p.get('retained_context'), dict):
                self.g.census.add('compacted/retained-not-taken', 'skipped')
            self._marked(n, at)
            return
        self.g.add(n, 0, Event(contract.BOUNDARY, at=at))
        self.g.census.add('compacted', 'taken')
        kept, full = lines.keep(message, contract.KEEP_PERSON, both_ends=True)
        self.g.add(n, 1, Event(contract.SUMMARY, text=kept, at=at, full_len=full))
        kept_ctx = p.get('retained_context')
        if isinstance(kept_ctx, dict) and isinstance(kept_ctx.get('user_messages'), list):
            if kept_ctx.get('user_messages_incomplete') is not False:
                self.g.census.add('compacted/retained-incomplete', 'skipped')
            else:
                entries = kept_ctx['user_messages']
                order = sorted(range(len(entries)), key=lambda i: (
                    entries[i].get('order') if isinstance(entries[i], dict) and
                    isinstance(entries[i].get('order'), int) else i, i))
                for rank, i in enumerate(order):
                    e = entries[i]
                    if not isinstance(e, dict):         # a helper thread's entries are lists
                        self.g.census.add('compacted/retained', 'skipped')
                        continue
                    text = e.get('text')
                    if not isinstance(text, str):
                        self.g.unknown('compacted/retained-shape', line=False)
                        continue
                    if 'origin' in e or not text.strip() or _tag(text):
                        self.g.census.add('compacted/retained', 'skipped')
                        continue
                    k, f = lines.keep(text, contract.KEEP_PERSON, both_ends=True)
                    self.ums_retained.append(_Msg(n=n, sub=-1000 + rank, text=k, full=f, at=at, client=True,
                                                  tag=None, hop=facts.note_hop(text), before=True, retained=True))
                    self.g.census.add('compacted/retained', 'taken')
        if self.limits.since_boundary and not self.boundary:
            self.phase = 'before'
        self.boundary = True

    # --- the whole read ---

    def run(self, it) -> Transcript:
        first = True
        try:
            for raw, offset in it:
                n = self.lines
                self.lines += 1
                if not raw:
                    first = False
                    if self._too_large(n, it.skipped_head):
                        if self.phase == 'before':
                            it.stopped = 'boundary'
                            break
                        continue
                    self.g.skipped_large += 1
                    head = it.skipped_head or b''
                    person = b'"UserMessage"' in head or (b'"response_item"' in head and _USER_ROLE_RE.search(head))
                    self.g.census.add('user/too-large' if person else 'too-large', 'counted')
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
                if self.line(n, d, offset, len(raw)) == 'stop':
                    it.stopped = 'boundary'
                    break
        finally:
            it.close()
        return self.finish(it)

    def finish(self, it) -> Transcript:
        g = self.g
        people = []
        notices = []
        if self.ums:
            any_client = any(m.client for m in self.ums)
            g.census.add('mode/client-id' if any_client else 'mode/no-client-id', 'taken')
            for m in self.ums:
                if m.hop:
                    notices.append((m, 'switchboard-note'))
                elif m.client or self._no_client_rule(m):
                    if not m.client and any_client:
                        g.census.add('mode/no-client-id-before-client-id', 'counted')
                    people.append(m)
                else:
                    notices.append((m, 'app-message'))
            if self.users:      # the same words again, as the model saw them: never taken beside the items
                g.census.add('response_item/message-user-beside-items', 'skipped', len(self.users))
        else:
            g.census.add('mode/response-user', 'taken')
            for m in self.users:
                if m.hop:
                    notices.append((m, 'switchboard-note'))
                elif m.tag is None:
                    people.append(m)            # [inferred: a file with no UserMessage item at all]
                else:
                    g.census.add('response_item/message-user-tagged', 'skipped')
        for m in self.ums_retained:
            if m.hop:
                notices.append((m, 'switchboard-note'))
            else:
                people.append(m)
        # A message kept by a compaction and also read back before it is one message.
        seen, kept_people = set(), []
        for m in sorted(people, key=lambda m: (m.retained, -m.n)):
            digest = hashlib.sha256(m.text.encode('utf-8', 'replace')).hexdigest()
            if m.retained and digest in seen:
                continue
            if m.before:
                seen.add(digest)
            kept_people.append(m)
        for m in kept_people:
            g.add(m.n, m.sub, Event(contract.PERSON, text=m.text, at=m.at, full_len=m.full,
                                    before_boundary=bool(m.before)))
        for m, origin in notices:
            text = words.NOTE_MARK_TEXT.format(hop=m.hop) if m.hop else lines.keep(m.text, contract.KEEP_TOOL_RESULT)[0]
            g.add(m.n, m.sub, Event(contract.NOTICE, text=text, at=m.at, full_len=len(text) if m.hop else m.full,
                                    origin=origin))
        # Tools: the typed items when the file has them (0.169), else the named calls and their outputs.
        names = {}
        for n, sub, e, call_id, is_output, ptype in self.responses:
            if not is_output and isinstance(call_id, str):
                names[call_id] = (e.name, e.files, e.ref)
        for n, sub, e, call_id, is_output, ptype in self.responses:
            if self.items_mode:
                g.census.add('response_item/%s-beside-items' % ptype, 'skipped')
                continue
            if is_output:
                name, files, ref = names.get(call_id, ('', (), None)) if isinstance(call_id, str) else ('', (), None)
                e = dataclasses.replace(e, name=name, files=files, ref=ref, text='' if ref is not None else e.text)
            g.add(n, sub, e)
            g.census.add('response_item/' + ptype, 'taken')
        g.census.add('mode/items' if self.items_mode else 'mode/responses', 'taken')
        for n, text, at in self.finals:
            if hashlib.sha256(text.strip().encode('utf-8', 'replace')).hexdigest() not in self.said:
                kept, full = lines.keep(text, contract.KEEP_PERSON, both_ends=True)
                g.add(n, 0, Event(contract.ASSISTANT, text=kept, at=at, full_len=full))
        side = facts.version_side(self.version, facts.CODEX_CHECKED)
        if side:
            g.census.add('version/' + side, 'counted')
        if self.marked is not None:
            g.items = [(n, sub, dataclasses.replace(e, before_boundary=True) if n > self.marked else e)
                       for n, sub, e in g.items]
        degraded = words.SHAPE_UNKNOWN.format(app=APP, version=self.version or 'unknown version') \
            if getattr(it, 'damaged', False) else None
        # A thread begun on Switchboard's note in which nothing was typed since is a shape this reader knows: the
        # note's own first message stands for the person's (rule I2).
        began_on_note = any(origin == 'switchboard-note' for _, origin in notices)
        if degraded is None and g.shape_unknown(facts.UNKNOWN_SHARE, facts.NEW_KIND_MIN,
                                                bool(kept_people) or began_on_note, self.boundary, self.lines > 0):
            degraded = words.SHAPE_UNKNOWN.format(app=APP, version=self.version or 'unknown version')
        info = ReadInfo(version=self.version, bytes_read=it.bytes_read, lines=self.lines,
                        from_end=it.stopped != 'start', stopped=it.stopped, unknown_lines=g.unknown_lines,
                        torn_tail=g.torn_tail, skipped_large=g.skipped_large, images=g.images,
                        reasoning=g.reasoning, title=self.chat.name, degraded=degraded,
                        compressed=self.chat.path.endswith('.zst'), census=g.census.rows(),
                        new_kinds=g.new_kinds(facts.NEW_KIND_MIN), reader=facts.CODEX_READER)
        return Transcript(chat=self.chat, events=g.events(), info=info)


def read(reach, chat: ChatRef, limits: ReadLimits = ReadLimits()) -> Transcript:
    """One chat as a Transcript, from its file's end within limits. A compressed file on a Python without
    compression.zstd gives no events and says so (ReadInfo.degraded is words.COMPRESSED)."""
    reader = _Reader(chat, limits)
    meta = _meta(chat.path) if (not chat.path.endswith('.zst') or lines.zstd_module()) else None
    reader.version = meta.get('cli_version') if meta and isinstance(meta.get('cli_version'), str) else chat.version
    if chat.path.endswith('.zst'):
        if lines.zstd_module() is None:
            return Transcript(chat=chat, events=(), info=ReadInfo(
                version=chat.version, compressed=True, stopped='start', title=chat.name, reader=facts.CODEX_READER,
                degraded=words.COMPRESSED.format(app=APP, python=platform.python_version())))
        it = lines.zst_back_lines(chat.path, limits, reach.mono)
    else:
        it = lines.back_lines(chat.path, limits, reach.mono)
    return reader.run(it)


# ---------- the saved numbers ----------


def _window(w, now) -> Optional[Window]:
    if not isinstance(w, dict):
        return None
    minutes = w.get('window_minutes')
    label = facts.window_label(minutes)
    if label is None:
        return None
    used = w.get('used_percent')
    used = float(used) if isinstance(used, (int, float)) and not isinstance(used, bool) else None
    resets = lines.epoch(w.get('resets_at'))
    if resets is not None and resets <= now:
        used = None             # it has refilled since it was read, and nothing has been read since
    return Window(label=label, used_pct=used, resets_at=resets, length_min=int(minutes))


def saved_limits(reach, now: float) -> Optional[Limits]:
    """Codex's own numbers as it last saved them (no process, no network): of the five most recently written plain
    session files of the last seven days, wherever they are filed (a helper's too: its numbers are the account's),
    each read from its end within 4 MiB and one second for its newest event_msg/token_count line whose rate_limits
    are for the 'codex' bucket (or name none), the reading with the newest time stamp. The file written last need not
    hold the newest reading (its last turn may have stopped before one). None when none."""
    oldest = now - facts.SAVED_LIMITS_DAYS * 86400
    found = sorted(((mtime, path) for mtime, path, _ in _rollouts(os.path.join(codex_home(reach), 'sessions'))
                    if path.endswith('.jsonl') and mtime >= oldest), reverse=True)
    best = None
    for _, path in found[:facts.SAVED_LIMITS_FILES]:
        got = _file_limits(reach, path, now)
        if got is not None and (best is None or (got.read_at or 0) > (best.read_at or 0)):
            best = got
    return best


def _file_limits(reach, path: str, now: float) -> Optional[Limits]:
    """One session file's newest reading for the 'codex' bucket, or None. Its limit word is dropped once every
    window has refilled."""
    it = lines.back_lines(path, ReadLimits(max_bytes=facts.SAVED_LIMITS_BYTES, max_seconds=facts.SAVED_LIMITS_SECONDS),
                          reach.mono)
    try:
        for raw, _ in it:
            if b'token_count' not in raw:
                continue
            d = lines.loads(raw)
            p = d.get('payload') if d and d.get('type') == 'event_msg' else None
            if not isinstance(p, dict) or p.get('type') != 'token_count':
                continue
            rl = p.get('rate_limits')
            if not isinstance(rl, dict) or rl.get('limit_id') not in (None, facts.CODEX_LIMIT_ID):
                continue
            read_at = lines.iso_time(d.get('timestamp'))
            wins = tuple(w for w in (_window(rl.get('primary'), now),
                                     _window(rl.get('secondary'), now)) if w is not None)
            reached = rl.get('rate_limit_reached_type')
            if place.refilled(wins, now):
                reached = None          # every window has refilled since this line said a limit was reached
            plan = rl.get('plan_type')
            return Limits(provider='codex', seat='codex', windows=wins, read_at=read_at, source='file',
                          plan=plan if isinstance(plan, str) else None,
                          reached=lines.kind_name(reached) if isinstance(reached, str) and reached else None)
    finally:
        it.close()
    return None


# ---------- the newest version, and the chat begun on a note ----------


def newest_seen(reach) -> Optional[str]:
    """The newest cli_version in the first line of Codex's newest session files (helpers' too), so an older program
    is never run on them. None when there are none."""
    newest = sorted(((mtime, path) for mtime, path, _ in _rollouts(os.path.join(codex_home(reach), 'sessions'))
                     if not path.endswith('.zst') or lines.zstd_module() is not None), reverse=True)
    best = None
    for _, path in newest[:facts.NEWEST_SEEN_FILES]:
        meta = _meta(path)
        v = meta.get('cli_version') if meta else None
        if isinstance(v, str) and run.version_key(v) and \
                (best is None or run.version_key(v) > run.version_key(best)):
            best = v
    return best


def started_from(reach, folder: str, hop_id: str, after: float) -> Optional[ChatRef]:
    """The newest Codex chat in folder begun at or after `after` whose first message is Switchboard's note of hop_id
    (words.NOTE_MARK_RE). Only the mark is looked at; nothing of the message is kept."""
    for ref in chats(reach, folder, reach.wall()):
        began = ref.started_at if ref.started_at is not None else ref.last_at
        if began is None or began < after:
            continue
        for raw in lines.head_lines(ref.path, max_bytes=facts.HEAD_MAX, max_lines=400):
            d = lines.loads(raw)
            p = d.get('payload') if d else None
            if not isinstance(p, dict):
                continue
            text = None
            if d.get('type') == 'event_msg' and p.get('type') == 'item_completed' and \
                    isinstance(p.get('item'), dict) and p['item'].get('type') == 'UserMessage':
                parts = p['item'].get('content')
                text = _parts_text(parts if isinstance(parts, list) else [], 'text')[0]
            elif d.get('type') == 'response_item' and p.get('type') == 'message' and p.get('role') == 'user':
                parts = p.get('content')
                text = _parts_text(parts if isinstance(parts, list) else [], 'input_text')[0]
                if _tag(text) or facts.codex_instructions(text):
                    text = None         # the app's own context block, or the project's instructions, come first
            if text:
                if facts.note_hop(text) == hop_id:
                    return ref
                break
    return None
