"""The commands of Switchboard's other assistants: providers, add, continue, back, hand, allow, stay and order.

`switchboard/__main__.py` hands these words here (`main(args)`). Every command works the same for every provider: the
provider is a word after the command, never a command of its own. What a command reaches outside this process comes
through one Kit (the Reach, the adapters, and the modules beside this one), so a test hands it stand-ins whole.

What the commands keep to, whatever the provider:
- Nothing leaves without the person's say. A chat's note, or a job about a folder, goes to another vendor only from a
  folder the person said yes for, for that provider. The yes is asked only at a terminal, after the note is made and
  its size shown, and never given by a flag. `back` asks none (the person typed it) but honours a `never` and a stay.
  `hand` and `allow` run only at a terminal: from an assistant's own shell tool they print the line to run.
- Every value from outside Switchboard that is printed (a chat's name, a path, an answer, a program's last words) goes
  through words.safe or words.one_line first.
- No lock is held while a question is asked or a program starts; the one job lock is held while the job runs, so one
  job runs at a time.
- Exit codes: 0 done; 1 refused or failed, with one sentence; 2 the words were wrong (the command's own help lines are
  printed); 130 stopped with Ctrl-C (or a closed terminal, while a job runs).
"""
import json
import os
import shlex
import signal
import sys
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from . import contract, words
from .contract import Found, Handoff, Hop, Limits, Provider, ProviderError, Reach, Room, Unsupported

COMMANDS = ('providers', 'add', 'continue', 'back', 'hand', 'allow', 'stay', 'order')

FRESH_ENGINE_S = 3600.0         # Claude's account code reads the account in use every 5 minutes (place: section 8.4)
FRESH_FILE_S = 7 * 86400.0      # a provider's own saved numbers: its weekly window is the one that matters
WORDS_FRESH_S = 3600.0          # "has room" only for numbers read within the hour; older ones say how old
LIST_MAX = 30                   # chats listed by number; the rest are counted
BACK_NOTE_BUDGET = 48000        # a note of the other side's work for back, and a job's note of its chat: half a
HAND_NOTE_BUDGET = 48000        # carried-on chat's (DESIGN 10.4)
HEADLESS = ('sdk',)             # an entrypoint that starts with one of these is a headless run, listed only by name
ABOVE_MAX = 6                   # folders above this one looked at for chats begun there, when none began here
ABOVE_NAMED = 3                 # and how many of them are named
# Every reason code place.pick gives (place.REASONS) and what the commands do with it: a provider picked, or a refusal.
PICK_REASONS = ('room', 'unknown', 'out', 'all_out', 'not_added', 'not_installed', 'signed_out', 'is_source',
                'no_other')
# The states of a continue that started something: only such a move makes a chat "already carried on".
STARTED = ('started', 'in_app', 'printed')


class Usage(Exception):
    """The words were wrong: the command's own help lines are printed, exit 2. A message, when given, comes first."""


class Refused(Exception):
    """The command stops here: its one sentence is printed, exit 1."""


@dataclass
class Kit:
    """Everything the commands reach. default_kit() builds the real one; tests build one of stand-ins.

    load(pid) -> Provider or None (its module is not in this build, or did not load: load_errors says why, by id).
    handoff, tasks, place, choices, run: the modules beside this one (or stand-ins with the same functions).
    say(text, kind, **facts): one History line (kinds info, setting, problem). ask(prompt) -> the person's answer
    (only ever called at a terminal). out: where lines are printed. cwd(): the folder the command was typed in.
    headless: entrypoint prefixes of headless runs."""
    reach: Reach
    load: Callable[[str], Optional[Provider]]
    load_errors: Mapping[str, str]
    handoff: Any
    tasks: Any
    place: Any
    choices: Any
    run: Any
    say: Callable[..., None]
    ask: Callable[[str], str]
    out: Any
    cwd: Callable[[], str] = os.getcwd
    headless: Tuple[str, ...] = HEADLESS


def _history(text, kind='info', **facts):
    """One History line, as history.note does, without importing history.py. Never raises: the History never stops
    what it records."""
    from switchboard import core
    try:
        with core.locked() as st:
            core.event(st, text, 'warn' if kind == 'problem' else 'info', h=kind, by='you', **facts)
    except Exception as e:  # noqa: BLE001 - a History line that cannot be written is logged, never raised
        core.log('providers history: %s' % type(e).__name__)


def _ask_terminal(prompt):
    try:
        return input(prompt)
    except EOFError:
        return ''


def default_kit(reach=None) -> Kit:
    """The real Kit: the real Reach (or the one given), the registry's adapters and the modules beside this one,
    imported only now."""
    from . import LOAD_ERRORS, adapter_class
    from . import choices, handoff, place, run, tasks
    reach = reach or run.default_reach()
    try:
        from .chat_facts import HEADLESS_ENTRYPOINTS as headless
    except ImportError:
        headless = HEADLESS

    def load(pid):
        cls = adapter_class(pid)
        return cls(reach) if cls is not None else None

    return Kit(reach=reach, load=load, load_errors=LOAD_ERRORS, handoff=handoff, tasks=tasks, place=place,
               choices=choices, run=run, say=_history, ask=_ask_terminal, out=sys.stdout,
               headless=tuple(headless))


def main(args: Sequence[str], kit: Optional[Kit] = None) -> int:
    """`switchboard CMD ...` for CMD in COMMANDS: its exit code."""
    args = list(args)
    cmd = args[0] if args else ''
    if cmd not in COMMANDS:
        return 2
    if kit is None:
        try:
            kit = default_kit()
        except ImportError as e:            # a build without every part of the package: say so, start nothing
            sys.stdout.write('This Switchboard cannot run switchboard %s: a part of it is missing (%s).\n'
                             % (cmd, type(e).__name__))
            return 1
    ctx = _Ctx(kit)
    try:
        return _RUN[cmd](ctx, args[1:])
    except Usage as e:
        if str(e):
            ctx.out(str(e))
        ctx.out(words.HELP[cmd])
        return 2
    except Refused as e:
        ctx.out(str(e))
        return 1
    except ProviderError as e:
        ctx.out(words.DISK if e.kind == 'disk' else words.one_line(str(e), 400))
        return 1
    except KeyboardInterrupt:
        ctx.out('')
        return 130
    finally:
        _prune(ctx)


def _prune(ctx: '_Ctx'):
    """Old notes, answers and moves go (handoff.prune, tasks.prune), once per command that recorded a move: at its end,
    or just before an exec, which never comes back. Each in its own guard: a prune that fails is logged, never printed,
    and never changes what the command did."""
    if not ctx.moved or ctx.pruned:
        return
    ctx.pruned = True
    for name, prune in (('handoff', lambda: ctx.kit.handoff.prune(ctx.reach, ctx.now)),
                        ('tasks', lambda: ctx.kit.tasks.prune(ctx.now))):
        try:
            prune()
        except Exception as e:  # noqa: BLE001 - removing old notes never stops the move it follows
            try:
                from switchboard import core
                core.log('providers %s.prune: %s' % (name, type(e).__name__))
            except Exception:  # noqa: BLE001
                pass


# ---------- what one command run knows ----------


class _Ctx:
    """One command run: the kit, the time it started, and what it asked the adapters (each asked once)."""

    def __init__(self, kit: Kit):
        self.kit = kit
        self.reach = kit.reach
        self.now = kit.reach.wall()
        self._prov: Dict[str, Optional[Provider]] = {}
        self._found: Dict[str, Optional[Found]] = {}
        self._limits: Dict[str, Tuple[Limits, ...]] = {}
        self._seats: Dict[str, tuple] = {}
        self._folder: Optional[str] = None
        self.moved = False              # a move (hop) was recorded: old notes are pruned at the end
        self.pruned = False

    # --- the person ---

    def out(self, line=''):
        self.kit.out.write(line + '\n')
        try:
            self.kit.out.flush()
        except (AttributeError, ValueError, OSError):
            pass

    def tty(self) -> bool:
        return bool(self.reach.tty())

    def ask(self, prompt) -> str:
        """The person's answer. Never asked without a terminal: no answer is then given for them."""
        if not self.tty():
            return ''
        return (self.kit.ask(prompt) or '').strip()

    def yes(self, prompt, default=False) -> bool:
        a = self.ask(prompt).lower()
        return default if a == '' else a in ('y', 'yes')

    def say(self, text, kind, **facts):
        self.kit.say(text, kind, **facts)

    @property
    def folder(self) -> str:
        if self._folder is None:
            self._folder = os.path.realpath(self.kit.cwd())
        return self._folder

    @property
    def basename(self) -> str:
        return words.one_line(os.path.basename(self.folder) or self.folder, 60)

    # --- the providers ---

    def known(self, word) -> bool:
        from . import known
        return known(word)

    def ids(self) -> Tuple[str, ...]:
        from . import ids
        return ids()

    def prov(self, pid) -> Optional[Provider]:
        if pid not in self._prov:
            self._prov[pid] = self.kit.load(pid) if self.known(pid) else None
        return self._prov[pid]

    def name(self, pid) -> str:
        p = self.prov(pid)
        return p.name if p is not None and p.name else pid.capitalize()

    def vendor(self, pid) -> str:
        p = self.prov(pid)
        return p.vendor if p is not None and p.vendor else self.name(pid)

    def app(self, pid) -> str:
        """Its program's name in a sentence ('Claude Code'), as its adapter says it; else its name."""
        return getattr(self.prov(pid), 'app_name', None) or self.name(pid)

    def sign_in(self, pid) -> str:
        """How a person signs in once in its own app, as its adapter says it."""
        return getattr(self.prov(pid), 'sign_in', '') or words.SIGN_IN_PLAIN

    def command(self, pid) -> str:
        """The word a person types to start a provider's terminal program. A program is kept by the path of the file
        itself (a link on PATH may lead to claude.exe or codex.js), so the file's name is never what is shown."""
        return getattr(self.prov(pid), 'command', None) or pid

    def can(self, pid, what) -> bool:
        p = self.prov(pid)
        return p is not None and what in p.can

    def found(self, pid) -> Optional[Found]:
        if pid not in self._found:
            p = self.prov(pid)
            got = p.detect() if p is not None else None
            self._found[pid] = got if isinstance(got, Found) else None
        return self._found[pid]

    def seats(self, pid) -> tuple:
        if pid not in self._seats:
            p = self.prov(pid)
            got = p.seats() if p is not None else ()
            self._seats[pid] = tuple(got) if isinstance(got, tuple) else ()
        return self._seats[pid]

    def limits(self, pid, ask=False) -> Tuple[Limits, ...]:
        if pid not in self._limits or ask:
            p = self.prov(pid)
            got = p.limits(ask=ask) if p is not None else ()
            self._limits[pid] = tuple(got) if isinstance(got, tuple) else ()
        return self._limits[pid]

    def added(self) -> Dict[str, float]:
        return dict(self.kit.choices.added())

    def order(self) -> List[str]:
        return [p for p in self.kit.choices.order() if self.known(p)]

    def room(self, pid, limits=None) -> Room:
        if pid not in self.added():
            return Room(pid, contract.NOT_ADDED)
        f = self.found(pid)
        if f is None or not f.installed:
            return Room(pid, contract.NOT_INSTALLED)
        if f.signed_in is False:
            return Room(pid, contract.SIGNED_OUT)
        lims = self.limits(pid) if limits is None else limits
        fresh = FRESH_ENGINE_S if any(lim.source == 'engine' for lim in lims) else FRESH_FILE_S
        return self.kit.place.room_of(pid, self.name(pid), lims, self.now, fresh)

    def one_login(self, pid) -> bool:
        """A provider with one login (Codex): its line names the plan, not a count of accounts."""
        s = self.seats(pid)
        return len(s) == 1 and s[0].key == pid

    def state_words(self, pid, room: Room) -> str:
        if room.state == contract.ROOM:
            if room.read_at is not None and self.now - room.read_at > WORDS_FRESH_S:
                return words.STATE_HAD_ROOM.format(ago=words.ago(self.now - room.read_at))
            return words.STATE_ROOM
        if room.state == contract.OUT:
            if room.back_at is None:
                return words.STATE_OUT_NOT_KNOWN
            many = not self.one_login(pid) and len(self.seats(pid)) > 1
            return (words.STATE_OUT_ALL if many else words.STATE_OUT).format(when=words.when(room.back_at, self.now))
        return {contract.UNKNOWN: words.STATE_UNKNOWN, contract.SIGNED_OUT: words.STATE_SIGNED_OUT,
                contract.NOT_INSTALLED: words.STATE_NOT_INSTALLED,
                contract.NOT_ADDED: words.STATE_NOT_ADDED}.get(room.state, words.STATE_UNKNOWN)

    def plan_label(self, pid) -> Optional[str]:
        """The plan as the person sees it ('ChatGPT Pro'), for a provider with one login."""
        if not self.one_login(pid):
            return None
        return words.one_line(self.seats(pid)[0].label, 60) or None

    def work_plan(self, pid) -> bool:
        plans = [lim.plan for lim in self.limits(pid)] + [s.plan for s in self.seats(pid)]
        return any(isinstance(p, str) and words.WORK_PLAN_RE.match(p) for p in plans)

    # --- the folder's choices ---

    def consent(self, pid, folder=None) -> Optional[str]:
        return self.kit.choices.consent(folder or self.folder, pid)

    def stays(self, folder=None) -> Optional[str]:
        return self.kit.choices.stays(folder or self.folder)

    def provider_word(self, word) -> str:
        """A provider id the person typed: known (else exit 2 with the sentence that lists the known ones)."""
        if not self.known(word):
            raise Usage(words.UNKNOWN_PROVIDER.format(word=words.one_line(word, 32), ids=', '.join(self.ids())))
        return word

    def must_be_added(self, pid):
        if pid not in self.added():
            raise Refused(words.NOT_ADDED.format(name=self.name(pid), pid=pid))

    def refuse_stay_and_never(self, pid, folder=None):
        """A stay on another provider, or a `never` for this one, ends the command (continue, back, hand): for this
        folder, or for the folder given (the one a chat's work moved into, when that is used)."""
        stay = self.stays(folder)
        if stay and stay != pid:
            raise Refused(words.STAYS.format(p=self.name(stay)))
        if self.consent(pid, folder) == 'never':
            raise Refused(words.NEVER.format(to=self.name(pid), to_id=pid))

    def refuse_too_old(self, pid):
        f = self.found(pid)
        if f is None or not f.installed:
            raise Refused(words.NOT_ON_MAC.format(name=self.name(pid)))
        if f.program is None:
            raise Refused(words.ADD_NO_COPY.format(name=self.name(pid), why=_refused_why(f)))
        if _older(f.program.version, f.newest_seen):
            raise Refused(words.TOO_OLD.format(name=self.app(pid), version=words.one_line(f.program.version, 40),
                                               seen=words.one_line(f.newest_seen, 40)))

    def billing_line(self, pid) -> Optional[str]:
        """The line saying a start runs on the app's own sign-in, when the shell holds a key it would have used (a
        name in contract.BILLING_ENV that starts with one of its adapter's billing_prefixes)."""
        env = self.reach.env or {}
        prefixes = tuple(getattr(self.prov(pid), 'billing_prefixes', ()) or ())
        if any(env.get(n) and n.startswith(prefixes) for n in contract.BILLING_ENV):
            return words.KEY_TAKEN_OUT.format(to=self.name(pid), vendor=self.vendor(pid))
        return None


def _older(version, seen) -> bool:
    from .run import version_key
    return bool(seen) and bool(version) and version_key(version) < version_key(seen)


def _refused_why(found: Found) -> str:
    why = [p.refused for p in found.programs if p.refused]
    return words.one_line(why[0] if why else 'none passed the checks', 120)


def _removed(handoff: Handoff) -> int:
    return sum(int(n) for _, n in handoff.removed)


def _within(path, root) -> bool:
    real, top = os.path.realpath(path), os.path.realpath(root)
    return real.startswith(top + os.sep)


# ---------- switchboard providers ----------


def cmd_providers(ctx: _Ctx, args) -> int:
    ask = json_out = False
    for a in args:
        if a == '--ask':
            ask = True
        elif a == '--json':
            json_out = True
        else:
            raise Usage()
    asked = _ask_now(ctx) if ask else {}
    if json_out:
        ctx.out(json.dumps(_board_json(ctx, asked), sort_keys=True))
        return 0
    for line in _ask_lines(ctx, asked):
        ctx.out(line)
    for line in _board(ctx):
        ctx.out(line)
    return 0


def _ask_now(ctx: _Ctx) -> Dict[str, Tuple[str, str]]:
    """Ask each added provider that can for its numbers now, once the person's one yes is given: pid to (outcome,
    why). An ask starts the app's helper, so it runs only at a terminal, where the yes is asked the first time;
    otherwise the saved numbers are shown."""
    done = {}
    for pid in ctx.order():
        if pid not in ctx.added() or not ctx.can(pid, contract.ASK):
            continue
        if not ctx.tty():
            done[pid] = (contract.ASK_OFF, '')
            continue
        if not ctx.kit.choices.ask_ok(pid):
            if not ctx.yes(words.ASK_QUESTION.format(name=ctx.name(pid))):
                done[pid] = (contract.ASK_OFF, '')
                continue
            ctx.kit.choices.set_ask_ok(ctx.now, pid=pid)
        lims = ctx.limits(pid, ask=True)
        outcome = next((lim.ask for lim in lims if lim.ask), None) or contract.ASK_REFUSED
        why = next((lim.error for lim in lims if lim.error), '') or ''
        done[pid] = (outcome, why)
        if outcome == contract.ASK_REFUSED:
            ctx.say(words.H_ASK_REFUSED.format(name=ctx.name(pid), why=words.one_line(why or 'no answer', 80)),
                    'problem', reason='ask refused')
    return done


def _ask_times(ctx: _Ctx, pid) -> Tuple[Optional[float], Optional[float]]:
    """(when the app was last asked, when the next ask may be) from the ask's own pacing file, when it says."""
    try:
        with open(os.path.join(ctx.reach.data_dir, contract.ASK_FILE), encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None, None
    entry = data.get(pid) if isinstance(data, dict) and isinstance(data.get(pid), dict) else data
    if not isinstance(entry, dict) or not isinstance(entry.get('at'), (int, float)):
        return None, None
    at = float(entry['at'])
    back = entry.get('backoff_until')
    return at, max(at + 300.0, float(back) if isinstance(back, (int, float)) else 0.0)


def _ask_lines(ctx: _Ctx, asked) -> List[str]:
    lines = []
    for pid, (outcome, why) in asked.items():
        name = ctx.name(pid)
        if outcome == contract.ASK_PACED:
            at, nxt = _ask_times(ctx, pid)
            lines.append(words.ASK_PACED_SOON.format(name=name) if at is None else words.ASK_LINES['paced'].format(
                name=name, ago=words.ago(ctx.now - at), when=words.when(nxt, ctx.now)))
        else:
            text = words.ASK_LINES.get(outcome, words.ASK_LINES['refused'])
            lines.append(text.format(name=name, why=words.one_line(why or 'no answer', 120)))
    return lines


def _board_ids(ctx: _Ctx) -> List[str]:
    order = ctx.order()
    return order + [p for p in ctx.ids() if p not in order]


def _board(ctx: _Ctx) -> List[str]:
    lines = []
    for pid in _board_ids(ctx):
        lines.extend(_board_block(ctx, pid))
    lines.append('')
    lines.append(_folder_line(ctx))
    lines.append(words.ORDER_LINE.format(names=words.names([ctx.name(p) for p in ctx.order()])))
    return lines


def _head(ctx, pid, text) -> str:
    return '%-9s%s' % (ctx.name(pid), text)


def _board_block(ctx: _Ctx, pid) -> List[str]:
    pad = ' ' * 9
    if ctx.prov(pid) is None:
        error = ctx.kit.load_errors.get(pid)
        if error:
            return [_head(ctx, pid, words.BOARD_NOT_LOADED.format(error=words.one_line(error, 60)))]
        return [_head(ctx, pid, words.BOARD_NOT_HERE)]
    f = ctx.found(pid)
    if f is None or not f.installed:
        return [_head(ctx, pid, words.BOARD_NOT_HERE)]
    added = ctx.added()
    if pid not in added:
        shown = f.program or (f.programs[0] if f.programs else None)
        version = words.one_line(shown.version or '', 40) if shown else ''
        return [_head(ctx, pid, words.BOARD_NOT_ADDED.format(app=ctx.app(pid), version=version, pid=pid))]
    lines = [_head(ctx, pid, _board_state(ctx, pid))]
    if f.program is None:
        lines.append(pad + words.BOARD_NO_PROGRAM)
    else:
        lines.extend(pad + x for x in _program_lines(ctx, pid, f))
    for p in f.programs:
        if p.refused:
            lines.append(pad + words.BOARD_REFUSED.format(where=words.one_line(p.where, 60),
                                                          version=words.one_line(p.version or '', 40),
                                                          refused=words.one_line(p.refused, 80)))
    at = added.get(pid) or 0
    if at:
        can = [w for what, w in words.CAN_WORDS if what in ctx.prov(pid).can]
        part = [words.BOARD_ADDED.format(date=words.date(at, ctx.now))]
        if can:
            part.append(words.BOARD_CAN.format(what=', '.join(can)))
        lines.append(pad + ' · '.join(part))
    return lines


def _board_state(ctx: _Ctx, pid) -> str:
    room = ctx.room(pid)
    state = ctx.state_words(pid, room)
    if room.state in (contract.SIGNED_OUT, contract.NOT_INSTALLED):
        return state
    seats = ctx.seats(pid)
    lims = ctx.limits(pid)
    if ctx.one_login(pid):
        lim = lims[0] if lims else None
        read = words.BOARD_READ_NONE
        if lim is not None and lim.read_at is not None:
            fmt = words.BOARD_READ_ASK if lim.source == 'ask' else words.BOARD_READ_FILE
            read = fmt.format(ago=words.ago(ctx.now - lim.read_at))
        parts = [state, ctx.plan_label(pid)] + [_window(ctx, w) for w in (lim.windows if lim else ())] + [read]
        return ' · '.join(x for x in parts if x)
    if not seats:
        return state
    under = 0
    for s in seats:
        mine = tuple(lim for lim in lims if lim.seat == s.key)
        if ctx.kit.place.room_of(pid, ctx.name(pid), mine, ctx.now, _fresh(mine)).state == contract.ROOM:
            under += 1
    in_use = [s for s in seats if s.in_use]
    text = words.BOARD_ACCOUNTS.format(state=state, n=len(seats), k=under,
                                       label=words.one_line(in_use[0].label, 80) if in_use else '')
    return text if in_use else text.rsplit(' · ', 1)[0]


def _fresh(lims) -> float:
    return FRESH_ENGINE_S if any(lim.source == 'engine' for lim in lims) else FRESH_FILE_S


def _window(ctx: _Ctx, w) -> str:
    label = words.one_line(w.label, 30)
    if w.used_pct is None:
        return words.BOARD_WINDOW_UNKNOWN.format(label=label)
    pct = int(round(w.used_pct))
    if w.resets_at is None:
        return words.BOARD_WINDOW_NO_RESET.format(label=label, pct=pct)
    return words.BOARD_WINDOW.format(label=label, pct=pct, when=words.when(w.resets_at, ctx.now))


def _program_lines(ctx: _Ctx, pid, f: Found) -> List[str]:
    prog = f.program
    usable = [p for p in f.programs if not p.refused]
    on_path = [p for p in f.programs if p.source == 'path']
    more = ''
    if len(usable) > 1:
        more += words.BOARD_PROGRAM_NEWEST.format(n=len(usable))
    path_prog = on_path[0] if on_path else None
    command = ctx.command(pid) if path_prog else ''
    if path_prog is not None and path_prog.path != prog.path:
        more += words.BOARD_PROGRAM_PATH.format(command=words.one_line(command, 40),
                                                path_version=words.one_line(path_prog.version or '?', 40))
    lines = [words.BOARD_PROGRAM.format(app=ctx.app(pid), version=words.one_line(prog.version or '?', 40),
                                        where=words.one_line(prog.where, 60), more=more)]
    if path_prog is not None and _older(path_prog.version, f.newest_seen):
        lines.append(words.BOARD_PATH_OLDER.format(command=words.one_line(command, 40), app=ctx.app(pid),
                                                   path_version=words.one_line(path_prog.version, 40),
                                                   seen=words.one_line(f.newest_seen, 40)))
        lines.append(words.BOARD_PATH_USED.format(path=words.one_line(prog.path, 200)))
    return lines


def _folder_line(ctx: _Ctx) -> str:
    stay = ctx.stays()
    if stay:
        what = words.FOLDER_STAYS.format(p=ctx.name(stay))
    else:
        parts = []
        for pid in ctx.order():
            if pid not in ctx.added():
                continue
            state = ctx.consent(pid)
            if state is None and pid == ctx.order()[0]:
                continue                # the first in the order is where chats start; it is offered nothing
            if state == 'yes':
                at = _consent_at(ctx, pid)
                parts.append(words.FOLDER_YES.format(to=ctx.name(pid), date=words.date(at, ctx.now)) if at
                             else words.FOLDER_YES_NO_DATE.format(to=ctx.name(pid)))
            elif state == 'never':
                parts.append(words.FOLDER_NEVER.format(to=ctx.name(pid), pid=pid))
            else:
                parts.append(words.FOLDER_NOT_YET.format(to=ctx.name(pid)))
        what = '; '.join(parts) or words.FOLDER_NOT_YET.format(to='another provider')
    return words.FOLDER_LINE.format(basename=ctx.basename, what=what)


def _consent_at(ctx: _Ctx, pid) -> Optional[float]:
    getter = getattr(ctx.kit.choices, 'consent_at', None)
    return getter(ctx.folder, pid) if callable(getter) else None


def _board_json(ctx: _Ctx, asked) -> dict:
    added = ctx.added()
    providers = []
    for pid in _board_ids(ctx):
        prov = ctx.prov(pid)
        f = ctx.found(pid) if prov is not None else None
        room = ctx.room(pid) if prov is not None else Room(pid, contract.NOT_INSTALLED)
        prog = f.program if f is not None else None
        providers.append({
            'id': pid, 'name': ctx.name(pid), 'vendor': ctx.vendor(pid), 'added': added.get(pid),
            'state': room.state, 'why': ctx.state_words(pid, room), 'back_at': room.back_at,
            'program': {'path': prog.path, 'version': prog.version, 'where': prog.where} if prog else None,
            'programs': len(f.programs) if f is not None else 0,
            'signed_in': f.signed_in if f is not None else None,
            'newest_seen': f.newest_seen if f is not None else None,
            'seats': [asdict(s) for s in ctx.seats(pid)] if prov is not None else [],
            'limits': [asdict(lim) for lim in ctx.limits(pid)] if prov is not None else [],
            'can': sorted(prov.can) if prov is not None else [],
            'load_error': ctx.kit.load_errors.get(pid) if prov is None else None,
        })
    consent = {pid: ctx.consent(pid) for pid in ctx.order() if ctx.consent(pid)}
    ask = None
    if asked:
        pid, (outcome, why) = next(iter(asked.items()))
        ask = {'outcome': outcome, 'why': why}
    return {'v': 1, 'order': ctx.order(), 'providers': providers,
            'folder': {'path': ctx.folder, 'stays': ctx.stays(), 'consent': consent}, 'ask': ask}


# ---------- switchboard add PROVIDER ----------


def cmd_add(ctx: _Ctx, args) -> int:
    if len(args) != 1:
        raise Usage()
    pid = ctx.provider_word(args[0])
    return _add(ctx, pid)


def _add(ctx: _Ctx, pid) -> int:
    name = ctx.name(pid)
    added = ctx.added()
    if pid in added:
        at = added.get(pid) or 0
        ctx.out(words.ADD_ALREADY.format(name=name, date=words.date(at, ctx.now) if at else 'from the start'))
        return 0
    f = ctx.found(pid)
    if ctx.prov(pid) is None or f is None or not f.installed:
        raise Refused(words.ADD_NOT_INSTALLED.format(name=name, vendor=ctx.vendor(pid), pid=pid))
    if f.program is None:
        raise Refused(words.ADD_NO_COPY.format(name=name, why=_refused_why(f)))
    ctx.kit.choices.add(pid, ctx.now)
    ctx.say(words.H_ADDED.format(name=name), 'setting', to=name)
    ctx.out(words.ADD_DONE.format(name=name, order=words.names([ctx.name(p) for p in ctx.order()])))
    ctx.out(words.ADD_WILL_RUN.format(app=ctx.app(pid), version=words.one_line(f.program.version or '?', 40),
                                      where=words.one_line(f.program.where, 60), name=name))
    if f.signed_in is True:
        ctx.out(words.ADD_SIGNED_IN)
    elif f.signed_in is False:
        ctx.out(words.ADD_SIGN_IN.format(name=name, how=ctx.sign_in(pid)))
    else:
        ctx.out(words.ADD_SIGN_IN_NOT_KNOWN)
    if _older(f.program.version, f.newest_seen):
        ctx.out(words.ADD_TOO_OLD.format(name=name, version=words.one_line(f.program.version, 40),
                                         seen=words.one_line(f.newest_seen, 40)))
    return 0


# ---------- choosing a chat (continue, back, hand) ----------


def _chat_name(chat) -> str:
    """A chat's name for a printed line; one with no name is its app and the start of its id, never the whole id."""
    if chat.name:
        return words.one_line(chat.name, 80)
    return _unnamed(chat.provider, chat.id)


def _unnamed(pid, cid) -> str:
    return words.UNNAMED.format(app=_app_of(pid), short=words.one_line((cid or '')[:8], 8))


def _app_of(pid) -> str:
    """A provider's program name for a line with no adapter at hand: its class in the registry says it ('a' for an
    id the registry does not know)."""
    from . import adapter_class, known
    cls = adapter_class(pid) if known(pid) else None
    return (getattr(cls, 'app_name', None) or getattr(cls, 'name', '') or 'a') if cls is not None else 'a'


def _all_chats(ctx: _Ctx) -> list:
    """Every chat of every added provider whose own lines say it ran in this folder."""
    chats = []
    for pid in ctx.order():
        if pid not in ctx.added():
            continue
        prov = ctx.prov(pid)
        got = prov.chats(ctx.folder) if prov is not None else ()
        if isinstance(got, Unsupported):
            continue
        chats.extend(c for c in got if c.folder and os.path.realpath(c.folder) == ctx.folder
                     and contract.CHAT_ID_RE.match(c.id or ''))
    return chats


def _started_above(ctx: _Ctx) -> List[str]:
    """Folders above this one, nearest first, where a chat of the last day was started (at most ABOVE_NAMED): a window
    is often started in one folder while its work goes on in a folder below it, and a chat is listed under the first."""
    found: List[str] = []
    here = ctx.folder
    for _ in range(ABOVE_MAX):
        up = os.path.dirname(here)
        if up == here or up == os.sep:
            break
        here = up
        for pid in ctx.order():
            if pid not in ctx.added():
                continue
            if any(c.live or (c.last_at or 0) >= ctx.now - 86400 for c in _offered(ctx, _chats_of(ctx, pid, up))):
                found.append(up)
                break
        if len(found) >= ABOVE_NAMED:
            break
    return found


def _offered(ctx: _Ctx, chats) -> list:
    """The ones offered without --chat: live, stopped at a limit, or written in the last day (else all), no headless
    runs."""
    plain = [c for c in chats if not (c.entrypoint or '').startswith(tuple(ctx.kit.headless))]
    recent = [c for c in plain if c.live or c.stopped or (c.last_at or 0) >= ctx.now - 86400]
    return recent or plain


def _ordered(ctx: _Ctx, chats) -> list:
    """Stopped at a limit first, then live, then newest; within one of those, the order of the providers."""
    rank = {pid: i for i, pid in enumerate(ctx.order())}
    groups = {}
    for c in chats:
        groups.setdefault((not c.stopped, not c.live), []).append(c)
    out = []
    for key in sorted(groups):
        out.extend(sorted(groups[key], key=lambda c: (rank.get(c.provider, 99), -(c.last_at or 0))))
    return out


def _match(chats, x) -> list:
    """--chat X: a whole name (any case), else the start of a name or of an id (4 or more hex characters)."""
    low = x.lower()
    whole = [c for c in chats if c.name and c.name.lower() == low]
    if whole:
        return whole
    hexy = len(x) >= 4 and all(ch in '0123456789abcdef-' for ch in low)
    return [c for c in chats if (c.name and c.name.lower().startswith(low)) or (hexy and c.id.startswith(low))]


def _chat_lines(ctx: _Ctx, chats) -> List[str]:
    lines = []
    for i, c in enumerate(chats[:LIST_MAX], 1):
        lines.append(words.CHAT_LINE.format(
            i=i, name=_chat_name(c), app=ctx.app(c.provider), stopped=words.CHAT_STOPPED if c.stopped else '',
            live=words.CHAT_LIVE if c.live else words.CHAT_NOT_LIVE, ago=words.ago(ctx.now - (c.last_at or ctx.now))))
    if len(chats) > LIST_MAX:
        lines.append(words.MORE_CHATS.format(n=len(chats) - LIST_MAX))
    return lines


def _choose(ctx: _Ctx, chat_word: Optional[str], latest: bool):
    everything = _all_chats(ctx)
    if chat_word is not None:
        found = _match(everything, chat_word)
        x = words.one_line(chat_word, 60)
        if not found:
            raise Refused(words.NO_MATCH.format(x=x))
        if len(found) > 1:
            raise Refused('\n'.join([words.SEVERAL_MATCH.format(x=x)] + _chat_lines(ctx, _ordered(ctx, found))))
        return found[0]
    offered = _ordered(ctx, _offered(ctx, everything))
    if not offered:
        names = ' or '.join("%s's" % ctx.name(p) for p in ctx.order() if p in ctx.added())
        above = _started_above(ctx)
        where = (words.NO_CHAT_THERE.format(folders=', '.join(words.one_line(f, 200) for f in above)) if above
                 else words.NO_CHAT_WHERE)
        raise Refused(words.NO_CHAT.format(names=names, days=contract.CHATS_DAYS) + '\n' + where)
    if latest:
        c = max(offered, key=lambda c: c.last_at or 0)
        ctx.out(words.LATEST.format(name=_chat_name(c), app=ctx.app(c.provider),
                                    ago=words.ago(ctx.now - (c.last_at or ctx.now))))
        return c
    mine = [c for c in everything if _this_shell(ctx, c)]
    if mine:
        ctx.out(words.THIS_SHELL.format(name=_chat_name(mine[0]), app=ctx.app(mine[0].provider)))
        return mine[0]
    if len(offered) == 1:
        return offered[0]
    listing = [words.SEVERAL.format(n=len(offered))] + _chat_lines(ctx, offered)
    if not ctx.tty():
        raise Refused('\n'.join(listing))
    for line in listing:
        ctx.out(line)
    answer = ctx.ask(words.WHICH_CHAT)
    if not (answer.isascii() and answer.isdigit()) or not 1 <= int(answer) <= min(len(offered), LIST_MAX):
        raise Refused(words.NOT_A_NUMBER)
    return offered[int(answer) - 1]


def _this_shell(ctx: _Ctx, chat) -> bool:
    """This shell was started from that chat: the name its provider's app sets in its own shell (the adapter's
    session_env) holds the chat's id."""
    name = getattr(ctx.prov(chat.provider), 'session_env', '') or ''
    here = (ctx.reach.env or {}).get(name) if name else None
    return bool(here) and chat.id == here


# ---------- the yes for a folder ----------


def _consent_question(ctx: _Ctx, to, frm, handoff: Optional[Handoff], where=None) -> str:
    """The yes for this folder. where: the folder the app will work in (this folder when not given)."""
    size = show = ''
    if handoff is not None:
        size = words.CONSENT_SIZE.format(size=words.size(handoff.bytes), k=_removed(handoff))
        show = words.CONSENT_SHOW
    there = _there(ctx, where) if where else None
    jobs = words.CONSENT_JOBS.format(to=ctx.name(to)) if ctx.can(to, contract.JOB) else ''
    return words.CONSENT.format(to=ctx.name(to), vendor=ctx.vendor(to), frm=ctx.name(frm), size=size, show=show,
                                work=words.WORK_PLAN if ctx.work_plan(to) else '', basename=ctx.basename,
                                where=there or words.THIS_FOLDER, jobs=jobs)


def _set_consent(ctx: _Ctx, pid, state, folder=None):
    folder = folder or ctx.folder
    ctx.kit.choices.set_consent(folder, pid, state, ctx.now)
    text = words.H_YES if state == 'yes' else words.H_NEVER
    basename = words.one_line(os.path.basename(folder) or folder, 60)
    ctx.say(text.format(basename=basename, to=ctx.name(pid)), 'setting', to=ctx.name(pid))


def _ask_consent(ctx: _Ctx, to, frm, handoff: Optional[Handoff], where=None) -> str:
    """The question, at a terminal: 'yes', 'never' or 'no'. With a note, s prints it and asks again."""
    while True:
        ctx.out(_consent_question(ctx, to, frm, handoff, where).rstrip('\n'))
        answer = ctx.ask(words.PROMPT).lower()
        if answer in ('y', 'yes'):
            return 'yes'
        if answer == 'never':
            return 'never'
        if answer == 's' and handoff is not None:
            ctx.out(_note_text(handoff))
            continue
        return 'no'


def _note_text(handoff: Handoff) -> str:
    with open(handoff.path, encoding='utf-8', errors='replace') as f:
        return words.safe(f.read())


def _drop_note(ctx: _Ctx, handoff: Handoff):
    """Remove a note nobody said yes to, and its sidecar: only files inside the notes folder."""
    root = ctx.kit.run.notes_root(ctx.reach)
    for path in (handoff.path, handoff.meta_path):
        if path and _within(path, root):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass


# ---------- switchboard continue ----------


@dataclass
class _ContinueArgs:
    to: Optional[str] = None
    chat: Optional[str] = None
    latest: bool = False
    how: Optional[str] = None
    show: bool = False
    again: bool = False


def _parse_continue(ctx: _Ctx, args) -> _ContinueArgs:
    a = _ContinueArgs()
    it = iter(args)
    for word in it:
        if word in ('--to', '--chat', '--in'):
            value = next(it, None)
            if value is None:
                raise Usage()
            if word == '--to':
                a.to = ctx.provider_word(value)
            elif word == '--chat':
                a.chat = value
            elif value in (contract.IN_TERMINAL, contract.IN_APP):
                a.how = value
            else:
                raise Usage()
        elif word == '--latest':
            a.latest = True
        elif word == '--show':
            a.show = True
        elif word == '--again':
            a.again = True
        else:
            raise Usage()
    if a.chat is not None and a.latest:
        raise Usage()
    return a


def _offer_add(ctx: _Ctx, want: Optional[str]):
    """Before a move: the provider named with --to must be added; without --to, some other provider must be. At a
    terminal, when the one named (or, with none named, the only other one installed) is here but not added, offer
    to add it and go on."""
    added = ctx.added()
    if want is not None:
        if want in added:
            return
        here = [want] if _installed(ctx, want) else []
    elif len(added) > 1:
        return
    else:
        here = [p for p in ctx.ids() if p not in added and _installed(ctx, p)]
    if ctx.tty() and len(here) == 1:
        pid = here[0]
        prog = ctx.found(pid).program
        if ctx.yes(words.OFFER_ADD.format(app=ctx.app(pid), version=words.one_line(prog.version or '?', 40)),
                   default=True):
            _add(ctx, pid)
            return
    if want is not None:
        raise Refused(words.NOT_ADDED.format(name=ctx.name(want), pid=want))
    raise Refused(_no_other(ctx, added))


def _no_other(ctx: _Ctx, added) -> str:
    """Why there is no other provider to carry on in: none in this build, the first other one not on this Mac (how
    to install it), or not added (how to add it)."""
    first = next((p for p in ctx.ids() if p not in added), None)
    if first is None or ctx.prov(first) is None:
        return words.NONE_OTHER
    f = ctx.found(first)
    if f is None or not f.installed:
        return words.NONE_OTHER_INSTALL.format(name=ctx.name(first), vendor=ctx.vendor(first), pid=first)
    return words.NO_OTHER.format(pid=first, name=ctx.name(first))


def _installed(ctx: _Ctx, pid) -> bool:
    f = ctx.found(pid) if ctx.prov(pid) is not None else None
    return f is not None and f.installed and f.program is not None


def _room_line(ctx: _Ctx, pid, room: Room) -> List[str]:
    state = ctx.state_words(pid, room)
    details = []
    plan = ctx.plan_label(pid)
    if plan:
        details.append(plan)
        lims = ctx.limits(pid)
        if lims:
            details.extend('%s %d%%' % (words.one_line(w.label, 30), round(w.used_pct))
                           for w in lims[0].windows if w.used_pct is not None)
    if room.read_at is not None:
        details.append(words.ROOM_READ.format(ago=words.ago(ctx.now - room.read_at)))
    line = (words.ROOM_LINE_MORE.format(name=ctx.name(pid), state=state, details=', '.join(details)) if details
            else words.ROOM_LINE.format(name=ctx.name(pid), state=state))
    lines = [line]
    old = room.read_at is None or ctx.now - room.read_at > WORDS_FRESH_S
    if old and ctx.can(pid, contract.ASK) and room.state in (contract.ROOM, contract.UNKNOWN):
        lines.append(words.ROOM_ASK_HINT)
    return lines


def _pick(ctx: _Ctx, source, to) -> str:
    rooms = {pid: ctx.room(pid) for pid in ctx.ids()}
    target, reason = ctx.kit.place.pick(ctx.order(), source, rooms, to)
    if target is not None:
        ctx.out(words.ROOM_LINE.format(name=ctx.name(source), state=ctx.state_words(source, rooms[source])))
        for line in _room_line(ctx, target, rooms[target]):
            ctx.out(line)
        if reason == 'unknown':
            ctx.out(words.TARGET_UNKNOWN.format(to=ctx.name(target)))
        return target
    want = to or next((p for p in ctx.order() if p != source and p in ctx.added()), source)
    if reason == 'is_source':
        raise Refused(words.IS_SOURCE.format(to=ctx.name(want)))
    if reason == 'not_added':
        raise Refused(words.NOT_ADDED.format(name=ctx.name(want), pid=want))
    if reason == 'not_installed':
        raise Refused(words.NOT_ON_MAC.format(name=ctx.name(want)))
    if reason == 'signed_out':
        raise Refused(words.SIGNED_OUT.format(name=ctx.name(want), how=ctx.sign_in(want)))
    if reason == 'all_out':
        each = [words.ALL_OUT_EACH.format(name=ctx.name(p), when=words.when(rooms[p].back_at, ctx.now))
                for p in ctx.order() if p in ctx.added() and p != source]
        raise Refused(words.ALL_OUT.format(each=', '.join(each)))
    raise Refused(_no_other(ctx, ctx.added()))


def _earlier(ctx: _Ctx, task) -> tuple:
    """This task's earlier notes, oldest first: (path, when, from) for "Earlier in this task"."""
    rows = [h for h in ctx.kit.tasks.hops(ctx.folder, limit=50) if h.task == task and h.handoff]
    return tuple((h.handoff, h.at, ctx.name(h.from_provider)) for h in reversed(rows))


def cmd_continue(ctx: _Ctx, args) -> int:
    opts = _parse_continue(ctx, args)
    _offer_add(ctx, opts.to)
    chat = _choose(ctx, opts.chat, opts.latest)
    source = chat.provider
    target = _pick(ctx, source, opts.to)
    ctx.refuse_stay_and_never(target)
    asked = ctx.consent(target)
    if asked is None and not ctx.tty():
        raise Refused(words.NOT_ASKED.format(to=ctx.name(target), vendor=ctx.vendor(target), to_id=target))
    ctx.refuse_too_old(target)
    tasks = ctx.kit.tasks
    earlier_hop = tasks.continued_from(ctx.folder, chat.id)
    if earlier_hop is not None and earlier_hop.state in STARTED and not opts.again and \
            (chat.last_at or 0) <= earlier_hop.at:
        raise Refused(words.ALREADY.format(to=ctx.name(earlier_hop.to_provider),
                                           when=words.when(earlier_hop.at, ctx.now)))
    work = _work(ctx, chat, target)
    moved = _moved_line(ctx, chat, target, work)
    if moved:
        ctx.out(moved)
    if chat.working:
        ctx.out(words.STILL_WORKING)
    elif chat.live and chat.stopped:
        ctx.out(words.MAY_CARRY_ON.format(source=ctx.name(source)))
    handoff, task = _make_note(ctx, chat, target, words.CONTINUE_WHY.format(to=ctx.name(target)),
                               ctx.kit.choices.handoff_budget(), where=work)
    if asked is None:
        answer = _ask_consent(ctx, target, source, handoff, work)
        if answer != 'yes':
            _drop_note(ctx, handoff)
            if answer == 'never':
                _set_consent(ctx, target, 'never')
            raise Refused(words.REMOVED)
        _set_consent(ctx, target, 'yes')
    found = ctx.found(target)
    hop = tasks.add_hop(Hop(id=handoff.hop, task=task, kind='continue', at=ctx.now, folder=ctx.folder,
                            from_provider=source, from_chat=chat.id, to_provider=target, handoff=handoff.path,
                            sha256=handoff.sha256, bytes=handoff.bytes, removed=_removed(handoff), state='made',
                            program_version=found.program.version if found and found.program else None))
    ctx.moved = True
    if opts.show:
        ctx.out(words.SHOW_NOTE.format(path=words.one_line(handoff.path, 400)))
        ctx.out(_note_text(handoff))
        if not ctx.tty() or not ctx.yes(words.SHOW_ASK.format(to=ctx.name(target))):
            tasks.update_hop(hop.id, state='failed')        # nothing started: it never blocks the next continue
            return 0
    how = opts.how or ctx.kit.choices.open_with(target)
    if how != contract.PRINT and not ctx.tty():     # nothing is started without a person at a terminal
        how = contract.PRINT
    return _launch(ctx, hop, chat, handoff, target, how, start=True, where=work)


def _raw_work(chat) -> Optional[str]:
    """ChatRef.work_folder (the folder a chat's newest lines ran in) when it is another folder than the one the chat
    was started in, absolute and still a folder; else None. Not yet a folder to use: see _work."""
    work = getattr(chat, 'work_folder', None)
    if isinstance(work, str) and os.path.isabs(work) and os.path.isdir(work) and \
            os.path.realpath(work) != os.path.realpath(chat.folder):
        return work
    return None


def _too_wide(ctx: _Ctx, path) -> bool:
    """/, the home folder, or a folder that holds it: never a folder another assistant starts in."""
    real = os.path.realpath(path)
    home = os.path.realpath(ctx.reach.home or os.sep)
    return real == os.sep or real == home or home.startswith(real + os.sep)


def _work(ctx: _Ctx, chat, to, starts=True) -> str:
    """The folder where things stand is read (git runs there) and another assistant starts: the chat's own folder,
    or the folder its work moved into when that may be used. A folder inside the chat's own is covered by the yes for
    it. One outside is used only with a yes for that folder and that provider, asked here at a terminal before
    anything runs there; without it the chat's own folder is used and one line says how to allow the other. /, the
    home folder and a folder that holds it are never used. A stay or a never for the folder used ends the command.
    The chat itself is opened again, and its content asked about, by its own folder."""
    own, work = chat.folder, _raw_work(chat)
    if work is None:
        return own
    shown, started = words.one_line(work, 200), words.one_line(own, 200)
    if _too_wide(ctx, work):
        ctx.out(words.WORK_TOO_WIDE.format(work=shown, started=started))
        return own
    ctx.refuse_stay_and_never(to, work)
    if _within(work, own) or ctx.consent(to, work) == 'yes':
        return work
    if not ctx.tty():
        ctx.out(words.WORK_NOT_ALLOWED.format(work=shown, started=started, to_id=to))
        return own
    more = words.WORK_ASK_STARTS.format(to=ctx.name(to)) if starts else ''
    if not ctx.yes(words.WORK_ASK.format(work=shown, started=started, starts=more)):
        return own
    _set_consent(ctx, to, 'yes', folder=work)
    return work


def _moved_line(ctx: _Ctx, chat, to, work) -> Optional[str]:
    """The one line saying the next assistant starts where the chat's work is, when that is not where it began."""
    if os.path.realpath(work) == os.path.realpath(chat.folder):
        return None
    return words.WORK_MOVED.format(started=words.one_line(chat.folder, 200), work=words.one_line(work, 200),
                                   to=ctx.name(to))


def _make_note(ctx: _Ctx, chat, to, why, budget, where=None) -> Tuple[Handoff, str]:
    """Read the chat and write its note (nothing has left the Mac yet): (the note, its task). Where things stand is
    read in where (this folder when not given); when that is not the folder the chat was started in, the note says
    where it was started (stand['started_in'])."""
    if ctx.tty():
        ctx.out(words.MAKING.format(name=ctx.kit.handoff.fact(_chat_name(chat))))
    tasks = ctx.kit.tasks
    task = tasks.task_for(ctx.folder, chat.provider, chat.id)
    hop_id = tasks.new_hop_id()
    prov = ctx.prov(chat.provider)
    transcript = prov.read(chat, contract.ReadLimits())
    if isinstance(transcript, Unsupported):
        raise Refused(words.one_line(transcript.why, 300))
    if transcript.info.degraded:
        ctx.out(words.one_line(transcript.info.degraded, 300))
    where = where or ctx.folder
    stand = ctx.kit.handoff.where_things_stand(where, ctx.reach)
    if os.path.realpath(where) != os.path.realpath(chat.folder) and isinstance(stand, dict):
        stand = dict(stand, started_in=chat.folder)
    handoff = ctx.kit.handoff.make(transcript, hop=hop_id, task=task, to_name=ctx.name(to),
                                   from_name=ctx.name(chat.provider), why=why, stand=stand,
                                   earlier=_earlier(ctx, task), budget=budget, reach=ctx.reach)
    return handoff, task


def _note_line(ctx: _Ctx, handoff: Handoff, chat) -> str:
    return words.NOTE_LINE.format(path=words.one_line(handoff.path, 400), size=words.size(handoff.bytes),
                                  places=words.places(_removed(handoff)), name=_chat_name(chat))


def _history_move(ctx: _Ctx, text, handoff: Handoff, to, frm, kind='info'):
    ctx.say(text.format(to=ctx.name(to), frm=ctx.name(frm), basename=ctx.basename, size=words.size(handoff.bytes),
                        places=words.places(_removed(handoff))), kind, frm=ctx.name(frm), to=ctx.name(to),
            reason='carry on')


def _there(ctx: _Ctx, where: str) -> Optional[str]:
    """The folder a start goes to, for the lines that would otherwise say "this folder"; None when it is this one."""
    return None if os.path.realpath(where) == ctx.folder else words.one_line(where, 200)


def _open_line(ctx: _Ctx, target, handoff: Handoff, where: str) -> str:
    there = _there(ctx, where)
    path = words.one_line(handoff.path, 400)
    return (words.PRINTED_OPEN_THERE.format(to=ctx.name(target), folder=there, path=path) if there
            else words.PRINTED_OPEN.format(to=ctx.name(target), path=path))


def _launch(ctx: _Ctx, hop: Hop, chat, handoff: Handoff, target, how, start: bool, where=None) -> int:
    """Start the target's app on the note: in this terminal (exec), its desktop app's link, or the line to print. A
    start goes to where (the chat's work folder; this folder when not given); opening a chat again keeps its own."""
    prov = ctx.prov(target)
    tasks = ctx.kit.tasks
    where = where or ctx.folder
    there = _there(ctx, where) if start else None
    try:
        launch = prov.start(where, handoff, how) if start else prov.resume(chat, handoff, how)
    except ProviderError:
        tasks.update_hop(hop.id, state='failed')
        raise
    if isinstance(launch, Unsupported):
        tasks.update_hop(hop.id, state='failed')
        raise Refused(words.one_line(launch.why, 300))
    to = ctx.name(target)
    if how == contract.PRINT:
        tasks.update_hop(hop.id, state='printed')
        _history_move(ctx, words.H_PRINTED, handoff, target, hop.from_provider)
        ctx.out(words.PRINTED.format(path=words.one_line(handoff.path, 400), size=words.size(handoff.bytes),
                                     places=words.places(_removed(handoff))))
        if launch.line:
            ctx.out(words.PRINTED_RUN.format(to=to))
            ctx.out('  ' + launch.line)
        else:
            ctx.out(_open_line(ctx, target, handoff, where if start else ctx.folder))
        return 0
    if how == contract.IN_APP:
        ran = ctx.reach.open_url(launch.url, launch.app)
        if ran.code != 0:
            return _not_started(ctx, hop, handoff, target, launch, 'the link did not open', where)
        tasks.update_hop(hop.id, state='in_app')
        _history_move(ctx, words.H_OPENED, handoff, target, hop.from_provider)
        ctx.out(_note_line(ctx, handoff, chat))
        ctx.out(words.IN_APP_THERE.format(to=to, folder=there) if there else words.IN_APP.format(to=to))
        return 0
    tasks.update_hop(hop.id, state='started')
    _history_move(ctx, words.H_STARTED, handoff, target, hop.from_provider)
    key_line = ctx.billing_line(target)
    if key_line:
        ctx.out(key_line)
    prog = launch.program
    started = dict(to=ctx.app(target), version=words.one_line(prog.version or '?', 40) if prog else '?',
                   where=words.one_line(prog.where, 60) if prog else '?')
    ctx.out(words.STARTED_THERE.format(folder=there, **started) if there else words.STARTED.format(**started))
    ctx.out(_note_line(ctx, handoff, chat))
    return _exec(ctx, hop, handoff, target, launch, where)


def _exec(ctx: _Ctx, hop: Hop, handoff: Handoff, target, launch, where=None, before=None) -> int:
    _prune(ctx)                             # an exec that works never comes back to the end of the command
    try:
        sys.stdout.flush()
    except (AttributeError, ValueError, OSError):
        pass
    if before is not None:
        before()
    try:
        ctx.reach.execve(launch.argv[0], list(launch.argv), dict(launch.env), launch.cwd)
    except OSError as e:
        return _not_started(ctx, hop, handoff, target, launch, e.strerror or type(e).__name__, where)
    return 0                                # only a stand-in exec comes back


def _not_started(ctx: _Ctx, hop: Hop, handoff: Handoff, target, launch, error, where=None) -> int:
    ctx.kit.tasks.update_hop(hop.id, state='failed')
    ctx.say(words.H_NOT_STARTED.format(to=ctx.name(target), basename=ctx.basename), 'problem', to=ctx.name(target),
            reason='could not start')
    ctx.out(words.CANNOT_START.format(to=ctx.name(target), error=words.one_line(error, 120)))
    line = launch.line
    if not line and launch.how != contract.IN_APP:          # an app's link has no line to type: name the file
        line = ctx.kit.run.shell_line(launch.argv, launch.cwd, ctx.reach.env)
    ctx.out('  ' + line if line else _open_line(ctx, target, handoff, where or ctx.folder))
    return 1


# ---------- switchboard back ----------


def cmd_back(ctx: _Ctx, args) -> int:
    chat_word, show = None, False
    it = iter(args)
    for word in it:
        if word == '--chat':
            chat_word = next(it, None)
            if chat_word is None:
                raise Usage()
        elif word == '--show':
            show = True
        else:
            raise Usage()
    tasks = ctx.kit.tasks
    if chat_word is not None:
        hop, left, away = _move_by_name(ctx, chat_word)
    else:
        hop = tasks.open_continue(ctx.folder)
        if hop is None:
            raise Refused(words.BACK_NOTHING)
        left = _left_chat(ctx, hop)
        away = _away_chat(ctx, hop, left)
    target = hop.from_provider
    if away is None:
        raise Refused(words.BACK_NO_CHAT.format(to=ctx.name(hop.to_provider), when=words.when(hop.at, ctx.now)))
    if hop.to_chat != away.id:
        hop = tasks.update_hop(hop.id, to_chat=away.id)
    ctx.refuse_stay_and_never(target)
    work = _work(ctx, away, target, starts=left is None and not show)
    if left is None and not show:
        moved = _moved_line(ctx, away, target, work)
        if moved:
            ctx.out(moved)
    handoff, _ = _make_note(ctx, away, target, words.BACK_WHY.format(from_name=ctx.name(target)), BACK_NOTE_BUDGET,
                            where=work)
    back = tasks.add_hop(Hop(id=handoff.hop, task=hop.task, kind='back', at=ctx.now, folder=ctx.folder,
                             from_provider=hop.to_provider, from_chat=away.id, to_provider=target,
                             to_chat=hop.from_chat, handoff=handoff.path, sha256=handoff.sha256, bytes=handoff.bytes,
                             removed=_removed(handoff), state='made'))
    ctx.moved = True
    frm, to = ctx.name(target), ctx.name(hop.to_provider)
    paste = words.BACK_WORDS.format(hop=handoff.hop, path=handoff.path, other_name=to)
    name = _chat_name(left) if left is not None else _unnamed(hop.from_provider, hop.from_chat)
    if show:
        ctx.out(words.SHOW_NOTE.format(path=words.one_line(handoff.path, 400)))
        ctx.out(words.BACK_PASTE.format(name=name))
        ctx.out('  ' + words.one_line(paste, 2000))
        return 0
    if left is None:
        ctx.out(words.BACK_GONE.format(frm=frm))
        return _launch_back(ctx, hop, back, None, handoff, target, start=True, where=work)
    prov = ctx.prov(target)
    if left.live:                           # open in a window: never opened twice; the hooks, else the paste line
        bridge = getattr(prov, 'bridge', None)
        can = bridge.hooks_can_deliver() if bridge is not None else None
        if can and contract.CHAT_ID_RE.match(left.id):
            text = ctx.kit.handoff.back_note(handoff, other_name=to, start=hop.at, end=ctx.now)   # epoch seconds
            if bridge.put_note(left.id, text, handoff.path, handoff.hop, reach=ctx.reach):
                ctx.out(words.BACK_HOOKED.format(frm=frm, to=to, path=words.one_line(handoff.path, 400),
                                                 size=words.size(handoff.bytes),
                                                 places=words.places(_removed(handoff)), name=name))
                return _back_done(ctx, hop, back, handoff, 0)
        _paste(ctx, frm, to, handoff, name, paste)
        if can is False:
            ctx.out(words.BACK_HOOKS_OFF)
        return _back_done(ctx, hop, back, handoff, 0, state='printed')
    if ctx.tty():
        if ctx.yes(words.BACK_OPEN_ASK.format(name=name)):
            return _launch_back(ctx, hop, back, left, handoff, target, start=False)
        _paste(ctx, frm, to, handoff, name, paste)
        return _back_done(ctx, hop, back, handoff, 0, state='printed')
    launch = prov.resume(left, handoff, contract.PRINT)
    ctx.out(words.BACK_NOTE.format(frm=frm, to=to, path=words.one_line(handoff.path, 400),
                                   size=words.size(handoff.bytes)))
    if isinstance(launch, Unsupported) or not launch.line:
        ctx.out(words.BACK_PASTE.format(name=name))
        ctx.out('  ' + words.one_line(paste, 2000))
    else:
        ctx.out(words.BACK_RUN)
        ctx.out('  ' + launch.line)
    return _back_done(ctx, hop, back, handoff, 0, state='printed')


def _left_chat(ctx: _Ctx, hop: Hop):
    """The chat a move left, when its file is still here."""
    return next((c for c in _chats_of(ctx, hop.from_provider) if c.id == hop.from_chat), None)


def _move_by_name(ctx: _Ctx, chat_word) -> tuple:
    """back --chat NAME: (move, the chat it left, the chat it led to) for the open continue in this folder whose
    chat matches the name. Each open move's chat is found as plain back finds it (the ledger's, else by its note's
    mark, here and where the chat it left was working), so a move just made, with no chat in the ledger yet, is
    found too."""
    moves = []
    for hop in ctx.kit.tasks.hops(ctx.folder, limit=50):
        if hop.kind != 'continue' or hop.state in ('returned', 'failed'):
            continue
        left = _left_chat(ctx, hop)
        away = _away_chat(ctx, hop, left)
        if away is not None and all(away.id != m[2].id for m in moves):
            moves.append((hop, left, away))
    found = _match([m[2] for m in moves], chat_word)
    if len(found) > 1:
        raise Refused('\n'.join([words.SEVERAL_MATCH.format(x=words.one_line(chat_word, 60))]
                                + _chat_lines(ctx, _ordered(ctx, found))))
    if not found:
        raise Refused(words.BACK_NOTHING)
    return next(m for m in moves if m[2].id == found[0].id)


def _paste(ctx: _Ctx, frm, to, handoff, name, paste):
    ctx.out(words.BACK_NOTE.format(frm=frm, to=to, path=words.one_line(handoff.path, 400),
                                   size=words.size(handoff.bytes)))
    ctx.out(words.BACK_PASTE.format(name=name))
    ctx.out('  ' + words.one_line(paste, 2000))


def _chats_of(ctx: _Ctx, pid, folder=None) -> tuple:
    """A provider's chats started in folder (this one when not given), as its reader lists them."""
    prov = ctx.prov(pid)
    where = folder or ctx.folder
    try:
        got = prov.chats(where) if prov is not None else ()
    except (ProviderError, OSError):
        if folder is None:
            raise
        return ()                           # another folder, looked at only for a hint
    return () if isinstance(got, Unsupported) else tuple(got)


def _away_chat(ctx: _Ctx, hop: Hop, left=None):
    """The chat the move led to: the one the ledger names, else the newest chat of that provider begun after the move
    whose first message is that note's (the adapter's own finder when it has one). Looked for in this folder and in
    the work folder of the chat that was left, where the move started the other assistant."""
    places = [ctx.folder]
    if left is not None and _raw_work(left) is not None:
        places.append(_raw_work(left))
    chats = []
    for i, place in enumerate(places):
        chats.extend(c for c in _chats_of(ctx, hop.to_provider, place if i else None) if c not in chats)
    if hop.to_chat:
        return next((c for c in chats if c.id == hop.to_chat), None)
    prov = ctx.prov(hop.to_provider)
    finder = getattr(getattr(prov, 'chatfiles', None), 'started_from', None)
    if callable(finder):
        for place in places:
            got = finder(ctx.reach, place, hop.id, hop.at)
            if got is not None:
                return got
    mark = words.NOTE_MARK_TEXT.format(hop=hop.id)
    later = sorted((c for c in chats if (c.started_at or c.last_at or 0) >= hop.at - 5),
                   key=lambda c: -(c.started_at or c.last_at or 0))
    for c in later:
        t = prov.read(c, contract.ReadLimits())
        if isinstance(t, Unsupported):
            continue
        for ev in t.events:
            m = words.NOTE_MARK_RE.match(ev.text or '')
            if ev.kind == contract.NOTICE and (ev.text == mark or (m and m.group(2) == hop.id)):
                return c
    return None


def _launch_back(ctx: _Ctx, hop: Hop, back: Hop, chat, handoff: Handoff, target, start: bool, where=None) -> int:
    """Open the chat that was left again on the note (or, when its file is gone, a new chat of that provider, started
    in where: the other side's work folder): in this terminal after the move is closed and the History line written,
    or the line to run."""
    prov = ctx.prov(target)
    how = contract.IN_TERMINAL if ctx.tty() else contract.PRINT
    where = where or ctx.folder
    try:
        launch = prov.start(where, handoff, how) if start else prov.resume(chat, handoff, how)
    except ProviderError as e:
        ctx.kit.tasks.update_hop(back.id, state='failed')
        raise Refused(words.one_line(str(e), 300))
    if isinstance(launch, Unsupported):
        ctx.kit.tasks.update_hop(back.id, state='failed')
        raise Refused(words.one_line(launch.why, 300))
    if how == contract.PRINT:
        ctx.out(words.BACK_NOTE.format(frm=ctx.name(target), to=ctx.name(back.from_provider),
                                       path=words.one_line(handoff.path, 400), size=words.size(handoff.bytes)))
        ctx.out(words.BACK_RUN)
        ctx.out('  ' + launch.line if launch.line else
                _open_line(ctx, target, handoff, where if start else ctx.folder))
        return _back_done(ctx, hop, back, handoff, 0, state='printed')
    prog = launch.program
    if not start:
        ctx.out(words.BACK_OPENING.format(name=_chat_name(chat), app=ctx.app(target),
                                          version=words.one_line(prog.version or '?', 40) if prog else '?'))
    # An exec that works never comes back: the move is closed and the History line written just before it; one that
    # fails says so after it (as continue does) and the move is open again.
    code = _exec(ctx, back, handoff, target, launch, where if start else None,
                 before=lambda: _back_done(ctx, hop, back, handoff, 0, state='started'))
    if code != 0:
        ctx.kit.tasks.update_hop(hop.id, state=hop.state)        # it did not start: the move is still open
    return code


def _back_done(ctx: _Ctx, hop: Hop, back: Hop, handoff: Handoff, code: int, state=None) -> int:
    if code != 0:
        return code
    if state:
        ctx.kit.tasks.update_hop(back.id, state=state)
    ctx.kit.tasks.update_hop(hop.id, state='returned')
    ctx.say(words.H_BACK.format(frm=ctx.name(hop.from_provider), to=ctx.name(hop.to_provider), basename=ctx.basename,
                                size=words.size(handoff.bytes)), 'info', frm=ctx.name(hop.to_provider),
            to=ctx.name(hop.from_provider), reason='come back')
    return 0


# ---------- switchboard hand ----------


def _parse_hand(args) -> Tuple[Optional[str], Optional[str], str]:
    if not args:
        raise Usage()
    pid, rest = args[0], list(args[1:])
    chat, job = None, []
    while rest:
        word = rest.pop(0)
        if word == '--':
            job.extend(rest)
            break
        if word == '--chat' and not job:
            if not rest:
                raise Usage()
            chat = rest.pop(0)
            continue
        job.append(word)
        job.extend(rest)
        break
    text = ' '.join(job).strip()
    if not text:
        raise Usage()
    return pid, chat, text


def cmd_hand(ctx: _Ctx, args) -> int:
    pid, chat_word, job = _parse_hand(args)
    pid = ctx.provider_word(pid)
    if not ctx.can(pid, contract.JOB):
        own = (getattr(ctx.prov(pid), 'cannot', None) or {}).get(contract.JOB)
        raise Refused(own or words.NO_JOB.format(name=ctx.name(pid)))
    to = ctx.name(pid)
    if not ctx.tty():
        quoted = shlex.quote(words.one_line(job, 4000))
        ctx.out(words.HAND_NOT_TTY.format(to=to))
        if chat_word is None:
            ctx.out(words.HAND_LINE.format(to_id=pid, job=quoted))
        else:
            ctx.out(words.HAND_LINE_CHAT.format(to_id=pid, chat=shlex.quote(words.one_line(chat_word, 200)),
                                                job=quoted))
        return 0
    ctx.must_be_added(pid)
    ctx.refuse_stay_and_never(pid)
    ctx.refuse_too_old(pid)
    chat = handoff = task = None
    if chat_word is not None:                   # the note first: the question says what leaves, with its size
        chat = _choose(ctx, chat_word, False)
        handoff, task = _make_note(ctx, chat, pid, words.CONTINUE_WHY.format(to=to), HAND_NOTE_BUDGET)
    nothing = words.REMOVED if handoff is not None else words.NOTHING_SENT
    try:
        if ctx.consent(pid) is None:
            answer = _ask_consent(ctx, pid, chat.provider, handoff) if handoff is not None else _ask_hand(ctx, pid)
            if answer != 'yes':
                if answer == 'never':
                    _set_consent(ctx, pid, 'never')
                raise Refused(nothing)
            _set_consent(ctx, pid, 'yes')
        question = getattr(ctx.prov(pid), 'job_question', '') or ''
        if question and not ctx.kit.choices.hand_ack(pid):     # the adapter's own question, once on this Mac
            if not ctx.yes(question):
                raise Refused(nothing)
            ctx.kit.choices.set_hand_ack(ctx.now, pid)
    except (Refused, KeyboardInterrupt):
        if handoff is not None:
            _drop_note(ctx, handoff)
        raise
    with ctx.kit.run.try_lock(contract.HAND_LOCK_NAME) as held:
        if not held:
            if handoff is not None:
                _drop_note(ctx, handoff)
            raise Refused(words.HAND_BUSY.format(to=to))
        return _hand(ctx, pid, chat, handoff, task, job)


def _ask_hand(ctx: _Ctx, pid) -> str:
    ctx.out(words.HAND_QUESTION.format(to=ctx.name(pid), vendor=ctx.vendor(pid),
                                       work=words.WORK_PLAN if ctx.work_plan(pid) else '',
                                       basename=ctx.basename).rstrip('\n'))
    answer = ctx.ask(words.PROMPT).lower()
    return 'yes' if answer in ('y', 'yes') else 'never' if answer == 'never' else 'no'


def _hand(ctx: _Ctx, pid, chat, handoff: Optional[Handoff], task, job) -> int:
    tasks = ctx.kit.tasks
    to = ctx.name(pid)
    prompt = words.JOB_WORDS.format(folder=ctx.folder)
    if handoff is not None:
        hop_id = handoff.hop
        prompt += words.JOB_BACKGROUND.format(path=handoff.path)
    else:
        task = tasks.task_for(ctx.folder, pid, None)
        hop_id = tasks.new_hop_id()
    prompt += words.JOB_GIVEN.format(job=job)
    out_path = ctx.kit.handoff.answer_path(task, hop_id, ctx.reach)
    hop = tasks.add_hop(Hop(id=hop_id, task=task, kind='hand', at=ctx.now, folder=ctx.folder,
                            from_provider=chat.provider if chat else pid, from_chat=chat.id if chat else None,
                            to_provider=pid, handoff=handoff.path if handoff else None, state='made'))
    ctx.moved = True
    ctx.out(words.HAND_ON_IT.format(to=to))
    billing = _billing(ctx, pid)
    answer = _run_job(ctx, pid, prompt, out_path)
    if answer is None:
        tasks.update_hop(hop.id, state='failed')
        ctx.say(words.H_HAND_NONE.format(to=to, basename=ctx.basename, why='stopped'), 'info', to=to,
                reason='hand')
        ctx.out(words.HAND_STOPPED)
        return 130
    if answer.ok:
        tasks.update_hop(hop.id, state='answered')
        ctx.say(words.H_HANDED.format(to=to, basename=ctx.basename, took=words.took(answer.took_s)), 'info', to=to,
                reason='hand')
        ctx.out(words.HAND_ANSWER.format(to=to, took=words.took(answer.took_s), billing=billing))
        ctx.out('')
        ctx.out(words.safe(answer.text).rstrip('\n'))
        ctx.out('')
        ctx.out(words.HAND_KEPT.format(path=words.one_line(answer.path or out_path, 400),
                                       days=contract.ANSWER_KEEP_DAYS))
        return 0
    tasks.update_hop(hop.id, state='failed')
    # How it ended, by the adapter's own answer: an exit code; else it ran for a while (the time limit); else it
    # never started. The sentence printed is the adapter's own (it holds the exit and the last words), once.
    if answer.code is not None:
        why, own = 'exit %d' % answer.code, words.HAND_NO_ANSWER.format(to=to, code=answer.code)
    elif answer.took_s:
        why, own = 'time limit', words.HAND_TIMED_OUT.format(to=to)
    else:
        why, own = 'could not start', words.HAND_NOT_STARTED.format(to=to)
    ctx.say(words.H_HAND_NONE.format(to=to, basename=ctx.basename, why=why), 'info', to=to, reason='hand')
    raise Refused(words.one_line(answer.why, 400) if answer.why else own)


def _billing(ctx: _Ctx, pid) -> str:
    plan = ctx.plan_label(pid)
    lims = ctx.limits(pid)
    on_key = any(lim.ask == contract.ASK_API_KEY for lim in lims)
    if plan and not on_key and any(lim.plan for lim in lims):
        fmt = words.BILLING_PLAN_OTHER if plan.lower().endswith(' plan') else words.BILLING_PLAN
        return fmt.format(plan=plan)
    return words.BILLING_SIGN_IN.format(to=ctx.name(pid))


def _stop(signum, frame):
    raise KeyboardInterrupt


def _run_job(ctx: _Ctx, pid, prompt, out_path):
    """The job, with SIGTERM and SIGHUP (a closed terminal, an ended parent) stopping it as Ctrl-C does: the program
    runs in its own session, so without them it would outlive this command. None when it was stopped."""
    old = {}
    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            old[sig] = signal.signal(sig, _stop)
        except (ValueError, OSError):        # not the main thread: Ctrl-C still stops it
            pass
    try:
        answer = ctx.prov(pid).job(ctx.folder, prompt, out_path, words.HAND_TIMEOUT_S)
    except KeyboardInterrupt:
        return None
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)
    if isinstance(answer, Unsupported):
        raise Refused(words.one_line(answer.why, 300))
    return answer


# ---------- switchboard allow, stay, order ----------


def cmd_allow(ctx: _Ctx, args) -> int:
    if len(args) != 1:
        raise Usage()
    pid = ctx.provider_word(args[0])
    if not ctx.tty():
        raise Refused(words.ALLOW_NOT_TTY)
    ctx.must_be_added(pid)
    frm = next((p for p in ctx.order() if p != pid), pid)
    answer = _ask_consent(ctx, pid, frm, None)
    if answer == 'yes':
        ctx.kit.choices.allow(ctx.folder, pid, ctx.now)
        ctx.say(words.H_YES.format(basename=ctx.basename, to=ctx.name(pid)), 'setting', to=ctx.name(pid))
        ctx.out(words.ALLOWED.format(basename=ctx.basename, to=ctx.name(pid)))
        return 0
    if answer == 'never':
        _set_consent(ctx, pid, 'never')
        ctx.out(words.NEVER_SET.format(basename=ctx.basename, to=ctx.name(pid), to_id=pid))
        return 0
    raise Refused(words.NOTHING_CHANGED)


def cmd_stay(ctx: _Ctx, args) -> int:
    if len(args) != 1:
        raise Usage()
    if args[0] == 'off':
        ctx.kit.choices.set_stay(ctx.folder, None, ctx.now)
        ctx.say(words.H_STAY_OFF.format(basename=ctx.basename), 'setting')
        ctx.out(words.STAY_OFF)
        for q in ctx.order():
            if q in ctx.added() and ctx.consent(q) == 'never':
                ctx.out(words.STAY_STILL_NEVER.format(q=ctx.name(q), q_id=q))
        return 0
    pid = ctx.provider_word(args[0])
    ctx.must_be_added(pid)
    ctx.kit.choices.set_stay(ctx.folder, pid, ctx.now)
    ctx.say(words.H_STAY.format(basename=ctx.basename, p=ctx.name(pid)), 'setting', to=ctx.name(pid))
    ctx.out(words.STAY_ON.format(basename=ctx.basename, p=ctx.name(pid)))
    return 0


def cmd_order(ctx: _Ctx, args) -> int:
    if args:
        named = [ctx.provider_word(a) for a in args]
        if len(set(named)) != len(named):
            raise Usage(words.ORDER_REPEAT)
        for pid in named:
            ctx.must_be_added(pid)
        new = named + [p for p in ctx.order() if p not in named]
        ctx.kit.choices.set_order(new)
        ctx.say(words.H_ORDER.format(names=words.names([ctx.name(p) for p in new])), 'setting')
    ctx.out(words.ORDER_LINE.format(names=words.names([ctx.name(p) for p in ctx.order()])))
    return 0


_RUN: Dict[str, Callable[[_Ctx, list], int]] = {
    'providers': cmd_providers, 'add': cmd_add, 'continue': cmd_continue, 'back': cmd_back, 'hand': cmd_hand,
    'allow': cmd_allow, 'stay': cmd_stay, 'order': cmd_order,
}
