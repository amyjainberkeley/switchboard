"""The rule every router adapter applies to a plan. Pure: no file, no clock, no program, nothing kept.

usable(plan, now): the plan may be used now (its version is 1, made_at <= now + AHEAD_S, now <= made_at +
good_for_s with good_for_s at most MAX_GOOD_FOR_S, routing is on).

resolve(plan, kind, here, now, where=None): where a piece of work of this kind, started in the app `here`, runs.
`where` is how it was started: "workflow" (a workflow script's agent), "helper" (any other helper or teammate),
"chat" (a turn of the person's own chat), or None (no default is looked at). `kind` may be None when it could not be
read; a None kind has no route. In order:
1. The plan cannot be used: {"do": "nothing", "reason": code}, code one of no_plan (no plan, or one this rule cannot
   read), old_plan, off. The kind has no route: the plan's default for `where` when it names a model or an effort
   ({"do": "model", "model", "effort", "reason": "default"}), else {"do": "nothing", "reason": "no_route"}. A
   default's model is given per app ({"claude": "haiku"}) and only the entry for `here` is used. A default never
   hands work to another app and never looks at room.
2. The route's choices are looked at in turn. A choice is passed when its app is not in the plan's agents
   (not_added), cannot take work (its why_not), is out of room (out), or is another app while the hand switch is off
   (hand_off). A choice whose room is not known is taken only when no later choice that would be taken has room
   (else it is passed: unknown).
3. The first choice not passed is the pick. In `here`: {"do": "model", "model", "effort", "reason": "route"}, or
   {"do": "nothing", "reason": "stays"} when it names neither. In another app: {"do": "hand", "to", "model",
   "effort", "reason": "route"}.
4. Every choice passed: {"do": "nothing", "reason": "all_passed", "back_at": the earliest known time a choice passed
   as out has room again, or null}. A route whose choices were all passed never falls to the default.
Every answer also holds "kind" (as given), "passed" ([[app, code], ...] in the order looked at) and "why" (the plan's
words for the kind, or null; null for a default). The answer is a new dict each time.

apply_effort(current, answer_effort, reason): the effort to set on work running at `current`, or None to leave it.
A route's effort (reason "route") is set, up or down: the person chose it for that kind of work. A default's effort
(reason "default") is a ceiling: work above it is lowered to it, work at or under it is left as someone chose it.
Nothing is set on work with no effort (current None) or when either word is not one of EFFORTS.

kind_of_work(kinds, types, helper_type, description, task): the kind of a helper's work. types[helper_type] when the
plan names that helper type; else the kind its short description reads as; and when that is "other", the kind the
first sentence of its task reads as. The words are read by providers/kinds.py's own matcher, with the plan's table.
None when the plan's kinds block cannot be read. The words are read here, in memory, and never kept.

tests/router_fixtures.json holds the cases every other way of reading a plan must agree with.
"""
import math

from ..providers import kinds as _kinds

PLAN_V = 1
ROOMS = ('room', 'out', 'unknown')
WHY_NOT = ('not_added', 'not_installed', 'signed_out', 'too_old', 'never')
PASS_CODES = WHY_NOT + ('out', 'hand_off', 'unknown')
NOT_USABLE = ('no_plan', 'old_plan', 'off')
REASONS = NOT_USABLE + ('no_route', 'stays', 'all_passed', 'route', 'default')
MAX_GOOD_FOR_S = 3600       # a plan that says it is good for longer cannot be used (no_plan)
AHEAD_S = 60                # a plan made more than this after now is not current (old_plan)
WHERE = ('workflow', 'helper', 'chat')
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')     # lowest first
OTHER = 'other'

# kinds.kind_of names its kinds by kinds.KINDS: the plan's own kind names are given those names for the read, the
# light one as kinds.LIGHT, "other" as itself, the rest in the plan's order, so a tie keeps the plan's order.
_SLOTS = tuple(k for k in _kinds.KINDS if k not in (_kinds.LIGHT, OTHER))


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _problem(plan, now):
    """Why the plan cannot be used now (no_plan, old_plan, off), or None."""
    if not isinstance(plan, dict):
        return 'no_plan'
    v, hand = plan.get('v'), plan.get('hand')
    if not _num(v) or v != PLAN_V:
        return 'no_plan'
    if not (_num(plan.get('made_at')) and _num(plan.get('good_for_s')) and isinstance(plan.get('on'), bool)
            and isinstance(plan.get('agents'), dict) and isinstance(plan.get('routes'), dict)
            and isinstance(hand, dict) and isinstance(hand.get('on'), bool)):
        return 'no_plan'
    if plan['good_for_s'] > MAX_GOOD_FOR_S:
        return 'no_plan'
    # A plan from the future (a clock set back, a hand-made file) is not current, however long it says it is good.
    if not _num(now) or now > plan['made_at'] + plan['good_for_s'] or plan['made_at'] > now + AHEAD_S:
        return 'old_plan'
    if not plan['on']:
        return 'off'
    return None


def usable(plan, now) -> bool:
    """The plan may be used now: version 1, made_at <= now + AHEAD_S, now <= made_at + good_for_s (at most
    MAX_GOOD_FOR_S), and on."""
    try:
        return _problem(plan, now) is None
    except Exception:  # noqa: BLE001 - a plan that cannot be read is not usable
        return False


def _route(plan, kind):
    """This kind's choices as (app, model, effort); [] for no route; None for a route this rule cannot read."""
    if not isinstance(kind, str):
        return []
    got = plan['routes'].get(kind)
    if got is None:
        return []
    if not isinstance(got, list):
        return None
    out = []
    for c in got:
        if not isinstance(c, dict) or not isinstance(c.get('agent'), str):
            return None
        model, effort = c.get('model'), c.get('effort')
        if (model is not None and not isinstance(model, str)) or (effort is not None and not isinstance(effort, str)):
            return None
        out.append((c['agent'], model, effort))
    return out


def _look(agents, agent, here, hand_on):
    """(pass code or None, room when not passed, back_at when passed as out)."""
    a = agents.get(agent)
    if not isinstance(a, dict):
        return 'not_added', None, None
    if a.get('can') is not True:
        why = a.get('why_not')
        return (why if isinstance(why, str) and why in WHY_NOT else 'not_added'), None, None
    room = a.get('room') if a.get('room') in ROOMS else 'unknown'
    if room == 'out':
        back = a.get('back_at')
        return 'out', None, back if _num(back) else None
    if agent != here and not hand_on:
        return 'hand_off', None, None
    return None, room, None


def _default(plan, where, here):
    """(model, effort) of the plan's default for `where`, or None when there is none it can read or it names
    neither. A default's model names its agent ({"claude": "haiku"}): only the entry for `here` is read, so a model
    set for one agent is never given to another. A model that is not such an object (a bare word) cannot be read."""
    if where not in WHERE:
        return None
    got = plan.get('defaults')
    got = got.get(where) if isinstance(got, dict) else None
    if not isinstance(got, dict):
        return None
    model, effort = got.get('model'), got.get('effort')
    if isinstance(model, dict):
        model = model.get(here) if isinstance(here, str) else None
    elif model is not None:
        return None
    if not (model is None or isinstance(model, str)) or not (effort is None or isinstance(effort, str)):
        return None
    if model is None and effort is None:
        return None
    return model, effort


def _answer(do, kind, passed, why, **more) -> dict:
    out = {'do': do}
    out.update(more)
    out.update(kind=kind, passed=[list(p) for p in passed], why=why)
    return out


def _resolve(plan, kind, here, now, where) -> dict:
    problem = _problem(plan, now)
    if problem is not None:
        return _answer('nothing', kind, (), None, reason=problem)
    words = plan.get('why')
    why = words.get(kind) if isinstance(words, dict) and isinstance(kind, str) else None
    why = why if isinstance(why, str) else None
    route = _route(plan, kind)
    if route is None:
        return _answer('nothing', kind, (), None, reason='no_plan')
    if not route:
        got = _default(plan, where, here)
        if got is not None:
            return _answer('model', kind, (), None, model=got[0], effort=got[1], reason='default')
        return _answer('nothing', kind, (), why, reason='no_route')
    looked = [_look(plan['agents'], agent, here, plan['hand']['on']) for agent, _, _ in route]
    passed, backs = [], []
    for i, ((agent, model, effort), (code, room, back)) in enumerate(zip(route, looked)):
        if code is None and room == 'unknown' and any(c is None and r == 'room' for c, r, _ in looked[i + 1:]):
            code = 'unknown'
        if code is not None:
            passed.append((agent, code))
            if back is not None:
                backs.append(back)
            continue
        if agent != here:
            return _answer('hand', kind, passed, why, to=agent, model=model, effort=effort, reason='route')
        if model is None and effort is None:
            return _answer('nothing', kind, passed, why, reason='stays')
        return _answer('model', kind, passed, why, model=model, effort=effort, reason='route')
    return _answer('nothing', kind, passed, why, reason='all_passed', back_at=min(backs) if backs else None)


def resolve(plan, kind, here, now, where=None) -> dict:
    """Where work of this kind, started in `here` as `where`, runs (see the module's opening lines). Never raises:
    anything this rule cannot read is {"do": "nothing", "reason": "no_plan"}."""
    try:
        return _resolve(plan, kind, here, now, where)
    except Exception:  # noqa: BLE001 - the adapter fails open: the app goes on as if there were no plan
        return _answer('nothing', kind, (), None, reason='no_plan')


def apply_effort(current, answer_effort, reason):
    """The effort to set on work now at `current`, or None to leave it (see the module's opening lines)."""
    try:
        if current not in EFFORTS or answer_effort not in EFFORTS:
            return None
        if reason == 'route':
            return answer_effort
        if reason == 'default' and EFFORTS.index(current) > EFFORTS.index(answer_effort):
            return answer_effort
        return None
    except Exception:  # noqa: BLE001 - a value that cannot be compared sets nothing
        return None


# ---------- the kind of a helper's work ----------

def _whole(x) -> bool:
    return _num(x) and float(x).is_integer()


def _word_rule(kinds):
    """(table in kinds.kind_of's names, {its name: the plan's name}, cut), or None when the block cannot be read: an
    order of distinct names ending with "other", a light kind in it (not "other"), a whole cut from 1 to kinds.CUT,
    and a table of the order's kinds, each a list of phrases that are not blank."""
    if not isinstance(kinds, dict):
        return None
    order, light, cut, table = kinds.get('order'), kinds.get('light'), kinds.get('cut'), kinds.get('table')
    if not isinstance(order, list) or not order or not all(isinstance(k, str) for k in order) or \
            len(set(order)) != len(order) or order[-1] != OTHER:
        return None
    if not isinstance(light, str) or light not in order or light == OTHER:
        return None
    if not _whole(cut) or not 1 <= cut <= _kinds.CUT or not isinstance(table, dict):
        return None
    heavy = [k for k in order[:-1] if k != light]
    if len(heavy) > len(_SLOTS):
        return None
    names = dict(zip(heavy, _SLOTS))
    names.update({light: _kinds.LIGHT, OTHER: OTHER})
    mapped = {}
    for k, phrases in table.items():
        if k not in names or not isinstance(phrases, list) or \
                not all(isinstance(p, str) and p.strip() for p in phrases):
            return None
        mapped[names[k]] = tuple(phrases)
    return mapped, {v: k for k, v in names.items()}, int(cut)


def _read(rule, text) -> str:
    mapped, back, cut = rule
    text = text if isinstance(text, str) else ''
    if cut < _kinds.CUT:
        text = _kinds.first_sentence(text)[:cut]
    kind, _ = _kinds.kind_of(text, mapped)
    return back.get(kind, OTHER)


def kind_of_work(kinds, types, helper_type, description, task):
    """The kind of a helper's work (see the module's opening lines), or None when kinds cannot be read."""
    try:
        rule = _word_rule(kinds)
        if rule is None:
            return None
        if isinstance(types, dict) and isinstance(helper_type, str):
            named = types.get(helper_type)
            if isinstance(named, str) and named in kinds['order']:
                return named
        kind = _read(rule, description)
        return kind if kind != OTHER else _read(rule, task)
    except Exception:  # noqa: BLE001 - no kind: the adapter leaves the work as it was
        return None
