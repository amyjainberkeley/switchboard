"""How much room a provider has, and which provider is next. Pure: no file, no clock, no program, no words.

room_of() works out one provider's Room from its Limits, the same way for every provider (an adapter only sets each
Window's line). pick() walks the person's order. Neither says anything to the person: Room.why stays empty and pick
gives a reason code; the command turns both into sentences.
"""
from typing import Mapping, Optional, Sequence, Tuple, Union

from .contract import NOT_ADDED, NOT_INSTALLED, OUT, ROOM, SIGNED_OUT, UNKNOWN, Limits, Room, Window

FULL_AT = 100.0     # a window without a line of its own is full at 100%

# pick()'s reason codes.
REASONS = ('room',          # the provider picked has room
           'unknown',       # its numbers are not known; it is the next in the order
           'out',           # the named provider is out (picked all the same: the person named it)
           'all_out',       # every other provider is out
           'not_added',     # the named provider is not added
           'not_installed',
           'signed_out',
           'is_source',     # the named provider is the one the chat is in
           'no_other')      # no other provider is added


def _known(w: Window, now: float) -> bool:
    """A number that still stands: read, and its window has not refilled since (a reset that has passed)."""
    return w.used_pct is not None and not (w.resets_at is not None and w.resets_at <= now)


def _full(w: Window, now: float) -> bool:
    return _known(w, now) and w.used_pct >= (w.line if w.line is not None else FULL_AT)


def _seat(lim: Limits, now: float, fresh_s: float):
    """(state of one seat, its refill time when out)."""
    windows = tuple(lim.windows or ())
    full = [w for w in windows if _full(w, now)]
    if lim.reached and not full:
        full = list(windows)        # the app says a limit is reached: every window counts until it refills
    if full or lim.reached:
        resets = [w.resets_at for w in full]
        return OUT, (max(resets) if resets and None not in resets else None)
    fresh = lim.read_at is not None and now - lim.read_at <= fresh_s and not lim.error
    if fresh and any(_known(w, now) for w in windows):
        return ROOM, None
    return UNKNOWN, None


def room_of(provider: str, name: str, limits, now: float, fresh_s: float) -> Room:
    """DESIGN 8.4. A window is full when its number is at or over its line, or when the app says a limit is reached.
    A seat has room when its numbers are fresh (read within fresh_s, no error), no window is full and at least one is
    known; it is out when a window is full, until the latest reset among its full windows (None when one is not
    known). The provider is ROOM when any seat has room, else UNKNOWN when any seat is not known, else OUT until the
    first seat refills (None when any refill is not known). name is not used: why is left to the command."""
    seats = [lim for lim in (limits if isinstance(limits, (tuple, list)) else ()) if isinstance(lim, Limits)]
    states = [(lim, ) + _seat(lim, now, fresh_s) for lim in seats]
    with_room = [lim for lim, state, _ in states if state == ROOM]
    if with_room:
        return Room(provider=provider, state=ROOM, read_at=max(lim.read_at for lim in with_room))
    read = [lim.read_at for lim in seats if lim.read_at is not None]
    newest = max(read) if read else None
    if not states or any(state == UNKNOWN for _, state, _ in states):
        return Room(provider=provider, state=UNKNOWN, read_at=newest)
    refills = [back for _, _, back in states]
    return Room(provider=provider, state=OUT, back_at=None if None in refills else min(refills), read_at=newest)


_CANNOT = {NOT_ADDED: 'not_added', NOT_INSTALLED: 'not_installed', SIGNED_OUT: 'signed_out'}


def pick(order: Sequence[str], current: Optional[str], rooms: Union[Mapping[str, Room], Sequence[Room]],
         to: Optional[str] = None) -> Tuple[Optional[str], str]:
    """DESIGN 8.5: (the provider to carry on in, or None, and a reason code from REASONS). With to: that provider,
    unless it is current or cannot run (not added, not installed, signed out); it is picked even when out, since the
    person named it. Without: the first in order (current skipped) with room, else the first whose numbers are not
    known, else None: 'all_out'; or, when no other provider can run, why the first one in the order cannot
    ('not_installed', 'signed_out', 'not_added'); or 'no_other' when the order holds no other."""
    by_id = dict(rooms) if isinstance(rooms, Mapping) else {r.provider: r for r in rooms}
    if to is not None:
        if to == current:
            return None, 'is_source'
        r = by_id.get(to)
        if r is None:
            return None, 'not_added'
        if r.state in _CANNOT:
            return None, _CANNOT[r.state]
        return to, {ROOM: 'room', OUT: 'out'}.get(r.state, 'unknown')
    listed = [by_id[p] for p in order if p != current and p in by_id]
    others = [r for r in listed if r.state not in _CANNOT]
    for wanted, why in ((ROOM, 'room'), (UNKNOWN, 'unknown')):
        for r in others:
            if r.state == wanted:
                return r.provider, why
    if others:
        return None, 'all_out'
    return None, (_CANNOT[listed[0].state] if listed else 'no_other')
