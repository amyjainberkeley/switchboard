"""Which provider takes a new task, with which model and effort, and why. Pure: no file, no clock, no program, no words.

choose() looks at candidates in turn. Without a provider named and without a stay, they are the choices of this kind's
route, in order, then every other provider of the person's order, in order. A candidate that cannot run (not added,
not installed, signed out), or that this folder never goes to (the person's `never`), is passed. The first with room is
picked; if none has room, the first whose numbers are not
known; if every one is out, nobody, with the earliest time one is back when that is known. A provider named with --on
decides as place.pick(to=...) does: it is picked unless it cannot run, even when it is out, since the person named it.
A folder's stay means only that provider is looked at. A provider named with --on that the folder never goes to is
nobody, code `never`. The model and effort come from the route's choice for the
provider picked; a provider picked from the order has none. Rooms are place.room_of's (contract.Room); nothing here
says anything to the person: the reason is a code, and the command makes the sentence.
"""
from dataclasses import dataclass
from typing import List, Mapping, Optional, Sequence, Tuple, Union

from .contract import NOT_ADDED, NOT_INSTALLED, OUT, ROOM, SIGNED_OUT, UNKNOWN, Room

# Why the provider was picked, or why nobody was.
REASONS = ('route',             # the first choice of this kind's route
           'route_next',        # a later choice of the route: the ones before it were passed
           'order',             # no route for this kind: the first of the person's order
           'order_next',        # a later one of the order (the route's choices, or the order's first, were passed)
           'named',             # the provider named with --on
           'stay',              # this folder stays on it
           'unknown',           # nobody had room; its numbers are not known and it came first
           'all_out',           # every candidate that can run is out
           NOT_ADDED,           # the one named, or every candidate, cannot run: as place.pick says it
           NOT_INSTALLED,
           SIGNED_OUT,
           'never',             # the one named, or every candidate, is one this folder never goes to
           'no_other',          # there was nobody to look at
           'stays_elsewhere')   # the provider named is not the one this folder stays on
# Why a candidate looked at before the pick was passed: it cannot run, it is out, or its numbers are not known while a
# later one has room.
CANNOT = (NOT_ADDED, NOT_INSTALLED, SIGNED_OUT)
NEVER = 'never'
PASSED = CANNOT + (NEVER, 'out', 'unknown')
_NOT_THIS_ONE = CANNOT + (NEVER,)


@dataclass(frozen=True)
class RouteChoice:
    """One choice of a route: a provider, and optionally the model and the effort to ask it for (the words its app
    takes, as the catalog names them)."""
    provider: str
    model: Optional[str] = None
    effort: Optional[str] = None


@dataclass(frozen=True)
class Choice:
    """What choose() decided. provider: the one picked, or None. model, effort: from the route's choice for it (None
    when it came from the order). reason: one of REASONS. passed: (provider, code from PASSED) for each candidate
    looked at before the pick (every candidate, when nobody was picked), in order. back_at: with all_out, when the
    first one is back, when that is known. from_route: the pick was one of the route's choices. kind: as given."""
    provider: Optional[str]
    model: Optional[str]
    effort: Optional[str]
    reason: str
    passed: Tuple[Tuple[str, str], ...] = ()
    back_at: Optional[float] = None
    from_route: bool = False
    kind: str = ''


def _choice(c) -> RouteChoice:
    if isinstance(c, RouteChoice):
        return c
    provider, model, effort = (tuple(c) + (None, None))[:3]
    return RouteChoice(provider, model, effort)


def _candidates(route, order, stay, to) -> List[Tuple[RouteChoice, bool]]:
    """(choice, from the route) in the order they are looked at. A route that names a provider twice keeps its first
    choice for it."""
    out, seen = [], set()
    for c in (_choice(x) for x in (route or ())):
        if c.provider not in seen:
            seen.add(c.provider)
            out.append((c, True))
    for p in (order or ()):
        if p not in seen:
            seen.add(p)
            out.append((RouteChoice(p), False))
    want = to if to is not None else stay
    if want is not None:
        mine = [x for x in out if x[0].provider == want]
        out = mine or [(RouteChoice(want), False)]
    return out


def _first(states: Sequence[str], wanted: str) -> Optional[int]:
    return next((i for i, s in enumerate(states) if s == wanted), None)


def _code(state: str) -> str:
    return state if state in _NOT_THIS_ONE else ('out' if state == OUT else 'unknown')


def choose(kind: str, route, order: Sequence[str], rooms: Union[Mapping[str, Room], Sequence[Room]],
           stay: Optional[str] = None, to: Optional[str] = None, never=()) -> Choice:
    """The provider for a new task of this kind (see the module's opening lines). route: this kind's choices
    (RouteChoice, or (provider, model, effort) tuples), or None. order: the person's added providers, in order.
    rooms: each provider's Room (place.room_of, or a cannot-run state); one with no Room is not added. never: the
    providers this folder never goes to."""
    never = frozenset(never or ())
    by_id = dict(rooms) if isinstance(rooms, Mapping) else {r.provider: r for r in rooms}
    if stay is not None and to is not None and stay != to:
        return Choice(None, None, None, 'stays_elsewhere', kind=kind)
    looked = _candidates(route, order, stay, to)
    if not looked:
        return Choice(None, None, None, 'no_other', kind=kind)
    states = [NEVER if c.provider in never else by_id[c.provider].state if c.provider in by_id else NOT_ADDED
              for c, _ in looked]
    if to is not None:
        c, from_route = looked[0]
        if states[0] in _NOT_THIS_ONE:
            return Choice(None, None, None, states[0], passed=((to, states[0]),), kind=kind)
        return Choice(to, c.model, c.effort, 'named', from_route=from_route, kind=kind)
    at = _first(states, ROOM)
    if at is None:
        at = _first(states, UNKNOWN)
        why = 'unknown'
    else:
        c, from_route = looked[at]
        why = 'stay' if stay is not None else (('route' if at == 0 else 'route_next') if from_route
                                               else ('order' if at == 0 else 'order_next'))
    passed = tuple((c.provider, _code(s)) for (c, _), s in zip(looked[:len(looked) if at is None else at], states))
    if at is not None:
        c, from_route = looked[at]
        return Choice(c.provider, c.model, c.effort, why, passed=passed, from_route=from_route, kind=kind)
    out = [by_id[c.provider].back_at for (c, _), s in zip(looked, states) if s == OUT]
    if not out:
        return Choice(None, None, None, states[0], passed=passed, kind=kind)
    back = None if None in out else min(out)
    return Choice(None, None, None, 'all_out', passed=passed, back_at=back, kind=kind)
