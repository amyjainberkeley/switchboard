"""What the three packs share: where things live, careful file writes, Switchboard's record of what
it changed, the marker-block edit for an instructions file, and an in-place JSON member edit.

Copied in spirit from switchboard/models.py (plan, apply, undo exactly), not imported from it, so a
change there cannot break the packs. Standard library only; Python 3.9 and later.
"""
import hashlib
import json
import os
import tempfile
import time

STATE_VERSION = 1
STATE_NAME = 'state.json'
INSTRUCTIONS_NAME = 'CLAUDE.md'
BOM = b'\xef\xbb\xbf'


class Refused(Exception):
    """Switchboard will not do this. str(e) is the plain reason."""


# ---------------------------------------------------------------- paths, resolved at call time

def home():
    return os.path.expanduser('~')


def data_dir():
    return os.environ.get('SWITCHBOARD_HOME') or os.path.join(home(), '.switchboard')


def packs_dir():
    return os.path.join(data_dir(), 'packs')


def default_state_path():
    return os.path.join(packs_dir(), STATE_NAME)


def claude_dir(given=None):
    """Claude Code's folder: the one given, else CLAUDE_CONFIG_DIR, else ~/.claude (real path)."""
    d = given or os.environ.get('CLAUDE_CONFIG_DIR') or os.path.join(home(), '.claude')
    return os.path.realpath(os.path.abspath(os.path.expanduser(d)))


def make_private_dir(path):
    """Make a folder (and parents) readable only by the owner; an existing one is tightened to 0700."""
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        if os.stat(path).st_mode & 0o077:
            os.chmod(path, 0o700)
    except OSError:
        pass
    return path


# ---------------------------------------------------------------- small file helpers

def read_bytes(path):
    """The file's bytes, or None when there is no file."""
    try:
        with open(path, 'rb') as f:
            return f.read()
    except FileNotFoundError:
        return None


def sha(data):
    return None if data is None else hashlib.sha256(data).hexdigest()


def file_mode(path, default):
    try:
        return os.stat(path).st_mode & 0o7777
    except FileNotFoundError:
        return default


def atomic_write(path, data, mode):
    """Write bytes through a temp file in the same folder, then rename over the target. A link stays a link: the
    file it points at is written (a settings file kept in a dotfiles folder stays there, and is the one changed). A
    link to nothing is refused."""
    if os.path.islink(path):
        target = os.path.realpath(path)
        if not os.path.isfile(target):
            raise Refused("%s is a link to %s, which is not a file, so Switchboard leaves it alone." % (path, target))
        path = target
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix='.%s.' % os.path.basename(path), suffix='.switchboard-tmp')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def est_tokens(text):
    """A token estimate from characters: characters / 3, rounded up. English prose usually runs 3.5 to 4.5
    characters a token, so this over-counts on purpose; a block it calls under a budget is under it."""
    return -(-len(text) // 3)


# ---------------------------------------------------------------- Switchboard's own record

def blank_state():
    return {'version': STATE_VERSION, 'files': {}, 'blocks': {}, 'claude_mem': None}


def load_state(path=None):
    """(state, raw bytes). A record that cannot be read raises Refused: nothing is changed then."""
    path = path or default_state_path()
    raw = read_bytes(path)
    if raw is None:
        return blank_state(), None
    try:
        st = json.loads(raw.decode('utf-8'))
        if not isinstance(st, dict) or st.get('version') != STATE_VERSION or not isinstance(st.get('files'), dict) \
                or not isinstance(st.get('blocks'), dict):
            raise ValueError('unexpected shape')
    except ValueError as e:
        raise Refused("Switchboard's record of its pack changes (%s) can't be read (%s). It will not change files "
                      "it can't account for." % (path, e))
    st.setdefault('claude_mem', None)
    return st, raw


def save_state(path, st):
    make_private_dir(os.path.dirname(os.path.abspath(path)))
    atomic_write(path, (json.dumps(st, indent=1, sort_keys=True) + '\n').encode('utf-8'), 0o600)


# ---------------------------------------------------------------- a marker block in an instructions file
#
# The block is whole lines: a start marker line, the body, an end marker line, each ending in the
# file's own line ending. It goes at the very top of the file (after a byte-order mark, if the file
# has one), so adding it puts bytes only between the start of the start marker and the end of the
# end marker line; nothing of the person's moves inside their own lines. Taking it out removes exactly
# those bytes wherever the block now sits, so the file is what it was plus anything the person wrote
# around it since.

def block_bytes(start, end, body, nl=b'\n'):
    lines = [start] + list(body) + [end]
    return b''.join(x.encode('utf-8') + nl for x in lines)


def _line_spans(data):
    """(start, end with its line ending, text without it) for every line of data."""
    i, n = 0, len(data)
    while i < n:
        j = data.find(b'\n', i)
        e = n if j == -1 else j + 1
        text = data[i:j] if j != -1 else data[i:]
        if text.endswith(b'\r'):
            text = text[:-1]
        yield i, e, text
        i = e


def locate(data, start, end):
    """Where the block is: ('absent', None), ('one', (s, e)) or ('broken', why). s and e are byte offsets:
    the start marker line's first byte and the byte after the end marker line."""
    if not data:
        return 'absent', None
    sm, em = start.encode('utf-8'), end.encode('utf-8')
    starts, ends = [], []
    for s, e, text in _line_spans(data):
        if s == 0 and text.startswith(BOM):
            text, s = text[len(BOM):], len(BOM)
        if text == sm:
            starts.append(s)
        elif text == em:
            ends.append(e)
    loose = data.count(sm) - len(starts) + data.count(em) - len(ends)
    if not starts and not ends and not loose:
        return 'absent', None
    if len(starts) == 1 and len(ends) == 1 and starts[0] < ends[0] and not loose:
        return 'one', (starts[0], ends[0])
    return 'broken', ('%d start and %d end marker lines%s' % (len(starts), len(ends),
                      ', and a marker that is not on a line of its own' if loose else ''))


def newline_of(data):
    return b'\r\n' if data and b'\r\n' in data else b'\n'


def insert_block(data, block):
    """data with block at the top (after a byte-order mark)."""
    data = data or b''
    k = len(BOM) if data.startswith(BOM) else 0
    return data[:k] + block + data[k:]


def without_block(data, span):
    s, e = span
    return data[:s] + data[e:]


# ---------------------------------------------------------------- a JSON member, edited in place
#
# Used for claude-mem's own settings file. Only the bytes of the one member that is added, changed or
# taken out change; key order, spacing and the rest stay as written. Every result is parsed again and
# compared with the expected object, so a wrong splice is never written.

_WS = ' \t\n\r'


def _no_constant(name):
    raise ValueError('%s is not JSON' % name)


_DEC = json.JSONDecoder(parse_constant=_no_constant)


def _skip(text, i):
    while i < len(text) and text[i] in _WS:
        i += 1
    return i


def _object_at(text, i):
    """Members of the JSON object whose '{' is text[i] (key, k0, k1, v0, v1, value) and the index past its '}'."""
    if i >= len(text) or text[i] != '{':
        raise ValueError('not an object')
    j = _skip(text, i + 1)
    members = []
    if j < len(text) and text[j] == '}':
        return members, j + 1
    while True:
        if j >= len(text) or text[j] != '"':
            raise ValueError('expected a key at %d' % j)
        key, k1 = json.decoder.scanstring(text, j + 1, True)
        c = _skip(text, k1)
        if c >= len(text) or text[c] != ':':
            raise ValueError('expected ":" at %d' % c)
        v0 = _skip(text, c + 1)
        value, v1 = _DEC.raw_decode(text, v0)
        members.append({'key': key, 'k0': j, 'k1': k1, 'v0': v0, 'v1': v1, 'value': value})
        e = _skip(text, v1)
        if e < len(text) and text[e] == ',':
            j = _skip(text, e + 1)
            continue
        if e < len(text) and text[e] == '}':
            return members, e + 1
        raise ValueError('expected "," or "}" at %d' % e)


def _ws_before(text, pos):
    p = pos
    while p > 0 and text[p - 1] in _WS:
        p -= 1
    return text[p:pos]


def _line_indent(text, pos):
    start = text.rfind('\n', 0, pos) + 1
    j = start
    while j < len(text) and text[j] in ' \t':
        j += 1
    return text[start:j]


class JsonDoc(object):
    """A JSON settings file's text and the members of one object in it: the top level, or the object under
    the top-level key `inside` (for example "env")."""

    def __init__(self, raw, path, inside=None):
        self.path, self.inside = path, inside
        if raw is None:
            text = '{}\n'
        elif raw.startswith(BOM):
            raise Refused('%s starts with a byte-order mark; Switchboard edits plain UTF-8 JSON only.' % path)
        else:
            try:
                text = raw.decode('utf-8')
            except UnicodeDecodeError:
                raise Refused('%s is not UTF-8 text.' % path)
            if not text.strip():
                text = '{}' + (text or '\n')
        self.text = text
        self.parse()

    def parse(self):
        text = self.text
        try:
            whole = _DEC.decode(text)
            if not isinstance(whole, dict):
                raise ValueError('the top level is not an object')
            o = _skip(text, 0)
            top, end = _object_at(text, o)
            if _skip(text, end) != len(text):
                raise ValueError('text after the object')
        except ValueError as e:
            raise Refused('%s is not valid JSON (%s), so Switchboard will not edit it.' % (self.path, e))
        self.obj = whole
        self.o, self.end, self.members = o, end, top
        if self.inside is not None:
            m = self.member(self.inside, top)
            if m is None or not isinstance(m['value'], dict):
                raise Refused('%s has no "%s" object to edit.' % (self.path, self.inside))
            self.o = m['v0']
            self.members, self.end = _object_at(text, self.o)

    def member(self, key, members=None):
        found = [m for m in (self.members if members is None else members) if m['key'] == key]
        if len(found) > 1:
            raise Refused('%s has the key "%s" %d times; Switchboard will not guess which one is read.'
                          % (self.path, key, len(found)))
        return found[0] if found else None

    def target(self):
        return self.obj if self.inside is None else self.obj[self.inside]

    def set_raw(self, key, value_text):
        """New text with `key` set to the JSON text value_text (added last when missing)."""
        text = self.text
        have = self.member(key)
        if have is not None:
            return text[:have['v0']] + value_text + text[have['v1']:]
        nl = '\r\n' if '\r\n' in text else '\n'
        piece_key = json.dumps(key, ensure_ascii=False)
        if self.members:
            last = self.members[-1]
            sep = _ws_before(text, last['k0'])
            colon = text[last['k1']:last['v0']]
            if '\n' in sep:
                ind = sep[sep.rfind('\n') + 1:]
                piece = ',' + nl + ind + piece_key + colon + value_text
            else:
                piece = ',' + sep + piece_key + colon + value_text
            return text[:last['v1']] + piece + text[last['v1']:]
        base = _line_indent(text, self.o)
        inner = nl + base + '  ' + piece_key + ': ' + value_text + nl + base
        return text[:self.o + 1] + inner + text[self.end - 1:]

    def inner(self):
        """The text between the braces of the edited object (whitespace when it is empty)."""
        return self.text[self.o + 1:self.end - 1]

    def remove(self, key, empty_inner=None):
        """New text without `key`. When it is the last member, empty_inner (the object's text from before it was
        added) goes back between the braces."""
        text = self.text
        have = self.member(key)
        if have is None:
            return text
        i = self.members.index(have)
        if len(self.members) == 1:
            return text[:self.o + 1] + (empty_inner or '') + text[self.end - 1:]
        if i == 0:
            return text[:have['k0']] + text[self.members[1]['k0']:]
        return text[:self.members[i - 1]['v1']] + text[have['v1']:]

    def checked(self, new_text, expect):
        """new_text, after making sure the whole file parses to exactly `expect`."""
        try:
            got = _DEC.decode(new_text)
        except ValueError:
            got = object()
        if got != expect:
            raise Refused('Switchboard could not make this edit to %s without changing something else; nothing '
                          'was written.' % self.path)
        return new_text


def now():
    return time.time()


# ---------------------------------------------------------------- plan, apply, undo for a marker block
#
# spec: {'id': 'house-rules', 'name': 'House rules', 'start': marker line, 'end': marker line,
#        'body': [lines]}. The block goes in the user instructions file, <Claude Code folder>/CLAUDE.md.

def instructions_path(cd):
    return os.path.join(cd, INSTRUCTIONS_NAME)


def block_key(block_id, path):
    """Records are per block and per file, so a block added under another Claude Code folder is its own."""
    return '%s|%s' % (block_id, path)


def _line_no(data, pos):
    return data.count(b'\n', 0, pos) + 1


def plan_block(spec, on, claude_dir_=None, state_path=None):
    """What turning the block on or off would change, without changing anything.

    Returns {'pack', 'on', 'file', 'claude_dir', 'state_path', 'blocked', 'op', 'says', 'notes',
    'fingerprint', 'state_sha', 'block'}. op is one of None (nothing to do), 'add', 'replace', 'adopt',
    'remove', 'delete-file', 'forget'.
    """
    cd = claude_dir(claude_dir_)
    sp = state_path or default_state_path()
    path = instructions_path(cd)
    out = {'pack': spec['id'], 'on': bool(on), 'file': path, 'claude_dir': cd, 'state_path': sp, 'blocked': None,
           'op': None, 'says': None, 'notes': [], 'fingerprint': None, 'state_sha': None, 'block': None,
           'markers': (spec['start'], spec['end'])}
    if not os.path.isdir(cd):
        if on:
            out['blocked'] = 'There is no Claude Code folder at %s.' % cd
            return out
        try:                                # off: nothing can be in a folder that is not there; a record of a block
            st, raw_state = load_state(sp)  # in it is forgotten
        except Refused as e:
            out['blocked'] = str(e)
            return out
        out['state_sha'] = sha(raw_state)
        out['block'] = block_bytes(spec['start'], spec['end'], spec['body']).decode('utf-8')
        if block_key(spec['id'], path) in st['blocks']:
            out['op'], out['says'] = 'forget', '%s is already gone from %s.' % (spec['name'], path)
        else:
            out['says'] = '%s is off.' % spec['name']
        return out
    if os.path.islink(path):
        out['blocked'] = ('%s is a link to %s. Switchboard does not edit linked files; add the lines to the target '
                          'yourself.' % (path, os.path.realpath(path)))
        return out
    if os.path.isdir(path):
        out['blocked'] = '%s is a folder, not a file.' % path
        return out
    try:
        st, raw_state = load_state(sp)
    except Refused as e:
        out['blocked'] = str(e)
        return out
    out['state_sha'] = sha(raw_state)
    raw = read_bytes(path)
    out['fingerprint'] = sha(raw)
    rec = st['blocks'].get(block_key(spec['id'], path))
    where, span = locate(raw, spec['start'], spec['end'])
    want = block_bytes(spec['start'], spec['end'], spec['body'], newline_of(raw))
    out['block'] = want.decode('utf-8')
    out['markers'] = (spec['start'], spec['end'])
    writable = raw is None or os.access(path, os.W_OK)
    name = spec['name']

    if where == 'broken':
        out['blocked'] = ("%s has Switchboard's %s markers, but not as one block (%s). Switchboard will not guess "
                          'which lines are its own; fix the markers by hand.' % (path, name, span))
        return out
    cur = raw[span[0]:span[1]] if where == 'one' else None
    lines = (_line_no(raw, span[0]), _line_no(raw, span[1] - 1)) if where == 'one' else None
    ours = rec is not None and cur is not None and sha(cur) == rec.get('sha')

    if on:
        if where == 'one':
            if ours and cur == want:
                out['says'] = '%s is already on (lines %d-%d of %s).' % (name, lines[0], lines[1], path)
                return out
            if ours:
                out['op'], out['says'] = 'replace', ('Updates the %s block (lines %d-%d of %s) to the current text. '
                                                     'Nothing outside its markers changes.'
                                                     % (name, lines[0], lines[1], path))
            elif rec is not None:
                out['says'] = ('You edited the %s block (lines %d-%d of %s) since Switchboard wrote it. Your version '
                               'stays as it is.' % (name, lines[0], lines[1], path))
                return out
            elif cur == want:
                out['op'], out['says'] = 'adopt', ('The %s block in %s is already Switchboard\'s exact text; '
                                                   'Switchboard records it as its own and writes nothing.'
                                                   % (name, path))
                return out
            else:
                out['blocked'] = ('%s already has a block between Switchboard\'s %s markers (lines %d-%d), but its '
                                  'text is not Switchboard\'s and Switchboard has no record of writing it. Take it '
                                  'out by hand first.' % (path, name, lines[0], lines[1]))
                return out
        else:
            out['op'] = 'add'
            out['says'] = ('Adds the %s block (%d lines) at the top of %s%s. Nothing else in the file changes, and '
                           'turning it off takes out exactly these lines.'
                           % (name, len(spec['body']) + 2, path, '' if raw is not None else ', a new file'))
            if rec is not None:
                out['notes'].append('The block Switchboard added before is gone from the file; it is added again.')
        if not writable:
            out['blocked'] = '%s is set read-only, so Switchboard leaves it alone.' % path
        return out

    # off
    if where == 'absent':
        if rec is not None:
            out['op'], out['says'] = 'forget', '%s is already gone from %s.' % (name, path)
        else:
            out['says'] = '%s is off.' % name
        return out
    if rec is not None and not ours:
        out['blocked'] = ('You edited inside the %s block (lines %d-%d of %s) since Switchboard wrote it. Switchboard '
                          'will not take it out, because that would delete your edits. Take the block out yourself, '
                          'or put its lines back as they were and try again.' % (name, lines[0], lines[1], path))
        return out
    if rec is None and cur != want:
        out['blocked'] = ('%s has a block between Switchboard\'s %s markers (lines %d-%d) that is not Switchboard\'s '
                          'text, and Switchboard has no record of it. It is left for you.'
                          % (path, name, lines[0], lines[1]))
        return out
    after = without_block(raw, span)
    frec = st['files'].get(path) or {}
    others = [b for k, b in st['blocks'].items() if k != block_key(spec['id'], path) and b.get('file') == path]
    if after == b'' and rec is not None and frec.get('existed') is False and not others:
        out['op'] = 'delete-file'
        out['says'] = 'Deletes %s: Switchboard made it for this block and nothing else is in it.' % path
    else:
        out['op'] = 'remove'
        out['says'] = ('Takes out the %s block (lines %d-%d of %s). Every other byte of the file stays as it is.'
                       % (name, lines[0], lines[1], path))
    if not writable:
        out['blocked'] = '%s is set read-only, so Switchboard leaves it alone.' % path
    return out


def apply_block(plan_):
    """Make a plan_block() plan. Refuses (Refused, nothing written) when the plan is blocked, or when the file
    or Switchboard's record changed since the plan was made. Returns {'changed': [paths], 'op'}."""
    if plan_.get('blocked'):
        raise Refused(plan_['blocked'])
    op, path, sp = plan_['op'], plan_['file'], plan_['state_path']
    if op is None:
        return {'changed': [], 'op': None}
    st, raw_state = load_state(sp)
    if sha(raw_state) != plan_['state_sha']:
        raise Refused("Switchboard's record changed since this plan was made. Make a new plan.")
    raw = read_bytes(path)
    if sha(raw) != plan_['fingerprint']:
        raise Refused('%s changed since this plan was made. Nothing was written; make a new plan.' % path)
    bid = block_key(plan_['pack'], path)
    start, end = plan_['markers']
    want = plan_['block'].encode('utf-8')
    new = raw
    if op == 'add':
        new = insert_block(raw, want)
        span = (_top(raw), _top(raw) + len(want))
        if locate(new, start, end) != ('one', span) or without_block(new, span) != (raw or b''):
            raise Refused('Switchboard could not add the block to %s without changing something else; nothing was '
                          'written.' % path)
    elif op in ('replace', 'remove', 'delete-file'):
        where, span = locate(raw, start, end)
        if where != 'one':
            raise Refused('%s changed since this plan was made. Make a new plan.' % path)
        new = raw[:span[0]] + want + raw[span[1]:] if op == 'replace' else without_block(raw, span)
        if op == 'delete-file':
            if new != b'':
                raise Refused('%s holds more than the block now. Make a new plan.' % path)
            new = None
    mode = file_mode(path, 0o644)
    wrote = False
    try:
        if op not in ('adopt', 'forget'):
            if sha(read_bytes(path)) != plan_['fingerprint']:          # last look before the swap
                raise Refused('%s changed while Switchboard was writing; nothing was changed.' % path)
            if new is None:
                os.unlink(path)
            else:
                atomic_write(path, new, mode)
            wrote = True
        if op in ('add', 'replace', 'adopt'):
            st['files'].setdefault(path, {'existed': raw is not None})
            st['blocks'][bid] = {'file': path, 'sha': sha(want), 'lines': want.count(b'\n'), 'at': now()}
        else:
            st['blocks'].pop(bid, None)
            if not any(b.get('file') == path for b in st['blocks'].values()):
                st['files'].pop(path, None)
        save_state(sp, st)
    except BaseException:
        if wrote:
            try:
                if raw is None:
                    if os.path.lexists(path):
                        os.unlink(path)
                else:
                    atomic_write(path, raw, mode)
            except OSError:
                pass
        raise
    return {'changed': [path] if wrote else [], 'op': op}


def _top(raw):
    """Where a block added to raw starts: after a byte-order mark, else at 0."""
    return len(BOM) if raw and raw.startswith(BOM) else 0


def block_status(spec, claude_dir_=None, state_path=None):
    """Read-only: is the block on, edited, or off; its size."""
    cd = claude_dir(claude_dir_)
    path = instructions_path(cd)
    raw = read_bytes(path) if not os.path.isdir(path) else None
    try:
        st, _ = load_state(state_path)
        rec = st['blocks'].get(block_key(spec['id'], path))
    except Refused:
        rec = None
    where, span = locate(raw, spec['start'], spec['end'])
    text = '\n'.join([spec['start']] + list(spec['body']) + [spec['end']]) + '\n'
    state = 'off'
    if where == 'one':
        cur = raw[span[0]:span[1]]
        state = 'on' if rec and sha(cur) == rec.get('sha') else ('edited' if rec else 'not ours')
    elif where == 'broken':
        state = 'broken'
    return {'pack': spec['id'], 'state': state, 'file': path, 'lines': len(spec['body']) + 2,
            'chars': len(text), 'tokens_estimate': est_tokens(text)}
