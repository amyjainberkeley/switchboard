"""What an install's record holds, and the careful JSON edits it makes.

Facts, never copies: for a file, whether it was there, its SHA-256, size and mode; for a JSON member (a key, or a key
inside a top-level object), whether it was there and its value. The value of a member Switchboard or an installer
adds is recorded (a plugin entry, an MCP server entry, an allow string), never the rest of a file, so a secret in
~/.claude/settings.json can't end up in Switchboard's folder.

A member goes in and comes out through common.JsonDoc, which changes only that member's bytes. When an outside
installer rewrites a whole file to add one member, splice() puts the member it wrote into the file's earlier bytes
(held in memory only), so the person's formatting stays and taking the member out again gives those bytes back.

Claude Code writes ~/.claude.json while it runs, under its own lock: a folder named ~/.claude.json.lock (npm's
proper-lockfile; stale after 10 s). claude_json_lock() takes the same lock, so the two never write at once.
"""
import json
import os
import random
import stat
import time
from contextlib import contextmanager

from switchboard.packs import common

LOCK_STALE = 10.0
LOCK_WAIT = 9.0


# ---------------------------------------------------------------- facts

def file_fact(path):
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return {'exists': False}
    if stat.S_ISLNK(st.st_mode):
        return {'exists': True, 'kind': 'link', 'target': os.readlink(path)}
    if stat.S_ISDIR(st.st_mode):
        return {'exists': True, 'kind': 'dir'}
    return {'exists': True, 'kind': 'file', 'sha': common.sha(common.read_bytes(path)), 'size': st.st_size,
            'mode': st.st_mode & 0o7777}


def load_obj(raw):
    if raw is None:
        return None
    try:
        obj = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def member(obj, keys):
    """(present, value) of obj[keys[0]] or obj[keys[0]][keys[1]]."""
    cur = obj
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return False, None
        cur = cur[k]
    return True, cur


def blank_text(raw):
    """A file that holds only whitespace or nothing, as its text, to give back exactly once what Switchboard (or an
    installer) added to it is gone; None for any other file. Whitespace is never a secret, so it may be kept."""
    if raw is None or raw.strip():
        return None
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return None


def member_fact(path, keys):
    raw = common.read_bytes(path)
    obj = load_obj(raw)
    present, value = member(obj or {}, keys)
    return {'file': raw is not None, 'parses': obj is not None or raw is None, 'present': present,
            'value': value if present else None, 'sha': common.sha(raw)}


# ---------------------------------------------------------------- JSON text in the file's own style

def _indent_unit(text):
    for line in text.split('\n')[1:]:
        stripped = line.lstrip(' \t')
        if stripped and line != stripped:
            lead = line[:len(line) - len(stripped)]
            return '\t' if lead.startswith('\t') else ' ' * len(lead)
    return '  '


def value_text(value, line_indent, unit):
    """JSON for value as it would sit on a line indented line_indent: nested lines one unit further in."""
    if not isinstance(value, (dict, list)) or not value:
        return json.dumps(value, ensure_ascii=False)
    width = len(unit) if unit != '\t' else 1
    text = json.dumps(value, indent=width, ensure_ascii=False)
    if unit == '\t':
        text = '\n'.join(_retab(x) for x in text.split('\n'))
    return text.replace('\n', '\n' + line_indent)


def _retab(line):
    n = len(line) - len(line.lstrip(' '))
    return '\t' * n + line[n:]


def _line_indent_of(doc, keys_members):
    """The indent of the line a new member of the edited object goes on."""
    text = doc.text
    if doc.members:
        k0 = doc.members[-1]['k0']
        start = text.rfind('\n', 0, k0) + 1
        return text[start:k0] if text[start:k0].strip() == '' else ''
    start = text.rfind('\n', 0, doc.o) + 1
    j = start
    while j < len(text) and text[j] in ' \t':
        j += 1
    return text[start:j] + _indent_unit(text)


# ---------------------------------------------------------------- one member in, one member out

def with_member(raw, keys, value, path='settings'):
    """raw (bytes or None) with obj[keys...] = value, changing only that member's bytes. Two levels at most:
    a missing parent object is added with the one member in it."""
    base = raw if raw is not None and raw.strip() else None
    text_raw = base if base is not None else b'{}\n'
    doc = common.JsonDoc(text_raw, path)
    unit = _indent_unit(doc.text)
    expect = json.loads(json.dumps(doc.obj))
    if len(keys) == 1 or (keys[0] not in doc.obj):
        k, v = (keys[0], value) if len(keys) == 1 else (keys[0], {keys[1]: value})
        ind = _line_indent_of(doc, None)
        new = doc.set_raw(k, value_text(v, ind, unit))
        expect[keys[0]] = v if len(keys) == 1 else {keys[1]: value}
    else:
        if not isinstance(doc.obj[keys[0]], dict):
            raise common.Refused('%s has "%s" but it is not an object, so Switchboard will not edit it.'
                                 % (path, keys[0]))
        inner = common.JsonDoc(text_raw, path, inside=keys[0])
        ind = _line_indent_of(inner, None)
        new = inner.set_raw(keys[1], value_text(value, ind, unit))
        expect[keys[0]][keys[1]] = value
        doc = inner
    new = doc.checked(new, expect)
    return new.encode('utf-8')


def empty_inner(raw, keys, path='settings'):
    """The whitespace inside the object a member at keys would be added to, when that object has no members yet
    (so taking the member out again can put it back); None otherwise."""
    if raw is None or not raw.strip():
        return None
    obj = load_obj(raw)
    if obj is None:
        return None
    if len(keys) == 1 or keys[0] not in obj:               # the member, or its new parent, goes in at the top
        doc = common.JsonDoc(raw, path)
    elif isinstance(obj.get(keys[0]), dict):
        doc = common.JsonDoc(raw, path, inside=keys[0])
    else:
        return None
    return doc.inner() if not doc.members else None


def without_member(raw, keys, drop_empty_parent=False, path='settings', inner=None):
    """raw without obj[keys...] (unchanged when it is not there). With drop_empty_parent, a parent object left empty
    goes too (it was added with the member). inner: what empty_inner() said before the member was added."""
    obj = load_obj(raw)
    if obj is None:
        raise common.Refused('%s is not valid JSON, so Switchboard will not edit it.' % path)
    present, _ = member(obj, keys)
    if not present:
        return raw
    expect = json.loads(json.dumps(obj))
    if len(keys) == 1:
        doc = common.JsonDoc(raw, path)
        expect.pop(keys[0])
        new = doc.remove(keys[0], inner)
    else:
        parent = expect[keys[0]]
        parent.pop(keys[1])
        if drop_empty_parent and not parent:
            expect.pop(keys[0])
            doc = common.JsonDoc(raw, path)
            new = doc.remove(keys[0], inner)
        else:
            doc = common.JsonDoc(raw, path, inside=keys[0])
            new = doc.remove(keys[1], inner)
    return doc.checked(new, expect).encode('utf-8')


# ---------------------------------------------------------------- one string in a JSON array, in place
#
# For permissions.allow: the array's text is found through JsonDoc, its elements by JSON's own decoder; a string is
# added after the last element with the same separator the array already uses, or taken out with the comma before it.

def _array_at(text, v0):
    """[(start, end)] of each element of the array whose '[' is text[v0], and the index of its ']'."""
    i = common._skip(text, v0 + 1)
    spans = []
    if text[i] == ']':
        return spans, i
    while True:
        _, e = common._DEC.raw_decode(text, i)
        spans.append((i, e))
        j = common._skip(text, e)
        if text[j] == ',':
            i = common._skip(text, j + 1)
            continue
        if text[j] == ']':
            return spans, j
        raise ValueError('expected "," or "]" at %d' % j)


def array_add(raw, keys, item, path='settings'):
    """raw with item appended to the array at keys (two levels), made when missing. Unchanged when it is there."""
    obj = load_obj(raw) if raw is not None and raw.strip() else {}
    if obj is None:
        raise common.Refused('%s is not valid JSON, so Switchboard will not edit it.' % path)
    present, arr = member(obj, keys)
    if not present:
        return with_member(raw, keys, [item], path)
    if not isinstance(arr, list):
        raise common.Refused('%s has "%s" but it is not a list, so Switchboard will not edit it.' % (path, keys[-1]))
    if item in arr:
        return raw
    doc = common.JsonDoc(raw, path, inside=keys[0]) if len(keys) == 2 else common.JsonDoc(raw, path)
    m = doc.member(keys[-1])
    text = doc.text
    spans, close = _array_at(text, m['v0'])
    piece = json.dumps(item, ensure_ascii=False)
    if not spans:
        new = text[:m['v0'] + 1] + piece + text[close:]
    else:
        last_s, last_e = spans[-1]
        if len(spans) > 1:
            sep = text[spans[-2][1]:last_s]                  # ", " or ",\n      ", as the array already has
        else:
            ws = text[m['v0'] + 1:last_s]
            sep = ',' + ws if '\n' in ws else ', '
        new = text[:last_e] + sep + piece + text[last_e:]
    expect = json.loads(json.dumps(obj))
    tgt = expect[keys[0]] if len(keys) == 2 else expect
    tgt[keys[-1]] = list(arr) + [item]
    return doc.checked(new, expect).encode('utf-8')


def array_inner(raw, keys, path='settings'):
    """The whitespace inside the array at keys when it is empty (to put back when the one added item goes)."""
    obj = load_obj(raw)
    present, arr = member(obj or {}, keys)
    if not present or arr != []:
        return None
    doc = common.JsonDoc(raw, path, inside=keys[0]) if len(keys) == 2 else common.JsonDoc(raw, path)
    m = doc.member(keys[-1])
    return doc.text[m['v0'] + 1:m['v1'] - 1]


def array_remove(raw, keys, item, path='settings', inner=None):
    """raw without the one element equal to item in the array at keys; unchanged when it is not there. inner: what
    array_inner() said before the item was added to an empty array."""
    obj = load_obj(raw)
    if obj is None:
        raise common.Refused('%s is not valid JSON, so Switchboard will not edit it.' % path)
    present, arr = member(obj, keys)
    if not present or not isinstance(arr, list) or item not in arr:
        return raw
    if arr.count(item) > 1:
        raise common.Refused('%s lists "%s" more than once; Switchboard will not guess which one is its own.'
                             % (path, item))
    doc = common.JsonDoc(raw, path, inside=keys[0]) if len(keys) == 2 else common.JsonDoc(raw, path)
    m = doc.member(keys[-1])
    text = doc.text
    spans, close = _array_at(text, m['v0'])
    k = arr.index(item)
    s, e = spans[k]
    if len(spans) == 1:
        new = text[:m['v0'] + 1] + (inner or '') + text[close:]
    elif k == 0:
        new = text[:s] + text[spans[1][0]:]
    else:
        new = text[:spans[k - 1][1]] + text[e:]
    expect = json.loads(json.dumps(obj))
    tgt = expect[keys[0]] if len(keys) == 2 else expect
    tgt[keys[-1]].remove(item)
    return doc.checked(new, expect).encode('utf-8')


def splice(before_raw, now_raw, keys, path='settings'):
    """When now_raw is before_raw plus the member at keys (and nothing else that parses differently), the same
    content in before_raw's own bytes; else None. Used after an installer rewrote a whole file."""
    now_obj = load_obj(now_raw)
    if now_obj is None:
        return None
    present, value = member(now_obj, keys)
    if not present:
        return None
    try:
        made = with_member(before_raw, keys, value, path)
    except common.Refused:
        return None
    return made if load_obj(made) == now_obj else None


# ---------------------------------------------------------------- Claude Code's lock on ~/.claude.json

@contextmanager
def claude_json_lock(json_path, wait=LOCK_WAIT, stale=LOCK_STALE):
    """Hold <file>.lock (a folder, as Claude Code's own writer makes it) while editing json_path."""
    lock = json_path + '.lock'
    t0 = time.monotonic()
    while True:
        try:
            os.mkdir(lock)
            break
        except FileExistsError:
            try:
                age = time.time() - os.lstat(lock).st_mtime
            except FileNotFoundError:
                continue
            if age > stale:
                try:
                    os.rmdir(lock)           # its holder stopped touching it: gone (the lock's own rule)
                except OSError:
                    pass
                continue
            if time.monotonic() - t0 > wait:
                raise common.Refused('Claude Code is writing %s right now. Try again in a minute.' % json_path)
            time.sleep(0.05 + random.random() * 0.1)
    try:
        yield
    finally:
        try:
            os.rmdir(lock)
        except OSError:
            pass


def write_if_same(path, expect_raw, new_raw, mode_default=0o600):
    """Write new_raw over path only while it still holds expect_raw (or is missing when expect_raw is None).
    new_raw None deletes it. A link stays a link: the file it points at is written (common.atomic_write). Returns
    True when written."""
    if common.read_bytes(path) != expect_raw:
        return False
    if new_raw is None:
        if os.path.lexists(path):
            os.unlink(path)
        return True
    d = os.path.dirname(path)
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    common.atomic_write(path, new_raw, common.file_mode(path, mode_default))
    return True
