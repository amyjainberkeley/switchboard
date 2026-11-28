"""What the adapters leave behind, read by Switchboard: their decisions and the facts they tell it.

<data folder>/router/decisions/<epoch ms>-<8 hex>.json: one decision each, written whole by an adapter for each piece
of work it looked at under a usable plan with a route or a default for it. fold() moves them into
<data folder>/router/decisions.jsonl: files not changed for FOLD_SETTLE_S, in name order, each good one appended and
then removed; one that still cannot be read FOLD_GIVE_UP_S after its name's time is removed and counted; at most
FOLD_PER_CALL a call. Adapters only add files; only this module reads or changes decisions.jsonl.

<data folder>/router/decisions.jsonl: one JSON object per line. read(n) gives the newest good records and how many lines
were passed over; trim() keeps the file under MAX_BYTES, newest whole lines kept; lines(recs) makes each record's
plain History sentence (words.py), from fixed words only: nothing an adapter wrote is shown as it is, except a model
or effort word the list of models spells that way.

<data folder>/router/signals/<epoch ms>-<8 hex>.json: one fact each (at_line, limit_stop). signals() reads the
well-formed ones, at most SIGNALS_PER_CALL a call, and removes each it read; anything else is left where it is and
counted.
"""
import json
import math
import os
import re
import stat
import time
from typing import List, Optional, Tuple

from .. import core
from ..providers import contract
from ..providers import kinds as _kinds
from ..providers import words as provider_words
from . import words

DECISIONS = 'router/decisions.jsonl'
DECIDED = 'router/decisions'        # one file per decision, as adapters write them
SIGNALS = 'router/signals'
LOCK = 'router/record.lock'
MAX_BYTES = 1024 * 1024         # the decisions file is kept under this
KEEP_BYTES = 512 * 1024         # what a trim keeps, so the next trim is not one line away
READ_MAX = 4 * 1024 * 1024      # read() looks at no more than this much of the file's end
SIGNAL_MAX_BYTES = 4096
SIGNALS_PER_CALL = 200
DECISION_MAX_BYTES = 4096
FOLD_SETTLE_S = 2               # a decision file is read only once it has not changed for this long
FOLD_GIVE_UP_S = 3600           # one that still cannot be read this long after its name's time is removed
FOLD_PER_CALL = 500
WHERE = ('chat', 'helper', 'workflow')
HAND_TYPE = 'switchboard-hand'      # the helper that runs a hand-over (hand.HELPER_TYPE); its record is the true one
DID = ('model', 'effort', 'hand', 'kept', 'none', 'would', 'carry_on')
QUIET = ('stays', 'bad_value', 'hand_later')     # reasons of a record that changed nothing and has no sentence
WHAT = ('at_line', 'limit_stop')
_SIGNAL_NAME = re.compile(r'^[0-9]{13}-[0-9a-f]{8}\.json\Z')     # a signal's or a decision's file name
_WINDOW = re.compile(r'^[a-z][a-z0-9_]{0,23}\Z')
_WORD = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._\[\]-]{0,63}\Z')
_PASSED_SHOWN = 4


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _id(x) -> bool:
    return isinstance(x, str) and bool(contract.ID_RE.match(x))


def _text_or_none(x) -> bool:
    return x is None or isinstance(x, str)


def _no_constants(word):
    raise ValueError(word)


def _loads(text):
    return json.loads(text, parse_constant=_no_constants)


# ---------- decisions ----------

def path() -> str:
    return core.data_path(DECISIONS)


def good(rec) -> bool:
    """A decision record in the shape the contract gives (section 3)."""
    if not isinstance(rec, dict) or not _num(rec.get('t')) or not _id(rec.get('agent')):
        return False
    if rec.get('where') not in WHERE or rec.get('did') not in DID or not _text_or_none(rec.get('kind')):
        return False
    if not all(_text_or_none(rec.get(k)) for k in ('type', 'from_model', 'model', 'effort', 'to', 'reason', 'why')):
        return False
    passed = rec.get('passed', [])
    if not isinstance(passed, list) or not all(isinstance(p, list) and len(p) == 2 and all(isinstance(x, str)
                                                                                         for x in p) for p in passed):
        return False
    return rec.get('plan') is None or _num(rec.get('plan'))


def read(n: Optional[int] = None) -> Tuple[List[dict], int]:
    """(the newest n good records, oldest first (all when n is None); how many lines could not be read). Only the
    last READ_MAX bytes are looked at; a line cut by that is not counted. Never raises."""
    try:
        with open(path(), 'rb') as f:
            size = os.fstat(f.fileno()).st_size
            if size > READ_MAX:
                f.seek(size - READ_MAX)
            data = f.read(READ_MAX)
    except OSError:
        return [], 0
    lines = data.split(b'\n')
    if size > READ_MAX:
        lines = lines[1:]
    recs, bad = [], 0
    for raw in lines:
        if not raw.strip():
            continue
        try:
            rec = _loads(raw.decode('utf-8'))
        except (ValueError, RecursionError):
            rec = None
        if good(rec):
            recs.append(rec)
        else:
            bad += 1
    if n is not None:
        recs = recs[-n:] if n > 0 else []
    return recs, bad


def trim() -> int:
    """Keep the decisions file under MAX_BYTES: when it is over, only its newest whole lines up to KEEP_BYTES stay
    (what an adapter appended while this ran is kept too). The bytes taken out."""
    os.makedirs(os.path.dirname(path()), mode=0o700, exist_ok=True)
    with core.file_lock(LOCK):
        try:
            with open(path(), 'rb') as f:
                data = f.read()
        except FileNotFoundError:
            return 0
        if len(data) <= MAX_BYTES:
            return 0
        tail = data[-KEEP_BYTES:]
        cut = tail.find(b'\n')
        tail = tail[cut + 1:] if cut >= 0 else b''
        with open(path(), 'rb') as f:              # appended since the read above
            f.seek(len(data))
            extra = f.read()
        core.write_bytes(path(), tail + extra, 0o600)
        return len(data) - len(tail)


def _decision(folder: str, name: str, now: float):
    """(record, None) for a settled good decision file; (None, True) for one to remove as broken; (None, None) to
    leave it for a later call (still being written, or not yet given up on)."""
    full = os.path.join(folder, name)
    given_up = now - int(name[:13]) / 1000.0 >= FOLD_GIVE_UP_S
    try:
        st = os.lstat(full)
    except FileNotFoundError:
        return None, None
    except OSError:
        return None, given_up or None
    if not stat.S_ISREG(st.st_mode) or st.st_size > DECISION_MAX_BYTES:
        return None, given_up or None
    if now - st.st_mtime < FOLD_SETTLE_S:
        return None, None
    try:
        fd = os.open(full, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as f:
            got = _loads(f.read(DECISION_MAX_BYTES + 1).decode('utf-8'))
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError, RecursionError):
        got = None
    if good(got):
        return got, None
    return None, given_up or None


def fold(now: Optional[float] = None) -> Tuple[int, int]:
    """Move the adapters' settled decision files into decisions.jsonl (see the module's opening lines): (how many
    were appended, how many broken ones were removed). A name not of the form <13 digits>-<8 hex>.json is left
    alone. Never raises."""
    now = time.time() if now is None else now
    folder = core.data_path(DECIDED)
    try:
        names = sorted(n for n in os.listdir(folder) if _SIGNAL_NAME.match(n))
    except OSError:
        return 0, 0
    try:
        with core.file_lock(LOCK):
            good_ones, broken = [], []
            for name in names:
                if len(good_ones) + len(broken) >= FOLD_PER_CALL:
                    break
                got, remove = _decision(folder, name, now)
                if got is not None:
                    good_ones.append((name, got))
                elif remove:
                    broken.append(name)
            if good_ones:
                data = b''.join(json.dumps(r, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
                                .encode('utf-8') + b'\n' for _, r in good_ones)
                fd = os.open(path(), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                with os.fdopen(fd, 'ab') as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
            removed = 0
            for name in [n for n, _ in good_ones] + broken:
                try:
                    os.remove(os.path.join(folder, name))
                except FileNotFoundError:
                    continue
                except OSError as e:
                    core.log('router record fold: %s' % type(e).__name__)
                    continue
                removed += name in broken
            return len(good_ones), removed
    except Exception as e:  # noqa: BLE001 - a fold that fails leaves the files for the next call
        core.log('router record fold: %s' % type(e).__name__)
        return 0, 0


# ---------- signals ----------

def _signal(folder: str, name: str):
    """The fact in one signal file, cleaned, or None when it is not a well-formed one."""
    if not _SIGNAL_NAME.match(name):
        return None
    full = os.path.join(folder, name)
    try:
        st = os.lstat(full)
        if not stat.S_ISREG(st.st_mode) or st.st_size > SIGNAL_MAX_BYTES:
            return None
        fd = os.open(full, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as f:
            got = _loads(f.read(SIGNAL_MAX_BYTES + 1).decode('utf-8'))
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(got, dict) or not _num(got.get('t')) or not _id(got.get('agent')) or \
            got.get('what') not in WHAT:
        return None
    window, pct = got.get('window'), got.get('pct')
    if window is not None and not (isinstance(window, str) and _WINDOW.match(window)):
        return None
    if pct is not None and not (_num(pct) and 0 <= pct <= 1000):
        return None
    if got['what'] == 'at_line' and (window is None or pct is None):
        return None
    return {'t': got['t'], 'agent': got['agent'], 'what': got['what'], 'window': window, 'pct': pct}


def signals() -> Tuple[List[dict], int]:
    """(the well-formed signal files' facts, oldest name first, at most SIGNALS_PER_CALL, each file removed once
    read; how many files were left because they are not well-formed). Never raises."""
    folder = core.data_path(SIGNALS)
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return [], 0
    got, left = [], 0
    for name in names:
        if len(got) >= SIGNALS_PER_CALL:
            break
        fact = _signal(folder, name)
        if fact is None:
            left += 1
            continue
        try:
            os.remove(os.path.join(folder, name))
        except FileNotFoundError:
            continue                            # another reader took it
        except OSError:
            left += 1
            continue
        got.append(fact)
    return got, left


# ---------- the History sentences ----------

def _app(pid) -> str:
    if not _id(pid):
        return words.APP_OTHER
    from .. import providers
    return providers.name_of(pid)


def _model(pid, word) -> Optional[str]:
    """A model as the list of models names it ('Haiku'), the word itself when the list does not know it but it is
    spelt like a model, or A_MODEL; None for no model."""
    if word is None:
        return None
    if not isinstance(word, str) or not _WORD.match(word):
        return words.A_MODEL
    try:
        from ..providers import catalog
        return provider_words.one_line(catalog.find_model(pid, word).name, 40)
    except Exception:  # noqa: BLE001 - a list that cannot be read, or a model it does not have: the word itself
        return word


def _known_model(pid, word) -> Optional[str]:
    """A model as the list of models names it, or None when the list does not know the word. For the model a caller
    named, which is its own text and could be a project's name."""
    if not isinstance(word, str) or not _WORD.match(word):
        return None
    try:
        from ..providers import catalog
        return provider_words.one_line(catalog.find_model(pid, word).name, 40)
    except Exception:  # noqa: BLE001 - a list that cannot be read, or a model it does not have: not shown
        return None


def _effort(word) -> Optional[str]:
    return word if isinstance(word, str) and _WORD.match(word) else None


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if not text.startswith('Your ') else 'your ' + text[5:]


def _passed(passed) -> str:
    shown = ['%s %s' % (_app(p[0]), words.PASSED[p[1]]) for p in passed or () if p[1] in words.PASSED]
    return words.PASSED_JOIN.join(shown[:_PASSED_SHOWN])


def _why(rec, kind) -> str:
    """Why the work ran as it did, from fixed words only: the default for where it started, the plan's words when
    they are the router's own sentence for this kind (a route, or a preset's), else the route's words for its kind."""
    if rec.get('reason') in ('default', 'effort_lower'):
        return words.DEFAULT_WHY[rec['where']]
    if not kind:
        return words.WHY_NONE
    return rec['why'] if rec.get('why') == words.PRESET_WHY[kind] else words.ROUTE_WHY[kind]


def line(rec) -> Optional[str]:
    """One decision as its History sentence, or None for a record that is not good or says nothing to tell."""
    if not good(rec):
        return None
    kind = rec['kind'] if rec['kind'] in _kinds.KINDS else None
    why = _why(rec, kind)
    app = _app(rec['agent'])
    subject = words.SUBJECT[rec['where']].format(helper=words.KIND_HELPER[kind or 'other'], app=app)
    work = words.KIND_WORK[kind or 'other']
    to = rec.get('to')
    model = _model(to if rec['did'] in ('hand', 'would') and _id(to) else rec['agent'], rec.get('model'))
    effort = _effort(rec.get('effort'))
    at = words.AT.format(effort=effort) if effort else ''
    on = words.ON.format(model=model) if model else ''
    did = rec['did']
    # "No choice could take it" is true only when every choice was passed. A route that names nothing to change, a
    # word that could not be used, or a hand-over not made yet changed nothing and has no sentence.
    if rec.get('reason') in QUIET or (did == 'none' and rec.get('reason') != 'all_passed'):
        return None
    if did == 'would':
        if _id(to) and to != rec['agent']:
            return words.LINE_WOULD_HAND.format(work=_lower_first(work), app=app, to=_app(to), on=on, at=at)
        if model:
            return words.LINE_WOULD_MODEL.format(subject=_lower_first(subject), model=model, at=at)
        if effort:
            return words.LINE_WOULD_EFFORT.format(subject=_lower_first(subject), effort=effort)
        return words.LINE_WOULD_NONE.format(subject=_lower_first(subject))
    if did == 'carry_on':
        return words.LINE_CARRY_ON.format(subject=subject)
    if did == 'hand' and rec.get('type') != HAND_TYPE:
        return None             # one record per hand-over: the hand helper's own, written once it was really handed
    if did == 'hand':
        return words.LINE_HAND.format(work=work, app=app, to=_app(to), on=on, at=at, why=why) if _id(to) else None
    if did == 'kept' and rec.get('reason') == 'effort_lower':
        return words.LINE_KEPT_EFFORT.format(subject=subject, effort=effort, why=why) if effort else \
            words.LINE_KEPT_EFFORT_BARE.format(subject=subject, why=why)
    if did == 'kept':
        old = _known_model(rec['agent'], rec.get('from_model'))
        return words.LINE_KEPT_MODEL.format(subject=subject, model=old) if old else \
            words.LINE_KEPT.format(subject=subject)
    if did == 'none':
        passed = _passed(rec.get('passed'))
        return words.LINE_NONE.format(subject=subject, why=why, passed=passed) if passed else \
            words.LINE_NONE_BARE.format(subject=subject, why=why)
    if did == 'model' and model:
        return words.LINE_MODEL.format(subject=subject, model=model, at=at, why=why)
    if effort:
        return words.LINE_EFFORT.format(subject=subject, effort=effort, why=why)
    return None


def lines(recs) -> List[Tuple[float, str]]:
    """(time, History sentence) for each record that has one, in the order given."""
    out = []
    for rec in recs or ():
        text = line(rec)
        if text:
            out.append((float(rec['t']), text))
    return out
