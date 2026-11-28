"""decide(snapshot) -> Decision: forced and optional switches, refills-first or most room, our own rules.

Pure: no files, no clock, no network. Everything it needs is in the Snapshot the loop (or api.decide) gathers, and
the same snapshot always gives the same answer, so every rule below is tested by building snapshots.

The windows that count for an account ("relevant"): the 5-hour window against its line (session_at, 90 by default)
and the weekly window against its line (weekly_at, 95). Spend never counts. A window an account does not have (an
Ultra account has no 5-hour window) does not count for it; a window it has but whose number is not known (never read,
or refilled since it was read) is "not known", never 0 and never the old number.

Model families. A model's own weekly window ('model:Fable') belongs to the family whose word its name holds (fable,
opus, sonnet, haiku: FAMILIES, top of the ladder first). It counts for "can this account serve family F" against
model_at (95), never for "can this account work at all": that is unusable_by_windows(), the 5-hour and weekly windows
alone, the one answer decide(), plan.py, autocheck.py and the providers' board use. The families that count are the ones the chats ask for
(Snapshot.asked, which the loop gathers), plus the family of the setting `model` when it names one; with nothing
known yet (asked None), only the setting's family decides anything (deciding_families), and the model weeks the
accounts report name families for the words and the poller's reads only. Room for a family F on an account: its
5-hour and weekly windows under their lines AND F's own window (when it has one) under model_at, none of it refused in
the last 10 minutes (Snapshot.refused), each by the margin the rule names, on numbers at most 15 minutes old.

Sign lines (setting sign_lines, off unless the person turns it on: every extra switch makes each open chat read its
whole conversation again uncached; with it off nothing below about sign lines runs): Claude Code shows a usage sign
in a chat from 70% of the 5-hour window, the weekly window or the Fable week of the account in use. So every window
has a second, lower line: sign_session_at (60), sign_weekly_at (65), sign_model_at (65). A target under every sign
line that counts by SIGN_MARGIN comes before any target that is only under its lines, in every rule. The sign lines
decide where Claude Code is, never which model answers.

Room: for each relevant window, its line minus its percent; an account's room is the smallest of those (points left
before its nearest line; negative once over one).

Forced switch (whatever Claude Code is doing; it waits only for a calm Mac, below): the active account is at or
over a line on its 5-hour or weekly window, its login was refused by the server (its saved login was refused, or
Claude Code emptied the live one after a refusal), or it is refused requests now. The target needs room of at least 5
points under every line (LAND_MARGIN) and numbers at most 15 minutes old. Among those, in groups: the ones under every
sign line, then the ones with room for every family the chats ask for, then the rest (an account with no room for a
family can still work on the next model). Inside a group: refills-first takes the account whose weekly window refills
soonest (ties: more room); most-room takes the most room (ties: refills sooner). A target whose numbers are frozen by
a rate limit (a lower bound) comes after every unfrozen one, and an account whose sign-in is in its last 3 days
(SIGNIN_WINDOW) comes after every other.
When no account has that room and the active one is truly blocked (a window at 100%, a refused login, refused
requests), an account with at least 5 points of raw room under 100 on its 5-hour and weekly windows is still better
than none (never a dead end); it is said plainly that it is past its line.
When Claude Code itself is stopped on the active account (its login refused or ended, or Claude Code said it is at
its limit) and no account read fresh is a landing place or an escape while others could not be read (Anthropic refusing
the reads, say), it goes by what was last known of those others rather than stay stopped (never one read fresh): an
account not known to be over its line, the most room when last read, the newest reading first among equals
(_on_older_numbers), inside the rules below (never one kept out, one used last only when no other qualifies, a lower
rank first). The switch says it moved on older numbers; the loop reads every account it could go to once before, and
the account it lands on at once after.
When nothing has room: stay, and say when the first account refills (earliest_refill). "Every account is out" is
said only when no account has room on its 5-hour and weekly windows.

Model switch (kind 'model'): the active account works but has no room for a family F the chats ask for (F's window at
or over model_at, or F refused there in the last 10 minutes), and another account has room for every asked family
with LAND_MARGIN (MODEL_ROOM under its 5-hour and weekly lines, so that a forced move back does not follow), on fresh
numbers (one used last only when no other has that room). When the account in use lacks F only by a refusal (no
number shows it at its line) it waits MODEL_REFUSED_DWELL since the last switch from any source: two accounts that
both refuse would otherwise trade places each time a refusal lapses (a number at its line cannot flap: a target needs
LAND_MARGIN under model_at). Like an optional switch it waits while optional switches are off this pass (a model
switch for at most CALM_MODEL_MAX_S), while a Doc or artifact made as the active account is open, and on a low
battery; at most MODEL_CAP of them go in OPTIONAL_CAP_WINDOW (Snapshot.model_recent); it does not wait for Claude
Code to be idle. With the chats' models not known yet (asked None) it is made only for the families of the models the
setting names (deciding_families). When no account has room for F: no switch for F's sake; the chats run on the next
model with room until F refills, and the words say until when.

Sign switch (kind 'sign'): nothing is over a line, the active account is at or over a sign line that counts, and
another account is under every sign line that counts by SIGN_MARGIN, on fresh numbers (never one used last or ranked
after the one in use). It waits at least SIGN_DWELL since the last switch from any source, and like an optional switch
while optional switches are off this pass, while a Doc or artifact made as the active account is open, on a low
battery, over LOAD_HIGH, and once OPTIONAL_CAP optional and sign switches together went in OPTIONAL_CAP_WINDOW
(Snapshot.sign_recent counts toward that cap for optional switches too); it does not wait for Claude Code to be idle.

Calm first: every automatic switch waits until the Mac's 1-minute load per core is under CALM_LOAD, for at most
CALM_FORCED_S for a forced switch or a move that keeps Claude Code signed in, whatever the percent (a 5-hour window
runs from its line to 100 in minutes under heavy work), and not at all when the account in use cannot work at all (a
window at 100%, a refused login, refused requests, a sign-in lost or ended: every request of every chat is refused
while it waits); model, sign and optional switches wait for CALM_LOAD up to
CALM_PATIENT_S, then take a load under LOAD_HIGH; a model switch goes whatever the load once it has been wanted
CALM_MODEL_MAX_S (the person's model is the model), or CALM_FORCED_S when a family it is for is one the setting
`model` names; sign and optional switches never go over LOAD_HIGH. Snapshot.waited says how long the loop has wanted
a switch of each kind.

A pick of the person's own (Snapshot.pick_age_s: seconds since they moved Claude Code onto the account in use from the
page, the menu or a terminal) holds against optional switches for PICK_HOLD_OPTIONAL_S and against model and sign
switches for PICK_HOLD_MOVE_S (setting pick_hold: 'default'; 'until_out' holds it until a forced switch; 'off').

A sign-in in its last 3 days: when the account in use's stored sign-in end is within SIGNIN_WINDOW, another signed-in
account not itself in its last 3 days with room for every asked family is moved to as an optional switch (never one
used last or ranked after the one in use; a family no account has room for is left out: it runs on the next model
everywhere alike).

Optional switch (rebalancing; refills-first only): the target's weekly window refills at least an hour sooner than the
active one's (REFILL_GAIN) and it has at least 10 points of room under every line (OPTIONAL_ROOM). It waits ("held")
until all of these hold: Claude Code idle for 5 minutes, no open obligation (a Doc or artifact made, a watch running,
or a claude.ai connector used in the last 30 minutes, under the active account), both accounts' numbers at most 10
minutes old and not frozen, at least 45 minutes since the last switch from any source, at most 2 optional and sign
switches in 6 hours, the Mac not under heavy load (4 per core), not on a low battery (25%), and not just woken. A
held switch is a Decision with action 'stay', its target and kind, and the hold's plain reason, so the page can say
"Next: x, when Claude Code is idle".

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
- Several models named (Settings.models): the family of each one counts as asked for (see Model families).
- best(s): the landing a forced switch would pick now, for a move a person asked for (`switchboard switch --best`):
  the same accounts, rules, ranks, lines and fresh numbers, with none of the waits that guard a move nobody asked for.

Keeping Claude Code signed in (stay_why(), its own setting, on by default; it works with automatic switching on and
off, and it is the only switch made while automatic switching is off): Claude Code moves to another signed-in account
when the sign-in in use is gone and its saved login can't be put back (`lost`), or when the sign-in in use stops
working within LEAVE_AHEAD_S (`ends_at`, see ends_at()). The targets and their ranking are the forced switch's: never
an account kept out of auto-switch, one used last only when no other account has room (a landing place first, then
the move past a line), a lower rank first, each account judged against its own lines. An account whose own sign-in
ends within ENDS_SOON_S is never one either, nor an account left for a lost sign-in that was not signed in again
since; and when no target has fresh numbers even after the loop read them (candidates_read), the signed-in account
whose last numbers had the most room goes (one that is not used last first, a lower rank first), however old, and it
is said that they are old: any signed-in account is better than one that can't work. It moves Claude Code off an
account kept out of auto-switch too, since a sign-in that can't work is no place to stay, and says so; when every
other signed-in account is kept out, Claude Code stays and that is said.
Its last step, onto old numbers, is taken only while Claude Code can't work at all (its
sign-in is lost or has ended) and only onto an account this pass tried to read and could not (Snapshot.read_failed):
no move lands on numbers older than FORCED_MAX_AGE otherwise.
Which of the two speaks: with automatic switching on and Anthropic's refusal of the sign-in in use (the account needs a
sign-in, or Claude Code emptied its login), the forced switch decides first, over the accounts this move may use and
after its brake (_refused_first), so it moves on older numbers and says so as it always has; this move decides whenever
the forced switch would not switch, and in every case the forced switch does not see (a login lost that the put-back
can't bring back, a sign-in about to end, automatic switching off, an account kept out of auto-switch). The History
line is the forced switch's own words when it decided, this move's otherwise (switch_note); the loop's notification
and brake are this move's either way.
"""
import math
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from . import api, facts
from ..term import SIGNIN_WINDOW

FIVE, WEEK, MODEL = '5-hour', 'weekly', 'model:'
STRATEGIES = ('refills-first', 'most-room', 'stay-then-refills')
LINE_MIN, LINE_MAX = 50.0, 99.9
DEFAULTS = {'session_at': 90.0, 'weekly_at': 95.0, 'model_at': 95.0, 'model': None, 'strategy': 'refills-first',
            'sign_session_at': 60.0, 'sign_weekly_at': 65.0, 'sign_model_at': 65.0}
FAMILIES = ('fable', 'opus', 'sonnet', 'haiku')       # the step-down ladder, top first
FAMILY_NAMES = {'fable': 'Fable', 'opus': 'Opus 5.5', 'sonnet': 'Sonnet 5.5', 'haiku': 'Haiku'}
PICK_HOLDS = ('default', 'until_out', 'off')

LAND_MARGIN = 5.0            # never land within this many points of a line
ESCAPE_ROOM = 5.0            # a truly blocked account may go to one with this much raw room under 100
FORCED_MAX_AGE = 900.0       # a forced or need switch may land on numbers up to 15 minutes old
OPTIONAL_MAX_AGE = 600.0     # an optional one needs both accounts' numbers at most 10 minutes old
IDLE_FOR = 300.0             # Claude Code is idle after 5 minutes with no turn
DWELL = 45 * 60.0            # at least this long since the last switch from any source
OPTIONAL_CAP = 2             # at most this many optional switches ...
OPTIONAL_CAP_WINDOW = 6 * 3600.0   # ... in this long (sign switches count toward it too)
MODEL_CAP = 4                # at most this many model switches in OPTIONAL_CAP_WINDOW
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
# An older build's word for an order, read (and taken when set) as the order it always meant. None now: a stored
# word this build does not know reads as the default order (settings_from), which is what the older word meant.
ORDER_ALIASES = {}
# Claude Code stores a sign-in's end (refreshTokenExpiresAt) as min(old, now + seconds left) at each renewal, so one
# sign-in's end moves down by up to seconds: the same slack as facts.same_sign_in.
END_SLACK_S = facts.SAME_SIGN_IN_MS / 1000.0
LEAVE_AHEAD_S = 600.0        # a sign-in that stops working is left this long before it does
ENDS_SOON_S = LEAVE_AHEAD_S + 3600.0   # an account whose own sign-in ends within this is no target for such a move
NOTE_ROOM = 240              # notify.py cuts a notification's body past this many characters
HISTORY_ROOM = 300           # history.py cuts a switch's reason past this many characters (history.REASON_MAX)
SIGN_MARGIN = 5.0            # a target counts as under its sign lines by this many points
SIGN_DWELL = 20 * 60.0       # a sign switch waits this long since the last switch from any source
REFUSED_FRESH = 600.0        # a request refused this recently means the family has no room on that account
CALM_LOAD = 2.0              # an automatic switch waits for a load per core under this ...
CALM_FORCED_S = 120.0        # ... at most this long for a forced switch, whatever the percent (a 5-hour window can run
                             # from its line to 100 in minutes with many chats working)
CALM_PATIENT_S = 1800.0      # model, sign and optional switches: after this long, a load under LOAD_HIGH will do
CALM_MODEL_MAX_S = 3600.0    # and a model switch goes whatever the load once it has been wanted this long
MODEL_ROOM = max(OPTIONAL_ROOM, LAND_MARGIN)   # a model switch's target has this much room under its 5-hour and weekly
MODEL_REFUSED_DWELL = 3600.0  # a model switch the account in use lacks only by a refusal: this long since the last
                              # switch (two accounts that both refuse would trade places as each refusal lapses)
PICK_HOLD_OPTIONAL_S = 12 * 3600.0   # a pick of the person's own holds this long against an optional switch
PICK_HOLD_MOVE_S = 2 * 3600.0        # and this long against a model or sign switch


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    session_at: float = 90.0
    weekly_at: float = 95.0
    model_at: float = 95.0
    model: Optional[str] = None          # also count this family as asked for, even when no chat asks for it now
    strategy: str = 'refills-first'
    rules: Tuple[Tuple[str, str], ...] = ()     # (account id, 'last' or 'out'), sorted by id: frozen and hashable
    own: Tuple[Tuple[str, Tuple[Tuple[str, float], ...]], ...] = ()   # (account id, ((OWN_KEYS key, line), ...))
    ranks: Tuple[Tuple[str, int], ...] = ()     # (account id, 1 to 99), sorted by id
    models: Tuple[str, ...] = ()                # several models named; () with `model` set: that one alone
    stay_signed_in: bool = True          # move Claude Code off a sign-in that is lost or ends (stay_why)
    pick_hold: str = 'default'           # PICK_HOLDS
    sign_lines: bool = False             # off unless the person turned it on: each extra switch re-reads every chat
    sign_session_at: float = 60.0
    sign_weekly_at: float = 65.0
    sign_model_at: float = 65.0


def order_word(word):
    """The order a word names: an older build's word (ORDER_ALIASES) as the order it always meant, any other as it is."""
    return ORDER_ALIASES.get(word, word) if isinstance(word, str) else word


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
    sign_recent: int = 0                 # sign switches in the last OPTIONAL_CAP_WINDOW
    model_recent: int = 0                # model switches in the last OPTIONAL_CAP_WINDOW
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
    lost: Optional[str] = None           # the sign-in in use is gone and its saved login can't be put back: why, in
                                         # words that follow "its saved login" ('was refused by Anthropic'); or
                                         # LIVE_REFUSED: Claude Code emptied its login after Anthropic refused it
    ends_at: Optional[float] = None      # wall time the sign-in in use stops working (ends_at()), None: it renews
    candidates_read: bool = False        # this pass already read the accounts a forced switch could go to
    left: frozenset = frozenset()        # accounts left for a lost sign-in and not signed in again since
    moved_recently: frozenset = frozenset()   # accounts Claude Code was just moved off to keep it signed in
    asked: Optional[frozenset] = None    # the model families the chats ask for now; None: not known
    refused: Dict[str, frozenset] = field(default_factory=dict)   # account id -> families refused there now
    waited: Dict[str, float] = field(default_factory=dict)        # kind -> seconds the loop has wanted that switch
    pick_age_s: Optional[float] = None   # since the person picked the account in use; None: the move was not theirs
    read_failed: Optional[frozenset] = None   # accounts this pass tried to read and could not; None: not told
    keep_running: bool = False           # Keep running is on (running/on.json): a request for a model with no room
                                         # goes out as the next model; off, such a chat stops until it refills


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
    signs: Dict[str, float] = field(default_factory=dict)          # the sign line of each relevant window
    sign_room: Optional[float] = None    # points before the nearest sign line (negative once over)
    fam: Dict[str, Optional[float]] = field(default_factory=dict)  # family -> its own window's percent
    fam_resets: Dict[str, Optional[float]] = field(default_factory=dict)
    fam_ages: Dict[str, Optional[float]] = field(default_factory=dict)
    refused: frozenset = frozenset()     # families refused on this account in the last 10 minutes
    ends: Optional[float] = None         # the stored end of the account's sign-in (wall seconds)


# ---------- settings ----------

def settings_from(d):
    """Settings from a stored dict, forgivingly: a missing or bad value takes its default, a line is clamped, and a
    rule that is not 'last' or 'out' (or rules that are not an object) is no rule. An older build's word for an order
    (ORDER_ALIASES) reads as the order it names. Keeping Claude Code signed in is on unless it is stored as False."""
    d = d if isinstance(d, dict) else {}

    def line(key):
        v = d.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
            return DEFAULTS[key]
        return min(LINE_MAX, max(LINE_MIN, float(v)))
    models = models_from(d.get('model'))
    strategy = order_word(d.get('strategy'))
    return Settings(enabled=d.get('enabled') is True, session_at=line('session_at'), weekly_at=line('weekly_at'),
                    model_at=line('model_at'), model=models[0] if models else None,
                    strategy=strategy if strategy in STRATEGIES else DEFAULTS['strategy'],
                    rules=rules_from(d.get('rules')), own=own_from(d.get('own_lines')),
                    ranks=ranks_from(d.get('ranks')), models=models if len(models) > 1 else (),
                    stay_signed_in=d.get('stay_signed_in') is not False,
                    pick_hold=d.get('pick_hold') if d.get('pick_hold') in PICK_HOLDS else 'default',
                    sign_lines=d.get('sign_lines') is True, sign_session_at=line('sign_session_at'),
                    sign_weekly_at=line('sign_weekly_at'), sign_model_at=line('sign_model_at'))


def family_of(name):
    """The model family a model id or a window's name belongs to ('claude-fable-5-2', 'model:Fable 5' -> 'fable'),
    or None when it holds none of the family words."""
    if not isinstance(name, str):
        return None
    low = name.lower()
    for f in FAMILIES:
        if f in low:
            return f
    return None


def family_words(f):
    return FAMILY_NAMES.get(f, str(f).capitalize())


def asked_families(s):
    """The families to name, top of the ladder first: the chats' (Snapshot.asked), or with nothing known the
    families of the model weeks the accounts report; plus the family of each model the setting
    `model` names. For the words and the
    poller's reads; the rules decide on deciding_families."""
    fams = set()
    if s.asked is not None:
        fams.update(f for f in s.asked if f in FAMILIES)
    else:
        for r in s.readings.values():
            for w in getattr(r, 'windows', ()) or ():
                if isinstance(w.name, str) and w.name.startswith(MODEL) and family_of(w.name):
                    fams.add(family_of(w.name))
    for m in model_list(s.settings):
        if family_of(m):
            fams.add(family_of(m))
    return tuple(f for f in FAMILIES if f in fams)


def window_families(readings):
    """The families that have a weekly window of their own on some reading ({account id: api.Reading})."""
    out = set()
    for r in (readings or {}).values():
        for w in getattr(r, 'windows', ()) or ():
            if isinstance(w.name, str) and w.name.startswith(MODEL) and family_of(w.name):
                out.add(family_of(w.name))
    return frozenset(out)


def read_lines(settings, families=(), account_id=None, seen=None):
    """{window name: line} for the poller (usage.Context.lines): the 5-hour and weekly windows (for `account_id`, its
    own lines where it has them) and the windows of the families that count, at their sign lines when those are on
    (never above the account's own line), so that an account is "near" (read every 90 s while saved reads last) from
    15 points under them. seen (window_families): when given, only a family with a window of its own on some account
    adds one, so that a family with none never stops the free status-line reads."""
    hard = lines_for(settings, account_id)
    if settings.sign_lines:
        out = {FIVE: min(settings.sign_session_at, hard[FIVE]), WEEK: min(settings.sign_weekly_at, hard[WEEK])}
        model = settings.sign_model_at
    else:
        out = {FIVE: hard[FIVE], WEEK: hard[WEEK]}
        model = settings.model_at
    for f in families:
        if seen is None or f in seen:
            out[MODEL + f] = model
    for m in model_list(settings):
        out[MODEL + m] = model
    return out


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

def _at_line(pct, line):
    """A window's number is at or over its line (a number that is not known never is)."""
    return pct is not None and line is not None and pct >= line


def unusable_by_windows(now, five_hour_pct=None, five_hour_resets_at=None, weekly_pct=None, weekly_resets_at=None,
                        session_line=90.0, weekly_line=95.0):
    """(unusable, back_at) for one account, by its own two windows only: unusable is True when its 5-hour window or
    its weekly window is at or over its line (pass the account's own line where it has one, else the shared one);
    back_at is when every window at or over its line has reset (the latest of their resets, epoch seconds), None
    when the account is usable or a reset of a window that holds it is not known or already passed. A percent that
    is None (not known, or a window the account does not have) never makes it unusable.

    A model's own week (the Fable week, say) is never part of this: it says whether the account can serve that model,
    never whether the account can work. Pure: `now` is passed in; nothing is read. This is the one answer decide(),
    plan.py and autocheck.py use, so every caller agrees with the loop by construction."""
    held = [r for pct, r, line in ((five_hour_pct, five_hour_resets_at, session_line),
                                   (weekly_pct, weekly_resets_at, weekly_line)) if _at_line(pct, line)]
    if not held:
        return False, None
    if any(r is None or r <= now for r in held):
        return True, None
    return True, max(held)


def view_unusable(v, now):
    """unusable_by_windows() for a View (its own lines included)."""
    return unusable_by_windows(now, v.pcts.get(FIVE), v.resets.get(FIVE), v.pcts.get(WEEK), v.resets.get(WEEK),
                               v.lines.get(FIVE), v.lines.get(WEEK))


def view(account, reading, settings, now, label=None, refused=frozenset()):
    """One account through its 5-hour and weekly windows (room, sign room, age), each judged against the account's own
    line where it has one, and each family's own window apart (fam, fam_resets, fam_ages). label: its name (label_of);
    refused: the families refused there in the last 10 minutes."""
    hard = lines_for(settings, account.id)
    own = own_of(settings, account.id)
    lines = {FIVE: hard[FIVE], WEEK: hard[WEEK]}
    sign_lines = {FIVE: settings.sign_session_at, WEEK: settings.sign_weekly_at}
    pcts, resets, wl, over, ages = {}, {}, {}, [], []
    fam, fam_resets, fam_ages = {}, {}, {}
    frozen = False
    for w in (reading.windows if reading else ()):
        name = w.name
        at = w.read_at if w.read_at is not None else reading.read_at
        future = at is not None and at > now + 5          # read "after now": a clock moved back; never believed
        pct = None if future else w.pct
        age = None if at is None or future else max(0.0, now - at)
        if isinstance(name, str) and name.startswith(MODEL):
            f = family_of(name)
            # two windows of one family (two versions): the fuller one counts
            if f is not None and (f not in fam or (pct is not None and (fam[f] is None or pct > fam[f]))):
                fam[f], fam_resets[f], fam_ages[f] = pct, w.resets_at, age
            continue
        line = lines.get(name)
        if line is None:
            continue
        pcts[name], resets[name], wl[name] = pct, w.resets_at, line
        ages.append(age)
        if reading.frozen and (w.source or reading.source) != 'status_line':
            frozen = True
        if _at_line(pct, line):
            over.append((name, pct, line))
    known = bool(pcts) and all(p is not None for p in pcts.values()) and all(a is not None for a in ages)
    room = min(wl[n] - pcts[n] for n in pcts) if known else None
    sign_room = min(sign_lines[n] - pcts[n] for n in pcts) if known else None
    raw = 100.0 - max(pcts.values()) if known else None
    weekly = resets.get(WEEK)
    return View(id=account.id, label=label or account.email or account.alias or account.id, order=account.order,
                known=known,
                pcts=pcts, resets=resets, lines=wl, room=room, raw_room=raw, over=tuple(over),
                age=max(ages) if known else None, frozen=frozen,
                weekly_reset=weekly if weekly is not None and weekly > now else None, rank=rank_of(settings, account.id),
                own=tuple(n for n, k in ((FIVE, 'session_at'), (WEEK, 'weekly_at')) if k in own and n in wl),
                signs=dict((n, sign_lines[n]) for n in pcts), sign_room=sign_room, fam=fam, fam_resets=fam_resets,
                fam_ages=fam_ages, refused=frozenset(refused or ()), ends=_when(account.refresh_expires_at))


def label_of(s, account):
    """An account's name in the snapshot's words: its email, or, when another account in the snapshot has the same
    email, its email and organization (names.of_account), as every other surface names it."""
    email = (account.email or '').lower()
    if not email or sum(1 for x in s.accounts if (x.email or '').lower() == email) < 2:
        return account.email or account.alias or account.id
    from .. import names
    return names.of_account(account, s.accounts)


def _view(s, account):
    """view() of one of the snapshot's accounts, with its name (label_of) and the families refused there."""
    return view(account, s.readings.get(account.id), s.settings, s.now, label=label_of(s, account),
                refused=(s.refused or {}).get(account.id, frozenset()))


view_of = _view

def fam_left(v, f, settings, sign=False):
    """Points before family f's own line on this account (its sign line when sign): inf when it has no window of its
    own, -inf when f was refused there, None when its number is not known."""
    if f in v.refused:
        return -math.inf
    if f not in v.fam:
        return math.inf
    p = v.fam[f]
    if p is None:
        return None
    return (settings.sign_model_at if sign else settings.model_at) - p


def serves(v, fams, settings, margin=LAND_MARGIN, sign=False, max_age=FORCED_MAX_AGE):
    """The account has room on its 5-hour and weekly windows and for every family in fams, `margin` points under each
    line (each sign line with sign), on numbers at most max_age old (None: any age)."""
    if not v.known or v.age is None or (max_age is not None and v.age > max_age):
        return False
    room = v.sign_room if sign else v.room
    if room is None or room < margin:
        return False
    for f in fams:
        left = fam_left(v, f, settings, sign)
        if left is None or left < margin:
            return False
        if f in v.fam and max_age is not None and math.isfinite(left):
            age = v.fam_ages.get(f)
            if age is None or age > max_age:
                return False
    return True


def lacks(v, fams, settings):
    """The families in fams the account in use can't serve now: refused there in the last 10 minutes, or their own
    window at or over model_at. A number that is not known counts as room (fail open)."""
    out = []
    for f in fams:
        left = fam_left(v, f, settings)
        if left is not None and left <= 0:
            out.append(f)
    return out


def sign_over(v, fams, settings):
    """[(window words, percent)] of the windows that count at or over their sign line, fullest first: the 5-hour and
    weekly windows, and the own windows of the families in fams. Empty with sign lines off."""
    if not settings.sign_lines:
        return []
    out = [(n, p) for n, p in v.pcts.items() if p is not None and p >= v.signs.get(n, 101.0)]
    out += [(MODEL + f, v.fam[f]) for f in fams if v.fam.get(f) is not None and v.fam[f] >= settings.sign_model_at]
    return sorted(out, key=lambda x: -x[1])


def last_days(v, now):
    """The account's sign-in is in its last SIGNIN_WINDOW before its stored end (Anthropic can end it any time then)."""
    return v.ends is not None and v.ends - now <= SIGNIN_WINDOW


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
    # an account left for a lost sign-in (stay_why) is not gone back to by any switch until it is signed in again
    return a.id != s.active and a.status in OK_STATUSES and a.id not in s.claimed and \
        not (s.settings.stay_signed_in and a.id in s.left)


def _refill_key(t):
    return math.inf if t is None else int(t // 60)


def _rank_key(v):
    """A lower rank first, then no rank: with no rank anywhere every account has the same key."""
    return math.inf if v.rank is None else v.rank


def _rank(views, strategy, s=None, fams=()):
    """Targets best first. With the snapshot: an account in its sign-in's last days last of all; then a lower rank
    first; frozen numbers after the rest; then the ones under every sign line (sign lines on), then the ones with room
    for every asked family; inside each group the strategy's order."""
    def key(v):
        if strategy == 'most-room':
            own = (-v.room, _refill_key(v.weekly_reset), v.order)
        else:
            own = (_refill_key(v.weekly_reset), -v.room, v.order)
        if s is None:
            return (_rank_key(v), v.frozen) + own
        return (last_days(v, s.now), _rank_key(v), v.frozen,
                s.settings.sign_lines and not serves(v, fams, s.settings, SIGN_MARGIN, sign=True),
                not serves(v, fams, s.settings)) + own
    return sorted(views, key=key)


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
    return view_unusable(v, now)[1]


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
    why = stay_why(s)
    if why is not None:
        first = _refused_first(s)
        return first if first is not None else _stay_signed_in(s, why)
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
    fams = deciding_families(s)

    forced = None
    if active.status == 'needs_login' or s.live_refused:
        forced = "%s's login was refused" % av.label
    elif s.rate_limited:
        forced = '%s is refusing requests now' % av.label
    elif view_unusable(av, s.now)[0]:
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
        return _forced(s, active, av, cands, forced, kept, fams)
    d = _model(s, av, cands, fams)
    if d is not None:
        # no account has room for an asked family: a sign-in in its last days still moves (G5), on the rest
        return d if d.target or d.hold else (_ending(s, av, cands, fams) or d)
    for rule in (_sign, _ending):
        d = rule(s, av, cands, fams)
        if d is not None:
            return d
    d = _optional(s, av, cands, fams)
    if d.action == 'stay' and not d.target and not d.kind:
        note = _only_here_words(s, av, cands, fams)
        if note:
            d = stay(d.reason + note)
    return d


def _only_here_words(s, av, cands, fams):
    """' No other account has room for Fable until Sun 2:00 PM (first: b@x).' when the account in use has room for an
    asked family and no other account has (a note: where Claude Code goes when the one in use reaches a line is
    decided by the 5-hour and weekly windows alone), else ''."""
    for f in fams:
        if lacks(av, (f,), s.settings) or not cands or any(serves(v, (f,), s.settings) or _could_serve(v, (f,), s.settings)
                                                                     for v in cands):
            continue
        firsts = sorted((t, v.label) for v in cands for t in (room_back(v, f, s.settings, s.now),) if t is not None)
        return ' No other account has room for %s %s.' % (
            family_words(f), 'until %s (first: %s)' % (_clock(firsts[0][0], s.now), firsts[0][1]) if firsts else 'now')
    return ''


def _fresh(views):
    return [v for v in views if v.known and v.age is not None and v.age <= FORCED_MAX_AGE]


def _names(labels):
    """'a', 'a and b', 'a, b and c'."""
    labels = list(labels)
    return labels[0] if len(labels) == 1 else '%s and %s' % (', '.join(labels[:-1]), labels[-1])


# ---------- waits: a calm Mac, the person's own pick ----------

def _load_words(load):
    return ('%.1f' % load).rstrip('0').rstrip('.') if load < 5 else '%.0f' % load


def calm_hold(s, kind, cap=None):
    """Why an automatic switch of this kind waits for the Mac to calm down, or None (see the module's docstring).
    cap: the most a forced switch waits; None for the patient kinds. A model switch waits CALM_MODEL_MAX_S at most;
    sign and optional switches never go over LOAD_HIGH. A load that is not known never holds."""
    load = s.load_per_core
    if load is None or load < CALM_LOAD:
        return None
    waited = (s.waited or {}).get(kind) or 0.0
    if cap is None and kind == 'model':
        cap = CALM_MODEL_MAX_S
        if waited >= CALM_PATIENT_S and load < LOAD_HIGH:
            return None
    if cap is not None:
        if waited >= cap:
            return None
        return 'waits for the Mac to calm down (load %s per core), %s at most' % (_load_words(load),
                                                                                 _span(cap - waited))
    if waited >= CALM_PATIENT_S and load < LOAD_HIGH:
        return None
    return 'waits for the Mac to calm down (load %s per core)' % _load_words(load)


def pick_hold(s, av, kind):
    """(the sentence, the hold) when the person's own pick of the account in use holds against this kind of
    switch, else None."""
    age = s.pick_age_s
    if age is None or age < 0 or s.settings.pick_hold == 'off':
        return None
    span = math.inf if s.settings.pick_hold == 'until_out' else \
        (PICK_HOLD_OPTIONAL_S if kind == 'optional' else PICK_HOLD_MOVE_S)
    if age >= span:
        return None
    picked = s.now - age
    said = 'You picked %s at %s.' % (av.label, _clock(picked, s.now))
    until = 'until a limit makes it move' if math.isinf(span) else 'until %s' % _clock(picked + span, s.now)
    return said, 'you picked %s; it stays %s' % (av.label, until)


def _held(s, t, kind, reason, hold):
    return api.Decision('stay', target=t.id, kind=kind, reason='%s Next: %s; it %s.' % (reason, t.label, hold)
                        if hold.startswith('waits') else '%s Next: %s; waits: %s.' % (reason, t.label, hold),
                        hold=hold)


def _calm(s, t, kind, reason, cap):
    """A forced switch to t, or the same switch waiting for a calm Mac (calm_hold, at most `cap`)."""
    hold = calm_hold(s, kind, cap)
    if hold:
        return _held(s, t, kind, _sentence(reason), hold)
    return api.Decision('switch', target=t.id, kind=kind, reason=reason)


def _sentence(text):
    text = text.strip()
    return text if text.endswith('.') else text + '.'


def _forced(s, active, av, cands, why, kept=(), fams=()):
    last = set(v.id for v in cands if rule_of(s.settings, v.id) == 'last')
    fresh = _fresh(cands)
    blocked = _blocked(av, s, active)
    # an account that cannot work at all refuses every request of every chat while a move waits: it never waits
    cap = 0.0 if blocked else CALM_FORCED_S
    # 1. a landing place that is not used last; 2. one that is; 3. only for an account truly blocked, the escape past a
    # line to one that is not used last; 4. to one that is. With no account used last, this is the rule as it was.
    for group in ([v for v in fresh if v.id not in last], [v for v in fresh if v.id in last]):
        landing = [v for v in group if v.room >= LAND_MARGIN]
        if landing:
            t = _rank(landing, s.settings.strategy, s, fams)[0]
            reason = '%s; %s%s has room (%s)%s' % (why, t.label, _tags(t, landing, last),
                                                   _room_words(t, s.settings.strategy, s.now),
                                                   _next_model_words(t, fams, s))
            return _calm(s, t, 'forced', reason, cap)
    if blocked:
        for group in ([v for v in fresh if v.id not in last], [v for v in fresh if v.id in last]):
            escape = [v for v in group if v.raw_room is not None and v.raw_room >= ESCAPE_ROOM]
            if escape:
                t = sorted(escape, key=lambda v: (last_days(v, s.now), v.frozen, -v.raw_room, v.order))[0]
                reason = '%s; no account is under its lines, but %s%s can still work (%s)' % (
                    why, t.label, ', used last,' if t.id in last else '',
                    ', '.join('%s %s%%' % (_window_words(n), _pct(p)) for n, p in t.pcts.items()))
                return _calm(s, t, 'forced', reason, cap)
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
            hold = calm_hold(s, 'forced', cap)
            return _held(s, dict((v.id, v) for v in unknown)[older.target], 'forced', _sentence(older.reason),
                         hold) if hold else older
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
    if s.read_failed is not None:           # told which could not be read just now: only those (None: not told)
        cands = [v for v in cands if v.id in s.read_failed]
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


# ---------- the model the chats ask for ----------

def step_down(v, f):
    """The first family below f on the ladder that this account can still serve (not refused there, its own window
    under 100), or None."""
    for g in FAMILIES[FAMILIES.index(f) + 1:] if f in FAMILIES else ():
        if g in v.refused:
            continue
        p = v.fam.get(g)
        if p is None or p < EXHAUSTED:
            return g
    return None


def _next_model_words(t, fams, s):
    """A short clause for a forced landing that can't serve an asked family, '' when it can serve them all. Under
    100% the family still works there: ' (Fable is at 98% there, line 95%: chats stay on it until it is used up)'.
    Used up or refused there: with Keep running on ' (no room for Fable there: chats run on Opus 5.5)', off
    ' (no room for Fable there until Mon 1:00 PM)'. Kept short: History keeps 300 characters of a switch's words, and
    a blocked account's switch ends with "send your message again" (switch_note)."""
    missing = lacks(t, fams, s.settings)
    if not missing:
        return ''
    f = missing[0]
    p = t.fam.get(f)
    if f not in t.refused and p is not None and p < EXHAUSTED:
        return ' (%s is at %s%% there, line %s%%: chats stay on it until it is used up)' % (
            family_words(f), _pct(p), _pct(s.settings.model_at))
    if not s.keep_running:
        back = room_back(t, f, s.settings, s.now)
        return ' (no room for %s there%s)' % (family_words(f), ' until %s' % _clock(back, s.now) if back else '')
    g = step_down(t, f)
    return ' (no room for %s there%s)' % (family_words(f), ': chats run on %s' % family_words(g) if g else '')


def _stop_words(f, until_then):
    """What a chat that asks for f does while no account it can use has room for f and Keep running is off."""
    return 'chats that ask for %s stop there%s; choose another model in the chat to go on.' % (
        family_words(f), ' until then' if until_then else '')


def _used_words(v, f, settings):
    """'Fable is used up on a@x (100%).', or 'Fable is at 96% on a@x (line 95%).'"""
    p = v.fam.get(f)
    if f in v.refused and (p is None or p < EXHAUSTED):
        return '%s is used up on %s (a request for it was refused).' % (family_words(f), v.label)
    if p is not None and p < EXHAUSTED:
        return '%s is at %s%% on %s (line %s%%).' % (family_words(f), _pct(p), v.label, _pct(settings.model_at))
    return '%s is used up on %s (%s%%).' % (family_words(f), v.label, _pct(p if p is not None else EXHAUSTED))


def _fam_pct_words(v, f):
    p = v.fam.get(f)
    return 'no limit of its own' if f not in v.fam else '%s%%' % _pct(p) if p is not None else 'not known'


def _could_serve(v, fams, settings):
    """An account whose numbers are old or not known, but whose last numbers do not say it can't serve fams: read it
    before saying no account can."""
    if serves(v, fams, settings):
        return False
    if v.known and v.room is not None and v.room < LAND_MARGIN:
        return False
    for f in fams:
        left = fam_left(v, f, settings)
        if left is not None and left < LAND_MARGIN:
            return False
    return True


def room_back(v, f, settings, now):
    """When family f has room on this account again: the later of its own window's reset (when that is at or over
    its line) and the account's own refill (when a 5-hour or weekly window is), or None when not known (a refusal
    says no time)."""
    times = []
    if f in v.refused:
        return None
    p = v.fam.get(f)
    if p is not None and p >= settings.model_at:
        times.append(v.fam_resets.get(f))
    if v.over:
        times.append(refill_of(v, now))
    if not times or any(t is None or t <= now for t in times):
        return None
    return max(times)


def _move_families(s, fams):
    """The asked families a rule may decide on: with the chats' models not known yet (asked None: no look at the
    chats has run, as at a start under load), only the families of the models the setting names."""
    if s.asked is not None:
        return fams
    own = set(family_of(m) for m in model_list(s.settings))      # each model the setting names
    return tuple(f for f in fams if f in own)


def named_families(settings):
    """The families of the models the setting `model` names."""
    return frozenset(f for f in (family_of(m) for m in model_list(settings)) if f)


def deciding_families(s):
    """The families every rule decides on (targets, holds, moves): asked_families, but while the chats' models are
    not known yet only the setting `model`'s family. The reported model weeks name families for the words and the
    poller's reads only: a model nobody chose never decides."""
    return _move_families(s, asked_families(s))


def _stranding_hold(s, av, kind):
    """Why a model or sign switch waits so as not to strand work made as the account in use, or None: optional
    switches are off this pass (just restarted, or whether Claude Code is busy is not known; a model switch stops
    waiting for this after CALM_MODEL_MAX_S), an open Doc or artifact made as it (a chat watching an artifact holds a
    model switch CALM_MODEL_MAX_S at most), or a low battery. Claude Code
    working is no hold here (with many chats open it always is)."""
    waited = (s.waited or {}).get(kind) or 0.0
    if s.no_optional and not (kind == 'model' and waited >= CALM_MODEL_MAX_S):
        return s.no_optional
    for ob in s.obligations:
        if kind == 'model' and ob.get('kind') == 'watch':
            # a chat watching an artifact can last all day with many chats open: a model switch waits for it at most
            # CALM_MODEL_MAX_S, as for "optional switches are off"; a Doc or a fresh artifact still holds it
            if waited >= CALM_MODEL_MAX_S:
                continue
            return '%s, %s at most' % (_obligation_words(ob, av.label, s.now), _span(CALM_MODEL_MAX_S - waited))
        return _obligation_words(ob, av.label, s.now)
    if s.on_ac is False and s.battery_pct is not None and s.battery_pct < BATTERY_LOW:
        return 'the battery is at %d%%' % s.battery_pct
    return None


def _model(s, av, cands, fams):
    """Rule 'model' (see the module's docstring), or None when the active account can serve every asked family."""
    short = lacks(av, fams, s.settings)
    if not short:
        return None
    f = short[0]
    serving = [v for v in cands if not last_days(v, s.now) and serves(v, fams, s.settings)
               and v.room is not None and v.room >= MODEL_ROOM]
    targets = [v for v in serving if rule_of(s.settings, v.id) != 'last'] or serving
    if targets:
        t = _rank(targets, s.settings.strategy, s, fams)[0]
        reason = '%s %s has room for %s (%s).' % (_used_words(av, f, s.settings), t.label, family_words(f),
                                                  _fam_pct_words(t, f))
        picked = pick_hold(s, av, 'model')
        if picked:
            return api.Decision('stay', target=t.id, kind='model', reason='%s %s has room on %s.' % (
                picked[0], family_words(f), t.label), hold=picked[1])
        if s.disagree:
            return _held(s, t, 'model', reason, "Claude Code's account label and its login disagree")
        stranding = _stranding_hold(s, av, 'model')
        if stranding:
            return _held(s, t, 'model', reason, stranding)
        # a number at its line cannot flap (a target needs 5 points under model_at); a refusal alone lapses after 10
        # minutes, so two accounts that both refuse would trade places: then it waits an hour after any switch
        if all(_refused_only(av, g, s.settings) for g in short) and s.since_switch_s is not None and \
                s.since_switch_s < MODEL_REFUSED_DWELL:
            return _held(s, t, 'model', reason, 'the last switch was %s ago; with only a refusal to go by it waits '
                         'an hour, until %s' % (_span(s.since_switch_s),
                                                _clock(s.now + MODEL_REFUSED_DWELL - s.since_switch_s, s.now)))
        # a family the setting names waits for a calm Mac as a forced switch does, at most CALM_FORCED_S
        named = named_families(s.settings)
        hold = _cap_hold(s, 'model') or calm_hold(s, 'model', CALM_FORCED_S if any(g in named for g in short) else None)
        if hold:
            return _held(s, t, 'model', reason, hold)
        return api.Decision('switch', target=t.id, kind='model', reason=reason)
    unread = [v for v in cands if not last_days(v, s.now) and _could_serve(v, fams, s.settings)]
    if unread and not s.candidates_read:
        return stay('%s Reading %s for room.' % (_used_words(av, f, s.settings), ', '.join(v.label for v in unread)),
                    kind='model', hold='no fresh numbers')
    for v in cands:
        why = _why_no_target(v, f, fams, s)
        if why:
            return stay('%s %s has room for %s but %s, so Claude Code stays.%s' % (
                _used_words(av, f, s.settings), v.label, family_words(f), why,
                _chats_words(av, f, False, s.keep_running)))
    return stay(no_room_words(s, av, f, cands))


def _refused_only(v, f, settings):
    """f has no room on this account only because a request for it was refused: no number shows it at its line."""
    p = v.fam.get(f)
    return f in v.refused and (p is None or p < settings.model_at)


def _why_no_target(v, f, fams, s):
    """Why an account with fresh room for f is still no target for a model switch, in words, or None."""
    if last_days(v, s.now) or not v.known or v.age is None or v.age > FORCED_MAX_AGE or v.room is None or \
            v.room < LAND_MARGIN:
        return None
    left = fam_left(v, f, s.settings)
    if left is None or left < LAND_MARGIN:
        return None
    if f in v.fam and (v.fam_ages.get(f) is None or v.fam_ages[f] > FORCED_MAX_AGE):
        return None
    for g in fams:
        gl = fam_left(v, g, s.settings)
        if g != f and (gl is None or gl < LAND_MARGIN):
            return 'no room for %s' % family_words(g)
    if v.room < MODEL_ROOM:
        return 'is near a line (%s)' % _pcts_words(v)
    return None


def _chats_words(av, f, until_then, keep_running=True):
    """' Chats run on Opus 5.5 (until then).' or ' Chats stay on Fable on a@x until it is used up, then ...'. With
    Keep running off no chat goes on with another model by itself: ' Chats that ask for Fable stop there until then;
    choose another model in the chat to go on.'"""
    g = step_down(av, f)
    here = av.fam.get(f)
    if f not in av.refused and here is not None and here < EXHAUSTED:
        if not keep_running:
            return ' Chats stay on %s on %s until it is used up.' % (family_words(f), av.label)
        return ' Chats stay on %s on %s until it is used up%s.' % (
            family_words(f), av.label, ', then run on %s' % family_words(g) if g else '')
    if not keep_running:
        w = _stop_words(f, until_then)
        return ' ' + w[0].upper() + w[1:]
    if g is None:
        return ''
    return ' Chats run on %s%s.' % (family_words(g), ' until then' if until_then else '')


def no_room_words(s, av, f, cands):
    """Rule 4's words: no account has room for f. 'Fable is used up on every account until Sun 2 PM (first: b@x).
    Chats run on Opus 5.5 until then.'"""
    views = [av] + list(cands)
    firsts = sorted((t, v.label) for v in views for t in (room_back(v, f, s.settings, s.now),) if t is not None)
    every = all(f in v.refused or (v.fam.get(f) is not None and v.fam[f] >= EXHAUSTED) for v in views)
    head = ('%s is used up on every account' if every else 'No account has room for %s') % family_words(f)
    head += ' until %s (first: %s).' % (_clock(firsts[0][0], s.now), firsts[0][1]) if firsts else \
        '; no refill time is known yet.'
    return head + _chats_words(av, f, bool(firsts), s.keep_running)


# ---------- sign lines ----------

def _sign_window_words(name):
    if name == WEEK:
        return 'its week'
    if name == FIVE:
        return 'its 5-hour window'
    return 'its %s week' % family_words(family_of(name))


def _pct_on(v, name):
    if name.startswith(MODEL):
        return v.fam.get(family_of(name))
    return v.pcts.get(name)


def _sign(s, av, cands, fams):
    """Rule 'sign' (see the module's docstring), or None."""
    over = sign_over(av, fams, s.settings)
    if not over:
        return None
    targets = [v for v in _optional_cands(s, av, cands)
               if not last_days(v, s.now) and serves(v, fams, s.settings, SIGN_MARGIN, sign=True)]
    if not targets:
        return None
    t = _rank(targets, s.settings.strategy, s, fams)[0]
    name, pct = over[0]
    theirs = _pct_on(t, name)
    reason = '%s is at %s%% of %s. %s is at %s.' % (av.label, _pct(pct), _sign_window_words(name), t.label,
                                                     '%s%%' % _pct(theirs) if theirs is not None else
                                                     'no limit of that kind')
    picked = pick_hold(s, av, 'sign')
    if picked:
        return api.Decision('stay', target=t.id, kind='sign', reason='%s %s is at %s%% of %s.' % (
            picked[0], av.label, _pct(pct), _sign_window_words(name)), hold=picked[1])
    if s.disagree:
        return _held(s, t, 'sign', reason, "Claude Code's account label and its login disagree")
    stranding = _stranding_hold(s, av, 'sign')
    if stranding:
        return _held(s, t, 'sign', reason, stranding)
    if s.since_switch_s is not None and s.since_switch_s < SIGN_DWELL:
        return _held(s, t, 'sign', reason, 'the last switch was %s ago; it waits 20 minutes, until %s' % (
            _span(s.since_switch_s), _clock(s.now + SIGN_DWELL - s.since_switch_s, s.now)))
    hold = _cap_hold(s, 'sign') or calm_hold(s, 'sign')
    if hold:
        return _held(s, t, 'sign', reason, hold)
    return api.Decision('switch', target=t.id, kind='sign', reason=reason)


# ---------- a sign-in in its last days ----------

def _day(t):
    lt = time.localtime(t)
    return '%s %d' % (('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')[lt.tm_mon - 1],
                      lt.tm_mday)


def _ending(s, av, cands, fams):
    """The account in use's sign-in is in its last days: an optional switch to an account whose own is not."""
    if not last_days(av, s.now) or av.ends <= s.now:
        return None
    # a family no account has room for (used up everywhere) does not stop this move: it runs on the next model on
    # every account alike, and waiting would leave only the forced move when the sign-in ends
    fams = tuple(f for f in fams if any(_may_serve(v, f, s.settings) for v in [av] + list(cands)))
    targets = [v for v in _optional_cands(s, av, cands) if not last_days(v, s.now) and serves(v, fams, s.settings)]
    if not targets:
        return None
    t = _rank(targets, s.settings.strategy, s, fams)[0]
    reason = "%s's sign-in ends by %s; %s" % (av.label, _day(av.ends), "%s's is good until %s" % (
        t.label, _day(t.ends)) if t.ends is not None else '%s is signed in' % t.label)
    picked = pick_hold(s, av, 'optional')
    if picked:
        return api.Decision('stay', target=t.id, kind='optional', reason='%s %s.' % (picked[0], reason),
                            hold=picked[1])
    hold = _hold(s, av, t) or calm_hold(s, 'optional')
    if hold:
        return api.Decision('stay', target=t.id, kind='optional', reason=_next_words(t, hold), hold=hold)
    return api.Decision('switch', target=t.id, kind='optional', reason=reason)


def _may_serve(v, f, settings):
    """Family f may have room on this account: its own window under its line by LAND_MARGIN, or not known."""
    left = fam_left(v, f, settings)
    return left is None or left >= LAND_MARGIN


def _next_words(t, hold):
    """'Next: b@x; waits: Claude Code is working.' or 'Next: b@x; it waits for the Mac to calm down (...).'"""
    return 'Next: %s; it %s.' % (t.label, hold) if hold.startswith('waits') else \
        'Next: %s; waits: %s.' % (t.label, hold)


def _optional_cands(s, av, cands):
    """The accounts a move that can wait may go to: never one used last, and never one ranked after the one in use
    (from an account used last, any that is not)."""
    cands = [v for v in cands if rule_of(s.settings, v.id) != 'last']
    if rule_of(s.settings, av.id) == 'last':
        return cands
    return [v for v in cands if _rank_key(v) <= _rank_key(av)]


def _fits(s, av, fams):
    """The test every optional target passes besides its own rule's: room for every asked family with OPTIONAL_ROOM,
    not in its sign-in's last days, and never over its sign lines when the account in use is under them."""
    under_signs = s.settings.sign_lines and serves(av, fams, s.settings, 0.0, sign=True, max_age=None)

    def fits(v):
        return serves(v, fams, s.settings, max(OPTIONAL_ROOM, LAND_MARGIN), max_age=None) and \
            not last_days(v, s.now) and \
            (not under_signs or serves(v, fams, s.settings, SIGN_MARGIN, sign=True, max_age=None))
    return fits


def _optional_go(s, av, t, reason, said):
    """An optional switch to t, or the same switch held: the person's own pick first (`said` is what it adds), then
    every hold (_hold), then a calm Mac."""
    picked = pick_hold(s, av, 'optional')
    if picked:
        return api.Decision('stay', target=t.id, kind='optional', reason='%s %s' % (picked[0], said), hold=picked[1])
    hold = _hold(s, av, t) or calm_hold(s, 'optional')
    if hold:
        return api.Decision('stay', target=t.id, kind='optional', reason=_next_words(t, hold), hold=hold)
    return api.Decision('switch', target=t.id, kind='optional', reason=reason)


def _optional(s, av, cands, fams=()):
    if s.settings.strategy != 'refills-first':
        return stay('%s is under its lines.' % av.label)
    if not av.known:
        return stay("%s's numbers are not known yet; nothing to compare." % av.label)
    # the target needs room for every asked family too, and never one over its sign lines from one under them
    fits = _fits(s, av, fams)
    cands = [v for v in cands if rule_of(s.settings, v.id) != 'last']      # never an optional switch to a last one
    if rule_of(s.settings, av.id) == 'last':
        return _off_last(s, av, cands, fams, fits)
    up = [v for v in cands if _rank_key(v) < _rank_key(av) and v.known and v.room >= max(OPTIONAL_ROOM, LAND_MARGIN)
          and fits(v)]
    if up:
        return _up_rank(s, av, up, fams)
    cands = [v for v in cands if _rank_key(v) == _rank_key(av)]       # never to an account ranked after this one
    if av.weekly_reset is None:
        return stay("%s's weekly refill time is not known, so it can't tell which account refills first." % av.label)
    pick = [v for v in cands
            if fits(v) and v.weekly_reset is not None and v.weekly_reset <= av.weekly_reset - REFILL_GAIN]
    if not pick:
        return stay('%s is under its lines and refills first among the accounts with room.' % av.label)
    ready = [v for v in pick if v.age is not None and v.age <= OPTIONAL_MAX_AGE and not v.frozen]
    t = (_rank(ready, 'refills-first', s, fams) or _rank(pick, 'refills-first', s, fams))[0]
    reason = '%s refills first (weekly %s, against %s for %s), %s points before its nearest line' % (
        t.label, _clock(t.weekly_reset, s.now), _clock(av.weekly_reset, s.now), av.label, _pct(t.room))
    return _optional_go(s, av, t, reason, '%s refills first.' % t.label)


def _off_last(s, av, cands, fams=(), fits=None):
    """Claude Code is on an account used last: an early move to the best account with room that is not last (no
    refill-gain test), under every hold."""
    pick = [v for v in cands if v.known and v.room >= max(OPTIONAL_ROOM, LAND_MARGIN) and (fits is None or fits(v))]
    if not pick:
        return stay('%s is used last, and no account used before it has room now.' % av.label)
    ready = [v for v in pick if v.age is not None and v.age <= OPTIONAL_MAX_AGE and not v.frozen]
    t = (_rank(ready, 'refills-first', s, fams) or _rank(pick, 'refills-first', s, fams))[0]
    return _optional_go(s, av, t, '%s is used last, and %s has room (%s points before its nearest line)'
                        % (av.label, t.label, _pct(t.room)), '%s has room.' % t.label)


def _up_rank(s, av, up, fams=()):
    """An account ranked before the one in use has room under every line (OPTIONAL_ROOM, known numbers): an early move
    to the best of them, as a move off an account used last is (no refill-gain test), under every hold."""
    top = min(_rank_key(v) for v in up)
    up = [v for v in up if _rank_key(v) == top]                         # the earliest rank first, ready or not
    ready = [v for v in up if v.age is not None and v.age <= OPTIONAL_MAX_AGE and not v.frozen]
    t = (_rank(ready, 'refills-first', s, fams) or _rank(up, 'refills-first', s, fams))[0]
    return _optional_go(s, av, t, '%s is %s, and %s, rank %d, has room (%s points before its nearest line)'
                        % (av.label, 'rank %d' % av.rank if av.rank is not None else 'not ranked', t.label, t.rank,
                           _pct(t.room)), '%s, rank %d, has room.' % (t.label, t.rank))


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
    cap = _cap_hold(s, 'optional')
    if cap:
        return cap
    if s.on_ac is False and s.battery_pct is not None and s.battery_pct < BATTERY_LOW:
        return 'the battery is at %d%%' % s.battery_pct
    return None


def _cap_hold(s, kind):
    """Why a switch of this kind waits for its cap or for a very busy Mac, or None. Optional and sign switches share
    OPTIONAL_CAP and never go over LOAD_HIGH; a model switch has MODEL_CAP of its own (its load rule is calm_hold's)."""
    if kind == 'model':
        if s.model_recent >= MODEL_CAP:
            return '%d switches for a model in the last 6 hours already' % s.model_recent
        return None
    n = s.optional_recent + s.sign_recent
    if n >= OPTIONAL_CAP:
        return '%d optional switches in the last 6 hours already' % n
    if s.load_per_core is not None and s.load_per_core > LOAD_HIGH:
        return 'the Mac is very busy (load %.0f per core)' % s.load_per_core
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
            t = _rank(landing, s.settings.strategy, s, deciding_families(s))[0]
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


# ---------- keeping Claude Code signed in ----------

def _when(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def ends_at(expires_at, signin_ends_at):
    """The wall time the sign-in in use stops working, from its login's access expiry and its sign-in end (wall
    seconds), or None when it renews as usual or either time is not known.

    Claude Code renews its access token facts.EXPIRY_BUFFER_S before it expires, and only while the sign-in's end is
    still ahead (its refresh token counts only while refreshTokenExpiresAt > now; checked in 2.1.329). Using it does
    not move that end. So when the end comes before that renewal moment, the renewal moment is the last one that works;
    END_SLACK_S of slack, since Claude Code stores the end a little lower at each renewal."""
    expires_at, signin_ends_at = _when(expires_at), _when(signin_ends_at)
    if expires_at is None or signin_ends_at is None:
        return None
    stop = expires_at - facts.EXPIRY_BUFFER_S
    return stop if signin_ends_at < stop + END_SLACK_S else None


def _minutes(seconds):
    n = max(1, int(math.ceil(seconds / 60.0)))
    return '1 minute' if n == 1 else '%d minutes' % n


LIVE_REFUSED = 'live:refused'      # Snapshot.lost when Claude Code emptied its own login after Anthropic refused it


def stay_why(s):
    """Why Claude Code must move to keep a sign-in that works, in plain words naming the account in use by its whole
    email (label_of), or None: the setting is off, Claude Code is not on a saved account, or neither `lost` nor a near
    `ends_at` says so. A sign-in that ends counts from LEAVE_AHEAD_S before it stops, and only while the live login
    reads fine."""
    if not s.settings.stay_signed_in or not s.active:
        return None
    a = dict((x.id, x) for x in s.accounts).get(s.active)
    if a is None:
        return None
    if s.lost == LIVE_REFUSED:              # what was refused is the login Claude Code held; the saved one is not known
        return "%s's sign-in was refused by Anthropic" % label_of(s, a)
    if s.lost:
        return '%s lost its sign-in and its saved login %s' % (label_of(s, a), s.lost)
    end = _when(s.ends_at)
    if end is not None and s.live_ok and s.now >= end - LEAVE_AHEAD_S:
        if s.now >= end:
            return "%s's sign-in has ended" % label_of(s, a)
        return "%s's sign-in ends in %s" % (label_of(s, a), _minutes(end - s.now))
    return None


def ends_soon(a, now):
    """The account's own sign-in ends within ENDS_SOON_S (or has ended): never a target for a move that keeps Claude
    Code signed in."""
    end = _when(a.refresh_expires_at)
    return end is not None and end - now <= ENDS_SOON_S


def _not_target(a, s):
    """Why an account is no target for a move that keeps Claude Code signed in, in words that start with its whole
    email, or None. (An account kept out of auto-switch is signed in and is said apart: _stay_signed_in.)"""
    who = label_of(s, a)
    if a.status == 'claimed' or a.id in s.claimed:
        return '%s is in use by a `switchboard run`' % who
    if a.status == 'needs_login':
        return '%s needs a sign-in' % who
    if a.status == 'conflict':
        return '%s has a saved login that may belong to another account' % who
    if a.status == 'unreadable':
        return "%s's saved login can't be read right now" % who
    if a.status not in OK_STATUSES:
        return '%s is not usable right now (%s)' % (who, a.status)
    if a.id in s.left:
        return '%s lost its sign-in and was not signed in again since' % who
    if ends_soon(a, s.now):
        left = a.refresh_expires_at - s.now
        return ("%s's sign-in has ended" % who) if left <= 0 else ("%s's sign-in ends in %s" % (who, _minutes(left)))
    return None


def _refused_first(s):
    """The forced switch for a sign-in Anthropic refused, decided before the move that keeps Claude Code signed in
    (see the module's docstring): with automatic switching on, the live login readable, the Mac neither settling nor
    offline, the account in use kept as an account and not kept out of auto-switch, not moved off a moment ago (the
    move's brake), and its sign-in refused (it needs a sign-in, or Claude Code emptied its login). Its targets are the
    ones that move may use: signed in (_not_target), not kept out of auto-switch (one kept out is named, as the forced
    switch names it). The forced switch's own Decision when it switches; None when this is not its case or it would
    not switch (the move then decides)."""
    if not s.settings.enabled or not s.live_ok or s.settling or s.offline:
        return None
    active = dict((a.id, a) for a in s.accounts).get(s.active)
    if active is None or not (active.status == 'needs_login' or s.live_refused):
        return None
    if rule_of(s.settings, s.active) == 'out' or s.active in s.moved_recently:
        return None
    av = _view(s, active)
    allowed = [a for a in s.accounts if _eligible(a, s) and _not_target(a, s) is None]
    cands = [_view(s, a) for a in allowed if rule_of(s.settings, a.id) != 'out']
    kept = [_view(s, a) for a in allowed if rule_of(s.settings, a.id) == 'out']
    d = _forced(s, active, av, cands, "%s's login was refused" % av.label, kept, deciding_families(s))
    return d if d.action == 'switch' else None


def _kept_off(s):
    """The words a move adds when the account it leaves is kept out of auto-switch: it moves all the same, since a
    sign-in that can't work is no place to stay. '' for any other account."""
    if s.active and rule_of(s.settings, s.active) == 'out':
        return "; it is kept out of auto-switch, but Claude Code can't stay signed in there"
    return ''


def _stay_signed_in(s, why):
    """The move that keeps Claude Code signed in (see the module's docstring). Pure, like decide().

    Its targets are the signed-in accounts (_not_target), never one kept out of auto-switch, and they are tried in the
    forced switch's order (_forced): (1) a landing place that is not used last, (2) one that is, (3) the move past a
    line to one that is not used last, (4) to one that is; ranks and the order chosen decide inside each, each account
    judged against its own lines (_view). Claude Code is moved off an account kept out of auto-switch too, and the
    reason says so (_kept_off)."""
    if s.settling:
        return stay('The Mac just woke; waiting for the network before any switch.')
    if s.offline:
        return stay('No connection to Anthropic; Claude Code stays where it is.')
    if s.active in s.moved_recently:
        return stay('%s; Switchboard moved Claude Code off it a few minutes ago, so it waits.' % why, kind='forced',
                    hold='moved recently')
    targets, kept, out = [], [], []
    for a in s.accounts:
        if a.id == s.active:
            continue
        no = _not_target(a, s)
        if no:
            out.append(no)
        elif rule_of(s.settings, a.id) == 'out':
            kept.append(_view(s, a))
            out.append('%s is %s' % (label_of(s, a), KEPT_OUT))
        else:
            targets.append(_view(s, a))
    if not targets:
        return stay('%s, and no other account %sis signed in%s.' % (
            why, 'auto-switch may use ' if kept else '', (': ' + '; '.join(out)) if out else ''),
            kind='forced', hold='no other account')
    fams = deciding_families(s)
    last = set(v.id for v in targets if rule_of(s.settings, v.id) == 'last')
    off = _kept_off(s)
    fresh = _fresh(targets)
    for group in ([v for v in fresh if v.id not in last], [v for v in fresh if v.id in last]):
        landing = [v for v in group if v.room >= LAND_MARGIN]
        if landing:
            t = _rank(landing, s.settings.strategy, s, fams)[0]
            return _go_signed_in(s, t, '%s%s; %s%s is signed in and has room (%s)' % (
                why, off, t.label, _tags(t, landing, last), _room_words(t, s.settings.strategy, s.now)))
    for group in ([v for v in fresh if v.id not in last], [v for v in fresh if v.id in last]):
        escape = [v for v in group if v.raw_room is not None and v.raw_room >= ESCAPE_ROOM]
        if escape:
            t = sorted(escape, key=lambda v: (last_days(v, s.now), v.frozen, -v.raw_room, v.order))[0]
            return _go_signed_in(s, t, '%s%s; no account is under its lines, but %s%s is signed in and can still '
                                 'work (%s)' % (why, off, t.label, ', used last,' if t.id in last else '',
                                                _pcts_words(t)))
    held = [v.label for v in _fresh(kept) if v.room >= LAND_MARGIN or v.raw_room >= ESCAPE_ROOM]
    also = ' (%s %s kept out of auto-switch)' % (_names(held), 'is' if len(held) == 1 else 'are') if held else ''
    fresh_ids = set(v.id for v in fresh)
    stale = [v for v in targets if v.id not in fresh_ids]
    if stale and not s.candidates_read:
        return stay('%s; no other account has fresh numbers with room yet (reading %s)%s.'
                    % (why, ', '.join(v.label for v in stale), also), kind='forced', hold='no fresh numbers')
    # old numbers are landed on only while Claude Code can't work at all, and only for an account this pass tried to
    # read and could not (read_failed; None: the caller does not say, as before it was told)
    end = _when(s.ends_at)
    cannot = bool(s.lost) or (end is not None and s.now >= end)
    if s.read_failed is not None:
        stale = [v for v in stale if v.id in s.read_failed]
    if stale and not cannot:
        return stay('%s; no other account has fresh numbers with room yet (reading %s)%s.'
                    % (why, ', '.join(v.label for v in stale), also), kind='forced', hold='no fresh numbers')
    if stale:
        t = sorted(stale, key=lambda v: (last_days(v, s.now), v.id in last, _rank_key(v), v.room is None,
                                         -(v.room or 0.0), v.order))[0]
        return _go_signed_in(s, t, '%s%s; %s%s is signed in, but %s' % (
            why, off, t.label, _tags(t, stale, last), _old_words(t)))
    firsts = [(refill_of(v, s.now), v.label) for v in targets]
    firsts = sorted(x for x in firsts if x[0] is not None)
    back = ' First refill: %s (%s).' % (_clock(firsts[0][0], s.now), firsts[0][1]) if firsts else \
        ' No refill time is known yet.'
    return stay('%s; no other signed-in account has room%s, so it stays.%s' % (why, also, back), kind='forced',
                hold='no account has room')


def _go_signed_in(s, t, reason):
    """A move that keeps Claude Code signed in: a sign-in lost or ended goes at once (nothing works there); one that
    is about to end waits for a calm Mac CALM_FORCED_S at most."""
    end = _when(s.ends_at)
    cannot = bool(s.lost) or (end is not None and s.now >= end)
    hold = calm_hold(s, 'forced', 0.0 if cannot else CALM_FORCED_S)
    if hold:
        return _held(s, t, 'forced', _sentence(reason), hold)
    return api.Decision('switch', target=t.id, kind='forced', reason=reason)


def _pcts_words(v):
    return ', '.join('%s %s%%' % (_window_words(n), '?' if p is None else _pct(p)) for n, p in v.pcts.items())


def _old_words(v, short=False):
    """'its numbers are 3 h 5 min old (weekly 40%)' or 'its numbers are not known yet'."""
    if v.known and v.age is not None:
        return 'its numbers are %s old%s' % (_span(v.age), '' if short else ' (%s)' % _pcts_words(v))
    return 'its numbers are not known yet'


def stay_note(decision, s, why=None, short=False, room=None):
    """The sentence a move that keeps Claude Code signed in carries to the history and the notification: why, where
    to, that automatic switching is off when it is (and that the account left is kept out of auto-switch when it is),
    that the numbers it went on are old when they are, and that the account left can be signed in again. Whole
    emails throughout (label_of).

    Built to fit `room` characters (by default HISTORY_ROOM for the history's sentence, NOTE_ROOM for the
    notification's body). It always holds why, where Claude Code is now and the sign-in-again sentence; then the
    optional parts, each only when the whole still fits: in the history, that automatic switching is off, then that the
    numbers are old (a move made with switching off says so wherever it is read later); in the notification, the other
    way round. A part that does not fit is left out whole, so no word or email is ever cut. When even what must stay
    does not fit with the account named in the why (very long emails), the why is the one clause the notification
    uses.

    short: the notification's body: why in one clause, and the old numbers' age without the numbers."""
    why = why or stay_why(s)
    by_id = dict((a.id, a) for a in s.accounts)
    left = by_id.get(s.active)
    t = by_id.get(decision.target)
    to = label_of(s, t) if t else decision.target
    although = ([] if s.settings.enabled else ['automatic switching is off']) + \
        (['it was on an account kept out of auto-switch'] if _kept_off(s) else [])
    off = ', although %s' % ' and '.join(although) if although else ''
    old = ''
    if t is not None:
        v = _view(s, t)
        if not (v.known and v.age is not None and v.age <= FORCED_MAX_AGE):
            old = " %s's%s." % (to, _old_words(v, short)[len('its'):])
    again = ' Sign in to %s again when you like.' % label_of(s, left) if left is not None else ''
    room = room or (NOTE_ROOM if short else HISTORY_ROOM)

    def text(o, f):
        return '%s. Switchboard moved Claude Code to %s%s.%s%s' % (why, to, f, o, again)
    if short or len(text('', '')) > room:
        why = _short_why(s, why)
    if short:
        old = old if len(text(old, '')) <= room else ''
        return text(old, off if len(text(old, off)) <= room else '')
    off = off if len(text('', off)) <= room else ''
    return text(old if len(text(old, off)) <= room else '', off)


def _short_why(s, why):
    """Why in one clause, for the notification: what happened to Claude Code's sign-in (the account is named in the
    sign-in-again sentence)."""
    end = _when(s.ends_at)
    if s.lost:
        return 'Claude Code lost its sign-in'
    if end is not None:
        return "Claude Code's sign-in has ended" if s.now >= end else \
            "Claude Code's sign-in ends in %s" % _minutes(end - s.now)
    return why


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
    fams = deciding_families(s)
    for a in s.accounts:
        if _eligible(a, s) and rule_of(s.settings, a.id) != 'out':
            v = _view(s, a)
            if not v.known or v.age is None or v.age > FORCED_MAX_AGE:
                out.append(a.id)
            elif any(f in v.fam and f not in v.refused and (v.fam[f] is None or v.fam_ages.get(f) is None or
                                                            v.fam_ages[f] > FORCED_MAX_AGE) for f in fams):
                out.append(a.id)            # its own window of an asked family is old or refilled since it was read
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
    """The active account cannot work at all now (a window at 100%, a refused login, refused requests), or its
    sign-in is lost or about to stop (stay_why)."""
    a = dict((x.id, x) for x in s.accounts).get(s.active)
    if stay_why(s) is not None:
        return True                     # its sign-in is lost or about to stop: it can't work there
    return a is not None and _blocked(_view(s, a), s, a)


def switch_note(decision, snapshot):
    """The sentence a switch carries to the history and the notification. When the account left was truly blocked
    (a window at 100%, refused requests or a refused login) a reply may have stopped: it says to send the message
    again. It never offers to send anything itself. A move that keeps Claude Code signed in says stay_note(), except
    the forced switch for a sign-in Anthropic refused (_refused_first), which says its own words."""
    why = stay_why(snapshot) if decision.action == 'switch' else None
    if why is not None and _refused_first(snapshot) is None:
        return stay_note(decision, snapshot, why)
    note = decision.reason
    if decision.kind != 'forced':
        return note
    if active_blocked(snapshot):
        target = dict((a.id, a) for a in snapshot.accounts).get(decision.target)
        to = label_of(snapshot, target) if target else decision.target
        tail = '. Switched to %s. If a reply stopped, send your message again.' % to
        if len(note) + len(tail) > HISTORY_ROOM and note.endswith(')') and ' (' in note:
            note = note[:note.rindex(' (')]         # the model clause goes first: the last sentence must stay whole
        note = note + tail
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
