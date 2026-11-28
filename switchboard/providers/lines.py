"""Reading a chat's file from its end, within bytes, time and a line cap; and the small pieces both chat readers share.

A chat file is one JSON object a line, written by the app as the chat goes on, so the newest lines are at its end and
a long chat can be hundreds of megabytes. back_lines reads it backwards in 1 MiB chunks and gives whole lines, newest
first, until the reader has what it needs, the start is reached, or a cap is met. Nothing here parses JSON or knows
an app: claude_chats.py and codex_chats.py do that with chat_facts.py's tables.
"""
import calendar
import collections
import json
import re
import shlex
from typing import Iterator, Optional, Tuple

from . import contract
from .contract import ReadLimits

CHUNK = 1 << 20             # bytes read at a time
SKIPPED_HEAD = 1024         # bytes of a skipped line's start handed over, so a reader can tell what kind it was
                            # (Claude Code writes a line's "type" after its folder and ids: about 250 bytes in)
TIME_EVERY = 256            # lines between two looks at the clock while a chunk is handed out


class BackLines:
    """Whole lines of a file from its end, newest first, each as (bytes without its newline, byte offset of its
    start). A line longer than limits.line_max is handed out as b'' with its offset, never held whole in memory.
    Blank lines are passed over. It stops at the file's start, at limits.max_bytes, or at limits.max_seconds by
    mono(), and says why in .stopped ('start', 'bytes', 'time', 'missing'). .unterminated: the file's last byte is not
    a newline (its last line may be torn mid-write; the caller tells by whether it parses). .skipped_head: while the
    line just handed out is a skipped one, its first SKIPPED_HEAD bytes (else b''), so a reader can tell its kind."""

    def __init__(self, path, limits: ReadLimits, mono, chunk: int = CHUNK):
        self.path, self.limits, self.mono, self.chunk = path, limits, mono, max(1, int(chunk))
        self.stopped = 'start'
        self.bytes_read = 0
        self.unterminated = False
        self.skipped_large = 0
        self.skipped_head = b''
        self.most_held = 0              # the most bytes of one unfinished line held at once (bounded by line_max)
        self.damaged = False            # a compressed file could not be read to its end (nothing is handed out)
        self.closed = False
        self._gen = self._run()

    def __iter__(self):
        return self

    def __next__(self) -> Tuple[bytes, int]:
        return next(self._gen)

    def close(self):
        self._gen.close()
        self.closed = True

    def _over_time(self, start) -> bool:
        if self.mono() - start > self.limits.max_seconds:
            self.stopped = 'time'
            return True
        return False

    def _run(self):
        line_max, cap = self.limits.line_max, self.limits.max_bytes
        try:
            f = open(self.path, 'rb')
        except OSError:
            self.stopped = 'missing'
            return
        try:
            pos = f.seek(0, 2)
            start = self.mono()
            tail = b''                  # the end part of a line whose start is in an earlier chunk
            long_tail = False           # that line is already over line_max: its bytes are dropped as read
            front = b''                 # ... all but the first SKIPPED_HEAD bytes read of it so far
            first = True
            handed = 0
            while pos > 0:
                if self.bytes_read >= cap:
                    self.stopped = 'bytes'
                    return
                if self._over_time(start):
                    return
                n = min(self.chunk, pos, cap - self.bytes_read)
                pos -= n
                f.seek(pos)
                data = f.read(n)
                self.bytes_read += len(data)
                if first:
                    first = False
                    self.unterminated = not data.endswith(b'\n')
                    if not self.unterminated:
                        data = data[:-1]
                cut = data.rfind(b'\n')
                if long_tail:
                    if cut < 0:
                        front = (data + front)[:SKIPPED_HEAD]
                        continue
                    self.skipped_large += 1
                    self.skipped_head = (data[cut + 1:] + front)[:SKIPPED_HEAD]
                    yield b'', pos + cut + 1
                    long_tail, tail, front = False, b'', b''
                    data = data[:cut + 1]
                    cut = data.rfind(b'\n')
                buf = data + tail
                parts = buf.split(b'\n')
                tail = parts[0]
                at = pos + len(buf)
                for line in reversed(parts[1:]):
                    at -= len(line) + 1
                    if line:
                        self.skipped_head = b''
                        if len(line) > line_max:
                            self.skipped_large += 1
                            self.skipped_head, line = line[:SKIPPED_HEAD], b''
                        yield line, at + 1
                        handed += 1
                        if handed % TIME_EVERY == 0 and self._over_time(start):
                            return
                if len(tail) > line_max:
                    long_tail, tail, front = True, b'', tail[:SKIPPED_HEAD]
                self.most_held = max(self.most_held, len(tail))
            if long_tail:
                self.skipped_large += 1
                self.skipped_head = front
                yield b'', 0
            elif tail:
                self.skipped_head = b''
                yield tail, 0
        finally:
            f.close()


def back_lines(path, limits: ReadLimits, mono, chunk: int = CHUNK) -> BackLines:
    """See BackLines. The caller closes it (try/finally) when it stops early."""
    return BackLines(path, limits, mono, chunk)


def read_errors():
    """What reading a chat file may raise: the system's errors, a cut file's EOFError, and the compression module's
    own error for a damaged compressed file (compression.zstd.ZstdError is a plain Exception, 3.14.2)."""
    zstd = zstd_module()
    err = getattr(zstd, 'ZstdError', None) if zstd is not None else None
    return (OSError, EOFError, ValueError) + ((err,) if isinstance(err, type) else ())


def _open(path):
    """The file for reading from its start: a .zst one through compression.zstd (None without it), else plain."""
    if str(path).endswith('.zst'):
        zstd = zstd_module()
        return zstd.open(path, 'rb') if zstd is not None else None
    return open(path, 'rb')


def head_line(path, cap: int = 1 << 20) -> Optional[bytes]:
    """The file's first line without its newline, or None when it is longer than cap or the file cannot be read (a
    .zst file is read on a Python with compression.zstd). Only that line is read, never more than cap + 1 bytes."""
    try:
        f = _open(path)
        if f is None:
            return None
        with f:
            data = f.readline(cap + 1)
    except read_errors():
        return None
    cut = data.find(b'\n')
    if cut >= 0 and cut <= cap:
        return data[:cut]
    return data if len(data) <= cap else None


def head_lines(path, max_bytes: int, max_lines: int) -> Iterator[bytes]:
    """The file's first whole lines, oldest first, blank ones passed over, within max_bytes and max_lines."""
    errors = read_errors()
    try:
        f = _open(path)
    except errors:
        return
    if f is None:
        return
    with f:
        used = given = 0
        while given < max_lines:
            try:
                raw = f.readline(max_bytes - used + 1)     # never more than what is left of max_bytes, plus one
            except errors:
                return
            used += len(raw)
            if not raw or used > max_bytes:
                return
            line = raw.rstrip(b'\n')
            if line:
                given += 1
                yield line


# ---------- compressed files (Python 3.14 has compression.zstd; 3.9 has none) ----------


def zstd_module():
    """compression.zstd when this Python has it, else None."""
    try:
        from compression import zstd       # Python 3.14 and newer
        return zstd
    except ImportError:
        return None


class ZstBackLines(BackLines):
    """back_lines for a .jsonl.zst file: it can only be read from its start, so it is decompressed in 1 MiB pieces and
    the newest lines within limits.max_bytes are kept in a bounded deque, then handed out newest first. It is never
    stopped on time: a stop would hand out the file's oldest lines as its newest, and the bytes cap already bounds what
    is held. .stopped is 'bytes' when older lines were let go. A file that cannot be decompressed to its end hands out
    nothing and sets .damaged (its newest lines are the ones not read)."""

    def _run(self):
        zstd = zstd_module()
        if zstd is None:
            self.stopped = 'missing'
            return
        errors = read_errors()
        try:
            f = zstd.open(self.path, 'rb')
        except errors:
            self.stopped = 'missing'
            return
        line_max, cap = self.limits.line_max, self.limits.max_bytes
        kept = collections.deque()
        state = {'held': 0, 'dropped': False}

        def emit(line, at, head=None):
            if head is not None or len(line) > line_max:
                self.skipped_large += 1
                line, head = b'', line[:SKIPPED_HEAD] if head is None else head
            kept.append((line, at, head or b''))
            state['held'] += len(line) + 1
            while state['held'] > cap and kept:
                state['held'] -= len(kept.popleft()[0]) + 1
                state['dropped'] = True

        base = at = 0                   # base: offset of data[0]; at: offset where the unfinished line starts
        tail, long_tail, front = b'', False, b''
        try:
            while True:
                data = f.read(self.chunk)
                if not data:
                    break
                self.bytes_read += len(data)
                i = 0
                while True:
                    nl = data.find(b'\n', i)
                    if nl < 0:
                        if not long_tail:
                            tail += data[i:]
                            if len(tail) > line_max:
                                long_tail, tail, front = True, b'', tail[:SKIPPED_HEAD]
                        elif len(front) < SKIPPED_HEAD:
                            front = (front + data[i:])[:SKIPPED_HEAD]
                        self.most_held = max(self.most_held, len(tail))
                        break
                    if long_tail:
                        emit(b'', at, (front + data[i:nl])[:SKIPPED_HEAD])
                    elif tail or nl > i:
                        emit(tail + data[i:nl], at)
                    tail, long_tail, front = b'', False, b''
                    at, i = base + nl + 1, nl + 1
                base += len(data)
        except errors:
            self.damaged = True
            kept.clear()
            return
        finally:
            f.close()
        if long_tail or tail:
            self.unterminated = True
            emit(tail, at, front if long_tail else None)
        if state['dropped']:
            self.stopped = 'bytes'
        while kept:
            line, at, self.skipped_head = kept.pop()
            yield line, at


def zst_back_lines(path, limits: ReadLimits, mono, chunk: int = CHUNK) -> BackLines:
    return ZstBackLines(path, limits, mono, chunk)


# ---------- what both readers share ----------

_KIND_RE = re.compile(r'^[A-Za-z0-9_:./-]{1,40}\Z')


def kind_name(value) -> str:
    """A line or block kind as the file names it, for the census: kept when short and plain, else 'other' (a census
    row never carries words of the chat)."""
    return value if isinstance(value, str) and _KIND_RE.match(value) else 'other'


class Census:
    """Per line kind and block kind: seen, taken, skipped by the table, unknown (ReadInfo.census). Outcomes:
    'taken', 'skipped' (the table knows it and leaves it out), 'counted' (left out and counted elsewhere, such as a
    picture; shown as skipped), 'unknown'."""

    def __init__(self):
        self._rows = collections.OrderedDict()

    def add(self, kind: str, outcome: str, n: int = 1):
        row = self._rows.setdefault(kind, [0, 0, 0, 0])
        row[0] += n
        row[{'taken': 1, 'skipped': 2, 'counted': 2, 'unknown': 3}[outcome]] += n

    def unknown(self, kind: str) -> int:
        return self._rows.get(kind, (0, 0, 0, 0))[3]

    def rows(self) -> Tuple[Tuple[str, int, int, int, int], ...]:
        return tuple((k,) + tuple(v) for k, v in sorted(self._rows.items()))


class Gather:
    """What a reader keeps while it reads a chat from its end: events with their place in the file (n counts lines
    back from the end, sub orders events within one line), the census, and the counts the degrade rules look at.
    main / main_unknown: the lines the U rule looks at, and how many of them were of an unknown kind or shape (a new
    block kind inside one counts). text_misses: assistant text blocks that gave no event (rule I1). new: unknown
    kinds by name (rule I3)."""

    def __init__(self):
        self.census = Census()
        self.items = []
        self.main = self.main_unknown = self.text_misses = 0
        self.unknown_lines = self.torn_tail = self.skipped_large = self.images = self.reasoning = 0
        self.new = collections.Counter()

    def add(self, n: int, sub: int, event):
        self.items.append((n, sub, event))

    def unknown(self, kind: str, line: bool = True):
        """A kind or shape the table does not know: counted in the census and toward I3; line=True also counts the
        line in ReadInfo.unknown_lines (a new block inside a known line counts too)."""
        self.census.add(kind, 'unknown')
        self.new[kind] += 1
        if line:
            self.unknown_lines += 1

    def events(self):
        return tuple(e for _, _, e in sorted(self.items, key=lambda t: (-t[0], t[1])))

    def new_kinds(self, least: int) -> Tuple[str, ...]:
        return tuple(sorted(k for k, v in self.new.items() if v >= least))

    def shape_unknown(self, share: float, least: int, person: bool, boundary: bool, has_lines: bool) -> bool:
        """The degrade rules: U (more than share of the main lines unknown), I1 (an assistant text block gave
        nothing), I2 (lines, but no person message and no summary boundary), I3 (a new kind seen least times or
        more)."""
        u = self.main > 0 and self.main_unknown > share * self.main
        i2 = not person and not boundary and has_lines
        return u or self.text_misses > 0 or i2 or bool(self.new_kinds(least))


_ISO_RE = re.compile(r'^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)(?:\.(\d+))?(Z|[+-]\d\d:?\d\d)?\Z')


def iso_time(text) -> Optional[float]:
    """'2026-11-23T18:04:14.123Z' (both apps' timestamps) as epoch seconds; None when it is not one."""
    m = _ISO_RE.match(text) if isinstance(text, str) else None
    if not m:
        return None
    try:
        t = float(calendar.timegm(tuple(int(m.group(i)) for i in range(1, 7)) + (0, 0, 0)))
    except (ValueError, OverflowError):
        return None
    if m.group(7):
        t += int(m.group(7)[:6].ljust(6, '0')) / 1e6
    zone = m.group(8)
    if zone and zone != 'Z':
        sign = -1 if zone[0] == '-' else 1
        digits = zone[1:].replace(':', '')
        t -= sign * (int(digits[:2]) * 3600 + int(digits[2:]) * 60)
    return t


def epoch(value) -> Optional[float]:
    """A time a file gives as epoch seconds, epoch milliseconds or an ISO string, as epoch seconds; else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) / 1000.0 if value > 1e11 else float(value)
    return iso_time(value)


def keep(text: str, cap: int, both_ends: bool = False) -> Tuple[str, int]:
    """(text cut to at most cap characters, its full length). both_ends keeps the head and the tail and says how much
    was left out between them (the person's own words: a long message's ask is often at its end)."""
    n = len(text)
    if n <= cap:
        return text, n
    if not both_ends:
        return text[:cap], n
    mark = '\n[... %d characters left out ...]\n' % (n - cap)
    room = max(cap - len(mark), 2)
    head = room // 2
    return text[:head] + mark + text[n - (room - head):], n


def loads(raw: bytes):
    """One line as a JSON object, or None."""
    try:
        d = json.loads(raw.decode('utf-8', 'replace'))
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def compact(value) -> str:
    """A tool's input as compact JSON (its words kept as written)."""
    try:
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    except (TypeError, ValueError):
        return ''


# The note's code (redact.py) says which file names usually hold a secret. Imported on first use, so a build
# without it still reads chats (a call's files are then left empty); tests replace it with a stand-in.
redact = None


def _redact():
    global redact
    if redact is None:
        try:
            from . import redact as mod
            redact = mod
        except ImportError:
            redact = False
    return redact or None


def shell_words(text: str) -> list:
    """The words of a shell line, which is never run. Up to contract.SPLIT_MAX characters the shell's own way (shlex:
    what quotes hold is one word), and a line shlex cannot read at its blanks, as it stands. A longer line at its
    blanks too, with the quote marks at each word's ends taken off, so a quoted file name is still seen: shlex takes
    seconds on a megabyte, and a read has seconds for the whole chat."""
    if len(text) > contract.SPLIT_MAX:
        return [w for w in (x.strip('\'"') for x in text.split()) if w]
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def secret_words(words) -> Tuple[str, ...]:
    """The words of a shell command (already split, never run) that name a file which usually holds a secret, so
    the note leaves that command's result out too."""
    mod = _redact()
    if mod is None:
        return ()
    out, seen = [], set()
    for w in words:
        if isinstance(w, str) and w and w not in seen:
            seen.add(w)                     # a file's text in one command repeats its words: each is asked once
            if mod.secret_file(w):
                out.append(w)
    return tuple(out)
