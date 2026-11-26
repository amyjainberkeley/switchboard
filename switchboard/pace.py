"""The pace of an account's week: at the rate of its use so far, is its weekly limit full before it refills?

One rule for every surface (the page, the terminal, the menu bar, `switchboard status --json` and History), so they
never disagree. It is plan.week_ahead on the account's own reading: the percent used so far, over the time since the
window opened (seven days before its reset), carried on to 100%. It asks more of the reading than that:

- The reading is one the page shows (trusted is not False) and at most FRESH seconds old. An older one says nothing.
- The weekly window has a number, and a reset still ahead of the clock.
- plan.week_ahead has something to say: the window has been open 12 hours and 10% is used, the reading is under 100%,
  and the projection is still ahead of the clock. Before that, `ahead` is None: too little to say.

"Full before it refills" and "used more than an even week would have by now" are the same test (a rate that fills 100%
in under the week's length), so `ahead` and the words always agree. When the week lasts, nothing is said anywhere.

History says it once a week (note_history): the first time in a week that an account would be full before it refills,
one line, never repeated for that week. The state remembers which week each account was said for (`pace_said`:
{account: that week's reset, epoch seconds}), each kept until 8 days after its reset.
"""
import time

from . import plan
from .core import parse_iso

WEEK = plan.WEEK
FRESH = 3600.0          # seconds: as server.SHOWN_FOR, the oldest reading the page still shows
AHEAD = 5.0             # a reading dated further than this past the clock is not used


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float('inf')


def week(a, now):
    """{'even': the whole percent an even week would be at now, 'ahead': True when, at the pace of its use so far, the
    weekly limit is full before it refills (False when it lasts; None when there is too little to say), 'full_at':
    when it would be full (epoch seconds, only when ahead)}; None when the account's weekly reading can't be used at
    all. `a` is an account as the page's state has it (usage, usage_at, trusted)."""
    if not isinstance(a, dict) or a.get('trusted') is False or not _num(now):
        return None
    u = a.get('usage') if isinstance(a.get('usage'), dict) else {}
    w = u.get('weekly') if isinstance(u.get('weekly'), dict) else {}
    used, at, resets = w.get('pct'), a.get('usage_at'), parse_iso(w.get('resets'))
    if not _num(used) or not _num(at) or resets is None or resets <= now:
        return None
    if now - at > FRESH or at - now > AHEAD:
        return None
    even = min(100.0, max(0.0, (now - (resets - WEEK)) * 100.0 / WEEK))
    out = {'even': int(even + 0.5), 'ahead': None, 'full_at': None}
    est = plan.week_ahead(used, resets, at, now)
    if est is not None:
        out['ahead'] = not est['lasts']
        out['full_at'] = est['out_at'] if not est['lasts'] else None
    return out


def full_at(a, now):
    """When the weekly limit would be full at this pace, before it refills (epoch seconds); None when it lasts or there
    is too little to say. What every surface says words for."""
    p = week(a, now)
    return p['full_at'] if p and p['ahead'] else None


SAID_FOR = 8 * 86400    # seconds after its reset a week's line is remembered (a week has passed by then)
SAME_WEEK = 300         # two readings of one week carry reset times a few seconds apart


def note_history(st, now=None, tz=None):
    """Once in History: each account Switchboard keeps whose weekly limit, at the pace of its use so far, would be full
    before it refills (full_at), and was not said for this week yet, gets one line ("EMAIL: at this pace, its week is
    full by Thu 2:00 PM, before it refills"). `st` is the page's state, under its lock. Returns how many lines."""
    from . import core, names, term
    now = time.time() if now is None else now
    said = st.get('pace_said') if isinstance(st.get('pace_said'), dict) else {}
    said = dict((k, v) for k, v in said.items() if _num(v) and now - v < SAID_FOR)
    wrote = 0
    for key, a in sorted((st.get('accounts') or {}).items()):
        if not isinstance(a, dict) or not a.get('connected') or a.get('unmanaged'):
            continue
        t = full_at(a, now)
        if t is None:
            continue
        resets = parse_iso((((a.get('usage') or {}).get('weekly')) or {}).get('resets'))
        if key in said and abs(said[key] - resets) < SAME_WEEK:
            continue
        said[key] = resets
        words = 'full by %s' % term.clock(t, now, tz)
        core.event(st, '%s: at this pace, its week is %s, before it refills' % (key, words), 'info',
                   account=names.email_of(key), window='weekly', reason='at this pace, %s' % words)
        wrote += 1
    st['pace_said'] = said
    return wrote
