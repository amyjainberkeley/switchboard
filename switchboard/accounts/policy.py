"""decide(snapshot) -> Decision: forced and optional switches, refills-first or most room, our own rules.

Pure: no files, no clock, no network. Everything it needs is in the Snapshot the loop (or api.decide) gathers, and
the same snapshot always gives the same answer, so every rule below is tested by building snapshots.

The windows that count ("relevant"): the 5-hour window against its line (session_at, 90 by default), the weekly window
against its line (weekly_at, 95), and the weekly window of the model chosen in the settings (model_at, 95; e.g.
'model:Fable'). Spend never counts. A window an account does not have (an Ultra account has no 5-hour window) does
not count for it; a window it has but whose number is not known (never read, or refilled since it was read) is
"not known", never 0 and never the old number.

Room: for each relevant window, its line minus its percent; an account's room is the smallest of those (points left
before its nearest line; negative once over one).

Forced switch (goes at once, whatever Claude Code is doing): the active account is at or over a line on any relevant
window, its login was refused by the server (its saved login was refused, or Claude Code emptied the live one after a
refusal), or it is refused requests now. The target needs room of at least 5
points under every line (LAND_MARGIN) and numbers at most 15 minutes old. Among those: refills-first takes the account
whose weekly window refills soonest (ties: more room); most-room takes the most room (ties: refills sooner). A target
whose numbers are frozen by a rate limit (a lower bound) comes after every unfrozen one.
When no account has that room and the active one is truly blocked (a window at 100%, a refused login, refused
requests), an account with at least 5 points of raw room under 100 on every window is still better than none (never
a dead end); it is said plainly that it is past its line.
When Claude Code itself is stopped on the active account (its login refused or ended, or Claude Code said it is at
its limit) and no account read fresh is a landing place or an escape while others could not be read (Anthropic refusing
the reads, say), it goes by what was last known of those others rather than stay stopped (never one read fresh): an account not known to be over its line, the most room when last read, the
newest reading first among equals (_on_older_numbers), inside the rules below (never one kept out, one used last only
when no other qualifies, a lower rank first). The switch says it moved on older numbers; the loop reads every account
it could go to once before, and the account it lands on at once after.
When nothing has room: stay, and say when the first account refills (earliest_refill).

Optional switch (rebalancing; refills-first only): the target's weekly window refills at least an hour sooner than the
active one's (REFILL_GAIN) and it has at least 10 points of room under every line (OPTIONAL_ROOM). It waits ("held")
until all of these hold: Claude Code idle for 5 minutes, no open obligation (a Doc or artifact made, a watch running,
or a claude.ai connector used in the last 30 minutes, under the active account), both accounts' numbers at most 10
minutes old and not frozen, at least 45 minutes
since the last switch from any source, at most 2 optional switches in 6 hours, the Mac not under heavy load (4 per
core), not on a low battery (25%), and not just woken. A held switch is a Decision with action 'stay', its target and
kind, and the hold's plain reason, so the page can say "Next: x, when Claude Code is idle".

The 45-minute dwell and the one-hour refill gain also keep switching one-way: after moving from A to B because B
refills an hour sooner, a move back needs A to refill an hour sooner than B, which only a real window reset can bring.

Need switch (need()): something needs Claude Code on one account right now (Claude in Chrome follows the extension's
account). It goes only when that account has room under every line (LAND_MARGIN) on numbers at most 15 minutes old.

An account's rule (Settings.rules; none is 'in', the rest of this docstring):
- 'out', kept out of auto-switch: never the target of any switch here (forced, the escape, optional, need), and while
  Claude Code is on it every answer is stay, whatever its numbers. At or over a line, or refused, it says which, as a
  forced reason does, with kind 'forced' and hold 'kept out of auto-switch' (like "no account has room"), and that
  Claude Code stays on it. Its numbers are still read for the page; a person still moves to it or from it by hand.
  The sentences that count accounts (no other account, no room, every account out, the first refill) count only the
  accounts auto-switch may use, and name a kept-out account when it is why there is no landing place.
- 'last', used last: auto-switch goes there only when no other account has room. A forced switch looks, in order, for
  (1) a landing place that is not last, (2) a landing place that is last, then, only for an account truly blocked,
  (3) the escape to one that is not last and (4) the escape to one that is last; today's order holds within each. So a
  last account with room comes before running another past its line; one with no fresh numbers is read once before
  an escape is taken (unread_last; the loop reads it and decides again). An optional switch never goes to a last
  account. While Claude Code is on a last account under refills-first, an account
  that is not last with OPTIONAL_ROOM under every line (known numbers) is an optional switch with no refill-gain test,
  the best of them by today's ranking, under every hold. A need switch may go to a last account.

Finer rules, each unset by default (then every answer is the one above):
- An account's own lines (Settings.own): its 5-hour or weekly window, or both, judged against a line of its own in
  place of the shared one, everywhere the shared line counts for it: a forced switch off it, the margin for landing
  on it, the room an optional switch needs, a need switch. The model line stays shared.
- Ranks (Settings.ranks, 1 is used first): among the accounts a switch may land on, a lower number always wins, and
  the order chosen decides only inside one rank; an account with no rank comes after every ranked one. 'last' still
  comes after every account that is not last, whatever their ranks, and 'out' is still never used. An optional switch
  never goes to an account ranked after the one in use. The escape past a line takes no rank into account: the most raw room wins.
- The order 'stay-then-refills': no optional switch at all (Claude Code stays on an account until it reaches a line,
  as most-room does), and a forced one lands as refills-first does.
- Several models' weeks (Settings.models): each one counted against the model line.
- best(s): the landing a forced switch would pick now, for a move a person asked for (`switchboard switch --best`):
  the same accounts, rules, ranks, lines and fresh numbers, with none of the waits that guard a move nobody asked for.
"""
import math
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from . import api

FIVE, WEEK, MODEL = '5-hour', 'weekly', 'model:'
STRATEGIES = ('refills-first', 'most-room', 'stay-then-refills')
LINE_MIN, LINE_MAX = 50.0, 99.9
DEFAULTS = {'session_at': 90.0, 'weekly_at': 95.0, 'model_at': 95.0, 'model': None, 'strategy': 'refills-first'}

LAND_MARGIN = 5.0            # never land within this many points of a line
ESCAPE_ROOM = 5.0            # a truly blocked account may go to one with this much raw room under 100
FORCED_MAX_AGE = 900.0       # a forced or need switch may land on numbers up to 15 minutes old
OPTIONAL_MAX_AGE = 600.0     # an optional one needs both accounts' numbers at most 10 minutes old
IDLE_FOR = 300.0             # Claude Code is idle after 5 minutes with no turn
DWELL = 45 * 60.0            # at least this long since the last switch from any source
OPTIONAL_CAP = 2             # at most this many optional switches ...
OPTIONAL_CAP_WINDOW = 6 * 3600.0   # ... in this long
REFILL_GAIN = 3600.0         # the target's weekly window refills at least this much sooner
OPTIONAL_ROOM = 10.0         # and it has at least this much room under every line
LOAD_HIGH = 4.0              # load per core above this: no optional switch
BATTERY_LOW = 25.0           # on battery under this: no optional switch
EXHAUSTED = 100.0
OK_STATUSES = ('ok', 'expiring')
RULES = ('last', 'out')       # an account's rule; one with none ('in') is used as usual
KEPT_OUT = 'kept out of auto-switch'
OWN_KEYS = ('session_at', 'weekly_at')     # the lines an account may have its own of (the model line stays shared)
RANK_MIN, RANK_MAX = 1, 99


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    session_at: float = 90.0
    weekly_at: float = 95.0
    model_at: float = 95.0
    model: Optional[str] = None
    strategy: str = 'refills-first'
    rules: Tuple[Tuple[str, str], ...] = ()     # (account id, 'last' or 'out'), sorted by id: frozen and hashable
    own: Tuple[Tuple[str, Tuple[Tuple[str, float], ...]], ...] = ()   # (account id, ((OWN_KEYS key, line), ...))
    ranks: Tuple[Tuple[str, int], ...] = ()     # (account id, 1 to 99), sorted by id
    models: Tuple[str, ...] = ()                # every model week counted; () with `model` set: that one alone


def rules_from(d):
    """((account id, rule), ...) sorted, from a stored {id: 'last' | 'out'}: anything else is no rule."""
    if not isinstance(d, dict):
        return ()
    return tuple(sorted((k, v) for k, v in d.items() if isinstance(k, str) and k and v in RULES))


def rule_of(settings, account_id):
    """'last', 'out' or 'in' (no rule)."""
    for aid, rule in settings.rules:
        if aid == account_id:
            return rule
    return 'in'


def own_from(d):
    """((account id, ((key, line), ...)), ...) sorted, from a stored {id: {'session_at': N, 'weekly_at': N}}: a key
    that is not one of OWN_KEYS, or a line that is not a number from LINE_MIN to LINE_MAX, is not set."""
    if not isinstance(d, dict):
        return ()
    out = []
    for k, v in d.items():
        if not (isinstance(k, str) and k and isinstance(v, dict)):
            continue
        lines = tuple((key, float(v[key])) for key in OWN_KEYS if _line_ok(v.get(key)))
        if lines:
            out.append((k, lines))
    return tuple(sorted(out))


def _line_ok(v):
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(float(v)) \
        and LINE_MIN <= float(v) <= LINE_MAX


def ranks_from(d):
    """((account id, rank), ...) sorted, from a stored {id: N}: anything but a whole number from 1 to 99 is no rank."""
    if not isinstance(d, dict):
        return ()
    return tuple(sorted((k, v) for k, v in d.items() if isinstance(k, str) and k and isinstance(v, int)
                        and not isinstance(v, bool) and RANK_MIN <= v <= RANK_MAX))


def own_of(settings, account_id):
    """{key: line} of the lines this account has its own of ({} for none)."""
    for aid, lines in settings.own:
        if aid == account_id:
            return dict(lines)
    return {}


def rank_of(settings, account_id):
    """The account's rank, or None."""
    for aid, rank in settings.ranks:
        if aid == account_id:
            return rank
    return None


def models_from(value):
    """The model names a stored 'model' counts: one name, or a list of names (of two that name the same model, case
    and spacing aside, the first is kept)."""
    names = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    out = []
    for n in names:
        n = ' '.join(n.split()) if isinstance(n, str) else ''
        if n and n.lower() not in [m.lower() for m in out]:
            out.append(n)
    return tuple(out)


def model_list(settings):
    """Every model whose week counts: Settings.models, else the one in Settings.model, else none."""
    return settings.models or ((settings.model,) if settings.model else ())


@dataclass(frozen=True)
class Snapshot:
    """Everything one decision needs. Times: `now` is wall-clock (for Anthropic's reset times and reading ages);
    every duration (since_switch_s, idle_for_s) was measured by the caller on the monotonic clock."""
    now: float
    accounts: Tuple[api.Account, ...]
    readings: Dict[str, api.Reading]
    settings: Settings
    active: Optional[str] = None
    live_ok: bool = True                 # the live login was read and identified, so a switch may start
    live_why: Optional[str] = None       # why not, in plain words
    disagree: bool = False               # the config's label and the live login name different accounts
    busy: bool = False
    idle_for_s: Optional[float] = None
    obligations: Tuple[dict, ...] = ()   # the active account's open obligations: {'kind', 'url' or 'connector',
                                         # 'until', 'at'}
    since_switch_s: Optional[float] = None   # since the last switch from any source; None: none known
    optional_recent: int = 0             # optional switches in the last OPTIONAL_CAP_WINDOW
    load_per_core: Optional[float] = None
    battery_pct: Optional[float] = None
    on_ac: Optional[bool] = None
    settling: bool = False               # the Mac just woke (waiting for the network): no switch at all
    offline: bool = False                # the last pass could reach nothing: keep the status quo
    rate_limited: bool = False           # the active account is refused requests now
    claimed: frozenset = frozenset()     # accounts a run-as is using
    no_optional: Optional[str] = None    # optional switches are off this pass, and why
    live_refused: bool = False           # Claude Code emptied the active account's login (Anthropic refused it)
    left_forced: Tuple[Tuple[str, float], ...] = ()     # (account id, wall time a forced switch last left it)


@dataclass(frozen=True)
class View:
    """One account seen through the relevant windows."""
    id: str
    label: str
    order: int
    known: bool                          # at least one relevant window, every one with a number
    pcts: Dict[str, Optional[float]] = field(default_factory=dict)
    resets: Dict[str, Optional[float]] = field(default_factory=dict)
    lines: Dict[str, float] = field(default_factory=dict)
    room: Optional[float] = None         # points before the nearest line (negative once over)
    raw_room: Optional[float] = None     # 100 minus the highest relevant percent
    over: Tuple[Tuple[str, float, float], ...] = ()   # (window, pct, line) at or over their line
    age: Optional[float] = None          # age of the oldest relevant number
    frozen: bool = False                 # a relevant number is held back by a rate limit (a lower bound)
    weekly_reset: Optional[float] = None
    rank: Optional[int] = None           # its rank (Settings.ranks), or None
    own: Tuple[str, ...] = ()            # the windows judged against a line of its own (Settings.own)


# ---------- settings ----------

def settings_from(d):
    """Settings from a stored dict, forgivingly: a missing or bad value takes its default, a line is clamped, and a
    rule that is not 'last' or 'out' (or rules that are not an object) is no rule."""
    d = d if isinstance(d, dict) else {}

    def line(key):
        v = d.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
            return DEFAULTS[key]
        return min(LINE_MAX, max(LINE_MIN, float(v)))
    models = models_from(d.get('model'))
    return Settings(enabled=d.get('enabled') is True, session_at=line('session_at'), weekly_at=line('weekly_at'),
                    model_at=line('model_at'), model=models[0] if models else None,
                    strategy=d.get('strategy') if d.get('strategy') in STRATEGIES else DEFAULTS['strategy'],
                    rules=rules_from(d.get('rules')), own=own_from(d.get('own_lines')),
                    ranks=ranks_from(d.get('ranks')), models=models if len(models) > 1 else ())


def lines_for(settings, account_id=None):
    """{window name: its switch line} for the windows that count: for `account_id`, its own lines where it has them."""
    own = own_of(settings, account_id) if account_id is not None else {}
    out = {FIVE: own.get('session_at', settings.session_at), WEEK: own.get('weekly_at', settings.weekly_at)}
    for m in model_list(settings):
        out[MODEL + m] = settings.model_at
    return out


def same_model(a, b):
    """Two model names name the same model: equal (case and spacing aside), or one is the other followed by a
    version ('Fable' and 'Fable 5.2'). 'Opus 4' and 'Opus 5' are not the same."""
    a, b = ' '.join(str(a).lower().split()), ' '.join(str(b).lower().split())
    return a == b or a.startswith(b + ' ') or b.startswith(a + ' ')


def window_line(lines, name):
    """The switch line a window is judged against, or None when it does not count (a model nobody chose)."""
    if name in lines:
        return lines[name]
    if name.startswith(MODEL):
        for key, line in lines.items():
            if key.startswith(MODEL) and same_model(key[len(MODEL):], name[len(MODEL):]):
                return line
    return None


_line_for = window_line


# ---------- one account through the windows that count ----------

def view(account, reading, settings, now, label=None):
    lines = lines_for(settings, account.id)
    own = own_of(settings, account.id)
    pcts, resets, wl, over, ages = {}, {}, {}, [], []
    frozen = False
    for w in (reading.windows if reading else ()):
        line = _line_for(lines, w.name)
        if line is None:
            continue
        name = w.name
        at = w.read_at if w.read_at is not None else reading.read_at
        future = at is not None and at > now + 5          # read "after now": a clock moved back; never believed
        pct = None if future else w.pct
        pcts[name], resets[name], wl[name] = pct, w.resets_at, line
        ages.append(None if at is None or future else max(0.0, now - at))
        if reading.frozen and (w.source or reading.source) != 'status_line':
            frozen = True
        if pct is not None and pct >= line:
            over.append((name, pct, line))
    known = bool(pcts) and all(p is not None for p in pcts.values()) and all(a is not None for a in ages)
    room = min(wl[n] - pcts[n] for n in pcts) if known else None
    raw = 100.0 - max(pcts.values()) if known else None
    weekly = resets.get(WEEK)
    return View(id=account.id, label=label or account.email or account.alias or account.id, order=account.order,
                known=known,
                pcts=pcts, resets=resets, lines=wl, room=room, raw_room=raw, over=tuple(over),
                age=max(ages) if known else None, frozen=frozen,
                weekly_reset=weekly if weekly is not None and weekly > now else None, rank=rank_of(settings, account.id),
                own=tuple(n for n, k in ((FIVE, 'session_at'), (WEEK, 'weekly_at')) if k in own and n in wl))


def label_of(s, account):
    """An account's name in the snapshot's words: its email, or, when another account in the snapshot has the same
    email, its email and organization (names.of_account), as every other surface names it."""
    email = (account.email or '').lower()
    if not email or sum(1 for x in s.accounts if (x.email or '').lower() == email) < 2:
        return account.email or account.alias or account.id
    from .. import names
    return names.of_account(account, s.accounts)


def _view(s, account):
    """view() of one of the snapshot's accounts, with its name (label_of)."""
    return view(account, s.readings.get(account.id), s.settings, s.now, label=label_of(s, account))


def _pct(v):
    return ('%.1f' % v).rstrip('0').rstrip('.')


def _window_words(name):
    return name[len(MODEL):] + ' weekly' if name.startswith(MODEL) else name


def _over_words(over, own=()):
    """'5-hour at 92% (line 90%)'; a window judged against the account's own line (`own`): '(its own line, 85%)'."""
    name, pct, line = over
    return '%s at %s%% (%s%s%%)' % (_window_words(name), _pct(pct), 'its own line, ' if name in own else 'line ',
                                     _pct(line))


def _blocked(v, s, account):
    """The active account cannot work at all: a window at 100%, a refused login, or refused requests."""
    return (account is not None and account.status == 'needs_login') or s.rate_limited or s.live_refused or \
        any(p is not None and p >= EXHAUSTED for p in v.pcts.values())


def _eligible(a, s):
    return a.id != s.active and a.status in OK_STATUSES and a.id not in s.claimed


def _refill_key(t):
    return math.inf if t is None else int(t // 60)


def _rank_key(v):
    """A lower rank first, then no rank: with no rank anywhere every account has the same key."""
    return math.inf if v.rank is None else v.rank


def _rank(views, strategy):
    if strategy == 'most-room':
        return sorted(views, key=lambda v: (_rank_key(v), v.frozen, -v.room, _refill_key(v.weekly_reset), v.order))
    return sorted(views, key=lambda v: (_rank_key(v), v.frozen, _refill_key(v.weekly_reset), -v.room, v.order))


def _clock(t, now):
    from .usage import clock_text
    return clock_text(t, now)


def _span(seconds):
    from .usage import span_text
    return span_text(seconds)


# ---------- when the first account refills ----------

def refill_of(v, now):
    """When a blocked account gets room again: the latest reset among its windows at or over their line (it is usable
    only when every one has reset). None when it is not blocked or a blocking window's reset is not known."""
    if not v.over:
        return None
    times = [v.resets.get(name) for name, _, _ in v.over]
    if any(t is None or t <= now for t in times):
        return None
    return max(times)


def earliest_refill(s):
    """(time, account label, window words) of the first refill among accounts that are out, the active one included,
    or None when no refill time is known. An account kept out of auto-switch is not counted (unless it is the active
    one)."""
    best = None
    for a in s.accounts:
        if a.status not in OK_STATUSES or (a.id != s.active and rule_of(s.settings, a.id) == 'out'):
            continue
        v = _view(s, a)
        t = refill_of(v, s.now)
        if t is None:
            continue
        name = max(v.over, key=lambda o: v.resets.get(o[0]) or 0)[0]
        if best is None or t < best[0]:
            best = (t, v.label, _window_words(name))
    return best


# ---------- the decision ----------

def stay(reason, **kw):
    return api.Decision('stay', reason=reason, **kw)


def decide(s):
    """The policy's answer for one snapshot: a Decision (see the module's docstring for every rule)."""
    if not s.settings.enabled:
        return stay('Automatic switching is off.')
    if not s.live_ok:
        return stay('No switch now: %s.' % (s.live_why or "Claude Code's login can't be read right now"))
    by_id = dict((a.id, a) for a in s.accounts)
    if not s.active or s.active not in by_id:
        return stay("Claude Code is not on a saved account, so there is nothing to switch from.")
    if s.settling:
        return stay('The Mac just woke; waiting for the network before any switch.')
    if s.offline:
        return stay('No connection to Anthropic; Claude Code stays where it is.')
    active = by_id[s.active]
    av = _view(s, active)

    forced = None
    if active.status == 'needs_login' or s.live_refused:
        forced = "%s's login was refused" % av.label
    elif s.rate_limited:
        forced = '%s is refusing requests now' % av.label
    elif av.over:
        forced = '%s %s' % (av.label, _over_words(av.over[0], av.own))
    if rule_of(s.settings, s.active) == 'out':
        if forced:
            return stay('%s. It is kept out of auto-switch, so Claude Code stays on it. Move by hand when you want '
                        'to.' % forced, kind='forced', hold=KEPT_OUT)
        return stay('%s is kept out of auto-switch, so Claude Code stays on it.' % av.label)
    eligible = [a for a in s.accounts if _eligible(a, s)]
    cands = [_view(s, a) for a in eligible
             if rule_of(s.settings, a.id) != 'out']
    if forced:
        kept = [_view(s, a) for a in eligible
                if rule_of(s.settings, a.id) == 'out']
        return _forced(s, active, av, cands, forced, kept)
    return _optional(s, av, cands)


def _fresh(views):
    return [v for v in views if v.known and v.age is not None and v.age <= FORCED_MAX_AGE]


def _names(labels):
    """'a', 'a and b', 'a, b and c'."""
    labels = list(labels)
    return labels[0] if len(labels) == 1 else '%s and %s' % (', '.join(labels[:-1]), labels[-1])


def _forced(s, active, av, cands, why, kept=()):
    last = set(v.id for v in cands if rule_of(s.settings, v.id) == 'last')
    fresh = _fresh(cands)
    blocked = _blocked(av, s, active)
    # 1. a landing place that is not used last; 2. one that is; 3. only for an account truly blocked, the escape past a
    # line to one that is not used last; 4. to one that is. With no account used last, this is the rule as it was.
    for group in ([v for v in fresh if v.id not in last], [v for v in fresh if v.id in last]):
        landing = [v for v in group if v.room >= LAND_MARGIN]
        if landing:
            t = _rank(landing, s.settings.strategy)[0]
            return api.Decision('switch', target=t.id, kind='forced', reason='%s; %s%s has room (%s)'
                                % (why, t.label, _tags(t, landing, last), _room_words(t, s.settings.strategy, s.now)))
    if blocked:
        for group in ([v for v in fresh if v.id not in last], [v for v in fresh if v.id in last]):
            escape = [v for v in group if v.raw_room is not None and v.raw_room >= ESCAPE_ROOM]
            if escape:
                t = sorted(escape, key=lambda v: (v.frozen, -v.raw_room, v.order))[0]
                return api.Decision('switch', target=t.id, kind='forced',
                                    reason='%s; no account is under its lines, but %s%s can still work (%s)'
                                    % (why, t.label, ', used last,' if t.id in last else '',
                                       ', '.join('%s %s%%' % (_window_words(n), _pct(p)) for n, p in t.pcts.items())))
    held = [v.label for v in _fresh(kept) if v.room >= LAND_MARGIN or (blocked and v.raw_room >= ESCAPE_ROOM)]
    also = ' (%s %s kept out of auto-switch)' % (_names(held), 'is' if len(held) == 1 else 'are') if held else ''
    fresh_ids = set(v.id for v in fresh)
    unknown = [v for v in cands if v.id not in fresh_ids]
    if not cands:
        return stay('%s, and there is no other account to switch to%s.' % (why, also), kind='forced',
                    hold='no other account')
    if unknown:
        # no account read fresh is a landing place or an escape (both were tried above): the ones that could not be
        # read are judged by what was last known of them, never one read fresh
        older = _on_older_numbers(s, active, unknown, why, last, fresh=fresh)
        if older is not None:
            return older
        return stay('%s; no other account has fresh numbers with room yet (reading %s)%s.'
                    % (why, ', '.join(v.label for v in unknown), also), kind='forced', hold='no fresh numbers')
    first = earliest_refill(s)
    back = ' Back at %s (%s, %s).' % (_clock(first[0], s.now), first[1], first[2]) if first else \
        ' No refill time is known yet.'
    if blocked:
        return stay('Every account is out%s.%s' % (also, back), kind='forced', hold='every account is out')
    return stay('%s; no other account has room%s, so it stays.%s'
                % (why, also, back.replace('Back at', 'First refill:')), kind='forced', hold='no account has room')


def _tags(t, landing, last):
    """', used last,' and ', rank 1,' after a landing's name: its rank only when the rank decided it (another landing
    place had a later rank, or none), so the words with every account ranked alike are the words with no ranks."""
    tags = (['used last'] if t.id in last else []) + \
        (['rank %d' % t.rank] if t.rank is not None and any(_rank_key(v) > t.rank for v in landing) else [])
    return ', %s,' % ', '.join(tags) if tags else ''


def _stopped(s, active):
    """Claude Code cannot work on the account in use, by its own word or Anthropic's: the login was refused or has
    ended, or Claude Code said it is at its limit. Numbers alone never count here: they may be old too."""
    return active.status == 'needs_login' or s.live_refused or s.rate_limited


def _last_known(s, v):
    """(points before the nearest line, seconds since the newest number) from whatever was last read of a candidate,
    each None when nothing of it is known."""
    room = [v.lines[n] - p for n, p in v.pcts.items() if p is not None]
    r = s.readings.get(v.id)
    times = [w.read_at if w.read_at is not None else r.read_at for w in (r.windows if r else ())]
    times = [t for t in times if t is not None and t <= s.now + 5]
    return (min(room) if room else None), (max(0.0, s.now - max(times)) if times else None)


def _on_older_numbers(s, active, cands, why, last=frozenset(), fresh=()):
    """A forced switch when Claude Code is stopped (_stopped) and no account read fresh is a landing place or an
    escape: the best one by what was last known, rather than staying stopped. `cands` are the accounts a forced switch
    may use that could not be read fresh (never one kept out of auto-switch, never one read fresh); `last`, the ids of
    those used last; `fresh`, the accounts read fresh, none of which fits (the reason names them). Not known to be over its line; an account
    that is not used last before one that is; then a lower rank first (no rank after every rank); then the most room
    when last read, the newest reading first among equal room, an account never read after every one that was. Never
    an account a forced switch left before its newest number was read (_left_before_read). None when nothing qualifies
    (it then waits for fresh numbers, as ever)."""
    if not _stopped(s, active):
        return None
    pick = [v for v in cands if not v.over and not _left_before_read(s, v)]
    group = [v for v in pick if v.id not in last] or [v for v in pick if v.id in last]
    if not group:
        return None
    known = dict((v.id, _last_known(s, v)) for v in group)
    t = sorted(group, key=lambda v: (_rank_key(v), known[v.id][0] is None, -round(known[v.id][0] or 0.0, 1),
                                     math.inf if known[v.id][1] is None else known[v.id][1], v.order))[0]
    room, age = known[t.id]
    name = '%s%s' % (t.label, _tags(t, group, last))
    said = ("%s's numbers are not known yet" % name if room is None else
            '%s had %s points before its nearest line %s ago' % (name, _pct(room), _span(age or 0)))
    if fresh:
        full = '%s %s no room' % (_names([v.label for v in fresh]), 'has' if len(fresh) == 1 else 'have')
        return api.Decision('switch', target=t.id, kind='forced',
                            reason="%s; %s, and no other account's numbers could be read just now, so it moved on "
                                   "older numbers: %s" % (why, full, said))
    return api.Decision('switch', target=t.id, kind='forced',
                        reason="%s; no account's numbers could be read just now, so it moved on older numbers: %s"
                        % (why, said))


def _left_before_read(s, v):
    """A forced switch left this account after its newest number was read (s.left_forced): what is known of it
    predates the moment Claude Code was stopped on it, so it is no landing place on older numbers until it is read
    again. Two accounts both at their limit would otherwise trade Claude Code at every limit notice."""
    left = dict(s.left_forced).get(v.id)
    if left is None:
        return False
    age = _last_known(s, v)[1]
    return age is None or s.now - age < left


def on_older_numbers(decision, s):
    """A forced switch whose target's numbers are not fresh enough to land on (_on_older_numbers chose it): the
    account it lands on is read at once after the move."""
    if decision.action != 'switch' or decision.kind != 'forced':
        return False
    a = dict((x.id, x) for x in s.accounts).get(decision.target)
    if a is None:
        return False
    v = view(a, s.readings.get(a.id), s.settings, s.now)
    return not (v.known and v.age is not None and v.age <= FORCED_MAX_AGE)


def _room_words(v, strategy, now):
    room = '%s points before its nearest line' % _pct(v.room)
    if strategy != 'most-room' and v.weekly_reset:
        return '%s, weekly refills %s' % (room, _clock(v.weekly_reset, now))
    return room


def _optional(s, av, cands):
    if s.settings.strategy != 'refills-first':
        return stay('%s is under its lines.' % av.label)
    if not av.known:
        return stay("%s's numbers are not known yet; nothing to compare." % av.label)
    cands = [v for v in cands if rule_of(s.settings, v.id) != 'last']      # never an optional switch to a last one
    if rule_of(s.settings, av.id) == 'last':
        return _off_last(s, av, cands)
    up = [v for v in cands if _rank_key(v) < _rank_key(av) and v.known and v.room >= max(OPTIONAL_ROOM, LAND_MARGIN)]
    if up:
        return _up_rank(s, av, up)
    cands = [v for v in cands if _rank_key(v) == _rank_key(av)]       # never to an account ranked after this one
    if av.weekly_reset is None:
        return stay("%s's weekly refill time is not known, so it can't tell which account refills first." % av.label)
    pick = [v for v in cands
            if v.known and v.room >= max(OPTIONAL_ROOM, LAND_MARGIN) and v.weekly_reset is not None
            and v.weekly_reset <= av.weekly_reset - REFILL_GAIN]
    if not pick:
        return stay('%s is under its lines and refills first among the accounts with room.' % av.label)
    ready = [v for v in pick if v.age is not None and v.age <= OPTIONAL_MAX_AGE and not v.frozen]
    t = (_rank(ready, 'refills-first') or _rank(pick, 'refills-first'))[0]
    reason = '%s refills first (weekly %s, against %s for %s), %s points before its nearest line' % (
        t.label, _clock(t.weekly_reset, s.now), _clock(av.weekly_reset, s.now), av.label, _pct(t.room))
    hold = _hold(s, av, t)
    if hold:
        return api.Decision('stay', target=t.id, kind='optional', reason='Next: %s; waits: %s.' % (t.label, hold),
                            hold=hold)
    return api.Decision('switch', target=t.id, kind='optional', reason=reason)


def _off_last(s, av, cands):
    """Claude Code is on an account used last: an early move to the best account with room that is not last (no
    refill-gain test), under every hold."""
    pick = [v for v in cands if v.known and v.room >= max(OPTIONAL_ROOM, LAND_MARGIN)]
    if not pick:
        return stay('%s is used last, and no account used before it has room now.' % av.label)
    ready = [v for v in pick if v.age is not None and v.age <= OPTIONAL_MAX_AGE and not v.frozen]
    t = (_rank(ready, 'refills-first') or _rank(pick, 'refills-first'))[0]
    hold = _hold(s, av, t)
    if hold:
        return api.Decision('stay', target=t.id, kind='optional', reason='Next: %s; waits: %s.' % (t.label, hold),
                            hold=hold)
    return api.Decision('switch', target=t.id, kind='optional',
                        reason='%s is used last, and %s has room (%s points before its nearest line)'
                        % (av.label, t.label, _pct(t.room)))


def _up_rank(s, av, up):
    """An account ranked before the one in use has room under every line (OPTIONAL_ROOM, known numbers): an early move
    to the best of them, as a move off an account used last is (no refill-gain test), under every hold."""
    top = min(_rank_key(v) for v in up)
    up = [v for v in up if _rank_key(v) == top]                         # the earliest rank first, ready or not
    ready = [v for v in up if v.age is not None and v.age <= OPTIONAL_MAX_AGE and not v.frozen]
    t = (_rank(ready, 'refills-first') or _rank(up, 'refills-first'))[0]
    hold = _hold(s, av, t)
    if hold:
        return api.Decision('stay', target=t.id, kind='optional', reason='Next: %s; waits: %s.' % (t.label, hold),
                            hold=hold)
    return api.Decision('switch', target=t.id, kind='optional',
                        reason='%s is %s, and %s, rank %d, has room (%s points before its nearest line)'
                        % (av.label, 'rank %d' % av.rank if av.rank is not None else 'not ranked', t.label, t.rank,
                           _pct(t.room)))


def _hold(s, av, t):
    """Why an optional switch must wait, in plain words, or None. The first that applies, in a fixed order."""
    if s.no_optional:
        return s.no_optional
    if s.disagree:
        return "Claude Code's account label and its login disagree"
    for ob in s.obligations:
        return _obligation_words(ob, av.label, s.now)
    if s.busy:
        return 'Claude Code is working'
    if s.idle_for_s is not None and s.idle_for_s < IDLE_FOR:
        return 'Claude Code was working %s ago (it waits for 5 quiet minutes)' % _span(s.idle_for_s)
    for v in (av, t):
        if v.frozen:
            return "%s's numbers are held back by a rate limit" % v.label
        if v.age is None or v.age > OPTIONAL_MAX_AGE:
            return "%s's numbers are %s old (it needs them under 10 minutes)" % (v.label, _span(v.age or 0))
    if s.since_switch_s is not None and s.since_switch_s < DWELL:
        return 'the last switch was %s ago; it stays at least 45 minutes, until %s' % (
            _span(s.since_switch_s), _clock(s.now + DWELL - s.since_switch_s, s.now))
    if s.optional_recent >= OPTIONAL_CAP:
        return '%d optional switches in the last 6 hours already' % s.optional_recent
    if s.load_per_core is not None and s.load_per_core > LOAD_HIGH:
        return 'the Mac is very busy (load %.0f per core)' % s.load_per_core
    if s.on_ac is False and s.battery_pct is not None and s.battery_pct < BATTERY_LOW:
        return 'the battery is at %d%%' % s.battery_pct
    return None


def _obligation_words(ob, label, now):
    kind = ob.get('kind')
    at = ob.get('at')
    when = ' at %s' % _clock(at, now) if isinstance(at, (int, float)) else ''
    if kind == 'doc':
        return 'a Claude Doc made as %s%s is still open' % (label, when)
    if kind == 'watch':
        return 'a chat is watching an artifact as %s' % label
    if kind == 'connector':
        name = ob.get('connector')
        name = ''.join(c for c in name if c >= ' ' and c != '\x7f')[:60].strip() if isinstance(name, str) else ''
        return 'a chat used %s as %s%s' % (name or 'a claude.ai connector', label, when)
    return 'an artifact was published as %s%s' % (label, when)


# ---------- the best switch, asked for by hand ----------

def best(s):
    """`switchboard switch --best`: the account a forced switch would land on now, as a Decision with kind 'manual'.
    The same accounts as a forced switch (signed in, not claimed by a run, never one kept out of auto-switch), the
    same rules ('last' only when no other account has room), ranks, lines, landing margin and fresh numbers. None of
    the waits that guard a move nobody asked for (idle, time since the last switch, the cap), and it answers with
    auto-switch off and from an account kept out of it, since a person asked. Never the escape past a line. When there
    is none, a 'stay' that says why and when the first account refills."""
    cands = [_view(s, a) for a in s.accounts
             if _eligible(a, s) and rule_of(s.settings, a.id) != 'out']
    last = set(v.id for v in cands if rule_of(s.settings, v.id) == 'last')
    fresh = _fresh(cands)
    for group in ([v for v in fresh if v.id not in last], [v for v in fresh if v.id in last]):
        landing = [v for v in group if v.room >= LAND_MARGIN]
        if landing:
            t = _rank(landing, s.settings.strategy)[0]
            return api.Decision('switch', target=t.id, kind='manual', reason='%s%s has room (%s)' % (
                t.label, _tags(t, landing, last), _room_words(t, s.settings.strategy, s.now)))
    if not cands:
        return stay('There is no other account to switch to.', kind='manual', hold='no other account')
    fresh_ids = set(v.id for v in fresh)
    unknown = [v for v in cands if v.id not in fresh_ids]
    if unknown:
        return stay('No other account has fresh numbers with room: %s %s not read in the last 15 minutes.'
                    % (_names(v.label for v in unknown), 'was' if len(unknown) == 1 else 'were'), kind='manual',
                    hold='no fresh numbers')
    first = earliest_refill(s)
    back = ' First refill: %s (%s, %s).' % (_clock(first[0], s.now), first[1], first[2]) if first else \
        ' No refill time is known yet.'
    return stay('No other account has room.%s' % back, kind='manual', hold='no account has room')


# ---------- a switch something needs ----------

def need(s, account_id, why=''):
    """A need switch (see the module's docstring): a Decision with kind 'need'. Never to or from an account kept out
    of auto-switch; it may go to one used last."""
    if not s.settings.enabled:
        return stay('Automatic switching is off.', kind='need')
    if not s.live_ok:
        return stay('No switch now: %s.' % (s.live_why or "Claude Code's login can't be read right now"),
                    kind='need')
    if s.settling:
        return stay('The Mac just woke; waiting for the network.', kind='need')
    if s.offline:
        return stay('No connection to Anthropic.', kind='need')
    by_id = dict((a.id, a) for a in s.accounts)
    a = by_id.get(account_id)
    if a is None:
        return stay('That account is not saved in Switchboard.', kind='need')
    if account_id == s.active:
        return stay('Claude Code is already on %s.' % label_of(s, a), target=account_id, kind='need')
    on = by_id.get(s.active)
    if on is not None and rule_of(s.settings, on.id) == 'out':
        return stay('%s is kept out of auto-switch, so Claude Code stays on it.' % label_of(s, on),
                    target=account_id, kind='need')
    if rule_of(s.settings, account_id) == 'out':
        return stay('%s is kept out of auto-switch, so Claude Code is not moved there.' % label_of(s, a),
                    target=account_id, kind='need')
    if not _eligible(a, s):
        return stay('%s is not usable right now (%s).' % (label_of(s, a), a.status), target=account_id, kind='need')
    v = _view(s, a)
    if not v.known or v.age is None or v.age > FORCED_MAX_AGE:
        return stay('%s has no fresh numbers.' % v.label, target=account_id, kind='need')
    if v.room < LAND_MARGIN:
        worst = v.over[0] if v.over else min(((n, v.pcts[n], v.lines[n]) for n in v.pcts),
                                             key=lambda o: o[2] - o[1])
        return stay('%s has no room (%s).' % (v.label, _over_words(worst, v.own)), target=account_id, kind='need')
    return api.Decision('switch', target=account_id, kind='need',
                        reason='%s%s; it has room (%s points before its nearest line)'
                        % ('needed: ' if why else '', why or 'needed', _pct(v.room)))


def unread_candidates(s):
    """Accounts a forced switch could go to whose numbers are missing or older than FORCED_MAX_AGE: read them now
    rather than at their next planned read. Never one kept out of auto-switch."""
    out = []
    for a in s.accounts:
        if _eligible(a, s) and rule_of(s.settings, a.id) != 'out':
            v = _view(s, a)
            if not v.known or v.age is None or v.age > FORCED_MAX_AGE:
                out.append(a.id)
    return out


def unread_last(s, d):
    """When `d` is the move past a line (the escape): the accounts used last that a forced switch could land on but
    that have no fresh numbers. One of them with room comes before the escape, so they are read once before it is
    taken, and the decision made again (a read that fails leaves the escape as it was). Otherwise none."""
    if d.action != 'switch' or d.kind != 'forced' or not any(r == 'last' for _, r in s.settings.rules):
        return []
    t = dict((a.id, a) for a in s.accounts).get(d.target)
    v = _view(s, t) if t is not None else None
    if v is None or (v.room is not None and v.room >= LAND_MARGIN):
        return []                                   # a landing, not the escape
    return [x for x in unread_candidates(s) if rule_of(s.settings, x) == 'last']


# ---------- what a switch says ----------

def active_blocked(s):
    """The active account cannot work at all now (a window at 100%, a refused login, refused requests)."""
    a = dict((x.id, x) for x in s.accounts).get(s.active)
    return a is not None and _blocked(_view(s, a), s, a)


def switch_note(decision, snapshot):
    """The sentence a switch carries to the history and the notification. When the account left was truly blocked
    (a window at 100%, refused requests or a refused login) a reply may have stopped: it says to send the message
    again. It never offers to send anything itself."""
    note = decision.reason
    if decision.kind != 'forced':
        return note
    if active_blocked(snapshot):
        target = dict((a.id, a) for a in snapshot.accounts).get(decision.target)
        to = label_of(snapshot, target) if target else decision.target
        note = '%s. Switched to %s. If a reply stopped, send your message again.' % (note, to)
    return note


def summary(views):
    """For logs: 'account 2: 5-hour 41%, weekly 63%' (numbers only, never an email)."""
    out = []
    for v in views:
        parts = ', '.join('%s %s%%' % (_window_words(n), '?' if p is None else _pct(p)) for n, p in v.pcts.items())
        out.append('account %d: %s' % (v.order, parts or 'no numbers'))
    return '; '.join(out)


def views_of(s):
    return [_view(s, a) for a in s.accounts]
