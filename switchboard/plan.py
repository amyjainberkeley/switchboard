"""The order automatic switching will use the accounts in, and when the week runs out: what the page, the menu bar
and the terminal show as the order and as Next.

Pure functions: no files, no network, no clock. Pass `now` in. Percentages are account percent points (0 to 100)
as Anthropic's usage page shows them; times are epoch seconds.

An account is a dict:
    name, weekly_used_pct, weekly_resets_at, five_hour_used_pct (or None), five_hour_resets_at (or None),
    scoped: [{name, used_pct, resets_at}]   per-model weekly windows, if any
    disabled (it can't be switched to: not kept here), needs_login, active, trusted (False: its reading is too old or
    can't be proved, so it is not known), rule ('last': used last; 'out': kept out of auto-switch; none: as usual),
    session_line and weekly_line (its own lines, when it has them; else the settings' own), rank (1 is used first;
    none: after every ranked account)
Settings, as automatic switching has them (accounts.api.auto_settings):
    session_line (the 5-hour line, 90), weekly_line (95), model_line (95) and models (the model weeks that count:
    the model in use, e.g. ['Fable']; none when empty), strategy ('refills-first': what refills first, or
    'most-room'), margin (5: never land within this many points of a line).

Our own rule, the same one the engine's policy keeps (policy.py):
- Each window is measured against its own line: the 5-hour one against the 5-hour line, the weekly one against the
  weekly line, a counted model's week against the model line. A model week that is not counted never moves anyone.
- An account is a landing place when it can be switched to, is signed in, has a reading it can be trusted on, and
  every counted window has at least `margin` points of room under its line (and none is at 100%).
- The account in use stays first while every counted window is under its line.
- The landing places, in order: refills-first takes the one whose week refills soonest (its weekly reset, else its
  counted model week's; unknown resets go last), then the most room; most-room takes the most room (the least room
  over its counted windows), then the soonest refill. Equal keys keep the order the accounts were given in.
- With no landing place, nothing is next: automatic switching stays where it is and says when the first account is
  back (the latest reset among the windows that hold it back).
- An account kept out of auto-switch (rule 'out') is never a landing place (reason 'kept out of auto-switch'), and
  while it is the account in use nothing is next: automatic switching never moves Claude Code away from it. An
  account used last (rule 'last') comes after every other landing place and is still one: Next names it when no
  other account has room, as the policy lands on it before it would run any account past its line (an escape,
  which this order never shows). Among the accounts that are not landing places, those used last come after the
  others, and those kept out after all. The account in use keeps the place it has with no rule, whatever its rule
  (first while under its lines, else by when it is back).
- An account with lines of its own is judged by them in place of the settings' lines. Among the landing places that
  are not used last (and then among those that are), a lower rank comes first, and the order decides inside one rank;
  an account with no rank comes after every ranked one. 'stay-then-refills' orders the landing places as
  refills-first does (it differs only in when a switch goes, which the policy decides).
"""

WEEK = 7 * 86400
HOUR = 3600.0
STRATEGIES = ('refills-first', 'most-room', 'stay-then-refills')
DEFAULTS = {'session_line': 90.0, 'weekly_line': 95.0, 'model_line': 95.0, 'models': (), 'strategy': 'refills-first',
            'margin': 5.0}
INF = float('inf')
KEPT_OUT = 'kept out of auto-switch'


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v not in (INF, -INF)


def _settings(settings):
    s = dict(DEFAULTS)
    given = settings or {}
    for k in ('session_line', 'weekly_line', 'model_line', 'margin'):
        if _num(given.get(k)):
            s[k] = float(given[k])
    if given.get('strategy') in STRATEGIES:
        s['strategy'] = given['strategy']
    models = given.get('models', DEFAULTS['models'])
    if isinstance(models, str):
        models = [models]
    s['models'] = tuple(m.strip().lower() for m in models or () if isinstance(m, str) and m.strip()
                        and m.strip().lower() != 'none')
    return s


def _week(used, resets_at, now):
    """(used, the reset shown, True when that reset is a guess) for a weekly-type window. A reading keeps the number it
    reported until a new one comes; a reset already passed is rolled on by whole weeks for display only."""
    if not _num(resets_at):
        return used, now + WEEK, True
    if resets_at <= now:
        return used, resets_at + (int((now - resets_at) // WEEK) + 1) * WEEK, True
    return used, resets_at, False


def _ahead(t, now):
    """A reset as the order uses it: when it is still ahead, else last."""
    return t if _num(t) and t > now else INF


def _windows(a, s, now):
    """Every window that counts for this account: [{name, used, line, resets_at, refill}]."""
    out = []
    used, resets = a.get('five_hour_used_pct'), a.get('five_hour_resets_at')
    own = dict((k, float(a[k])) for k in ('session_line', 'weekly_line') if _num(a.get(k)))
    if _num(used):
        out.append({'name': 'session', 'used': float(used), 'line': own.get('session_line', s['session_line']),
                    'resets_at': resets if _num(resets) and resets > now else None, 'refill': INF})
    used, resets, _ = _week(a.get('weekly_used_pct'), a.get('weekly_resets_at'), now)
    if _num(used):
        out.append({'name': 'weekly', 'used': float(used), 'line': own.get('weekly_line', s['weekly_line']),
                    'resets_at': resets,
                    'refill': _ahead(a.get('weekly_resets_at'), now)})
    for w in a.get('scoped') or []:
        if not isinstance(w, dict) or not _num(w.get('used_pct')):
            continue
        name = (w.get('name') or '').strip()
        if not name or name.lower() not in s['models']:
            continue
        used, resets, _ = _week(w.get('used_pct'), w.get('resets_at'), now)
        out.append({'name': name, 'used': float(used), 'line': s['model_line'], 'resets_at': resets,
                    'refill': _ahead(w.get('resets_at'), now)})
    for w in out:
        w['room'] = w['line'] - w['used']
    return out


def _judge(a, s, now):
    """One account and what the order compares: room (the least room under its lines), refill (when its week
    refills, for refills-first), and why it is not a landing place (None when it is)."""
    wins = _windows(a, s, now)
    weekly = next((w for w in wins if w['name'] == 'weekly'), None)
    refill = weekly['refill'] if weekly else min([w['refill'] for w in wins if w['name'] != 'session'] or [INF])
    _, resets_at, estimated = _week(a.get('weekly_used_pct'), a.get('weekly_resets_at'), now)
    j = dict(a, windows=wins, room=min(w['room'] for w in wins) if wins else None, refill=refill,
             resets_at=resets_at, reset_estimated=estimated)
    if a.get('disabled'):
        j['reason'] = 'not kept here'
    elif a.get('rule') == 'out' and not a.get('active'):     # the account in use is judged on its numbers, as today
        j['reason'] = KEPT_OUT
    elif a.get('needs_login'):
        j['reason'] = 'needs sign-in'
    elif a.get('trusted') is False:
        j['reason'] = 'no trusted reading'
    elif not wins:
        j['reason'] = 'no reading yet'
    elif any(w['used'] >= 100 for w in wins):
        j['reason'] = 'at its limit'
    elif any(w['used'] >= w['line'] for w in wins):
        j['reason'] = 'over its line'
    elif any(w['room'] < s['margin'] for w in wins):
        j['reason'] = 'not enough room'
    else:
        j['reason'] = None
    return j


def _key(j, s):
    if s['strategy'] == 'most-room':
        return (-j['room'], j['refill'])
    return (j['refill'], -j['room'])


def _rank_key(j):
    """A lower rank first, then no rank (every account alike when none has one)."""
    r = j.get('rank')
    return r if isinstance(r, int) and not isinstance(r, bool) and r >= 1 else INF


def _blocking(j, s):
    """The windows that keep an account from being a landing place, and when they are all back (None if not known)."""
    if j['reason'] == 'at its limit':
        blocked = [w for w in j['windows'] if w['used'] >= 100]
    elif j['reason'] in ('over its line', 'not enough room'):
        blocked = [w for w in j['windows'] if w['room'] < s['margin']]
    else:
        blocked = []
    back = max(w['resets_at'] for w in blocked) if blocked and all(_num(w['resets_at']) for w in blocked) else None
    return [w['name'] for w in blocked], back


def order(accounts, settings, now):
    """Every account, in the order automatic switching will use them (see the module notes).

    Each comes back as a copy with rank (1 first), usable (a landing place, or the account in use while it is under
    its lines), reason (None, or why not: 'not kept here', 'kept out of auto-switch', 'needs sign-in', 'no trusted
    reading', 'no reading yet', 'at its limit', 'over its line', 'not enough room'), left_pct (points under its nearest
    counted line, never below 0), resets_at (its weekly reset as shown) and reset_estimated, blocked_by and back_at
    (when what holds it back has all refilled, if known), and fallback (always False: nothing is landed on over a
    line)."""
    s = _settings(settings)
    judged = [_judge(a, s, now) for a in accounts]
    active = next((j for j in judged if j.get('active')), None)
    pinned = [active] if active is not None and active['windows'] and active['reason'] in (None, 'not enough room') \
        and all(w['used'] < w['line'] for w in active['windows']) else []
    landing = sorted((j for j in judged if j is not active and j['reason'] is None),
                     key=lambda j: (j.get('rule') == 'last', _rank_key(j)) + _key(j, s))
    placed = set(id(j) for j in pinned + landing)
    rest = [j for j in judged if id(j) not in placed]
    for j in rest:
        j['blocked_by'], j['back_at'] = _blocking(j, s)
    ruled = lambda j, r: j.get('rule') == r and not j.get('active')      # noqa: E731  (in use: today's place)
    rest.sort(key=lambda j: (ruled(j, 'out'), ruled(j, 'last'), j['back_at'] is None, j['back_at'] or 0))
    out = []
    for i, j in enumerate(pinned + landing + rest):
        resting = id(j) not in placed
        x = dict((k, v) for k, v in j.items() if k not in ('windows', 'room', 'refill', 'blocked_by', 'back_at'))
        x.update(rank=i + 1, usable=not resting, fallback=False, reason=j['reason'] if resting else None,
                 left_pct=None if j['room'] is None else max(0.0, j['room']),
                 blocked_by=j['blocked_by'] if resting else [], back_at=j['back_at'] if resting else None)
        out.append(x)
    return out


def next_account(accounts, settings, now):
    """{'account': the account automatic switching goes to next (the first landing place that is not the one in
    use), 'reason': None, 'back_at': None}, or {'account': None, 'reason': why, 'back_at': when one is back}. The
    account in use kept out of auto-switch: nothing is next, reason 'kept out of auto-switch'."""
    ranked = order(accounts, settings, now)
    if any(j.get('active') and j.get('rule') == 'out' for j in ranked):
        return {'account': None, 'reason': KEPT_OUT, 'back_at': None}
    others = [j for j in ranked if not j.get('active')]
    for j in others:
        if j['usable']:
            return {'account': j, 'reason': None, 'back_at': None}
    if not others:
        return {'account': None, 'reason': 'no other account', 'back_at': None}
    backs = [j['back_at'] for j in others if _num(j['back_at'])]
    return {'account': None, 'reason': 'no other account has room', 'back_at': min(backs) if backs else None}


OPEN_AT_LEAST = 12 * 3600       # the week ahead needs the window open this long ...
USED_AT_LEAST = 10              # ... and this much of it used, or it says nothing


def week_ahead(used, resets_at, read_at, now):
    """When this week's allowance runs out at this week's own average pace: the percent used so far, over the
    time since the window opened (seven days before its reset), carried on to 100%.

    used and read_at are one reading (percent used, and when it was read); resets_at is that window's reset.
    Returns None (say nothing) until the window has been open 12 hours and 10% is used, when the reading is
    already at 100%, from the future, or past its reset, or when the projection is already behind the clock.
    Otherwise {'lasts': True, 'out_at': None} when 100% comes at or after the reset, else {'lasts': False,
    'out_at': when}."""
    if not (_num(used) and _num(resets_at) and _num(read_at)) or resets_at <= now or read_at > now + 5:
        return None
    elapsed = read_at - (resets_at - WEEK)
    if elapsed < OPEN_AT_LEAST or used < USED_AT_LEAST or used >= 100:
        return None
    out = read_at + (100.0 - used) * elapsed / used
    if out >= resets_at:
        return {'lasts': True, 'out_at': None}
    if out <= now:
        return None
    return {'lasts': False, 'out_at': out}


def wasted_if(accounts, sequence, now, rate_per_hour, settings=None):
    """Weekly allowance, in account-percent points, that refills unused if you work through the accounts in
    `sequence` (names, or entries with a 'name') at rate_per_hour, against the refills-first order. For the page's
    one-line explanation.

    The model: starting now, you use one account at a time, each until its room under the weekly line is gone or it
    resets, then the next. Only landing places are drawn from; the room of the others (and of landing places left out
    of `sequence`) refills unused either way. 5-hour windows are not modelled. Returns None without a positive rate."""
    if not _num(rate_per_hour) or rate_per_hour <= 0:
        return None
    s = _settings(settings)
    ranked = order(accounts, dict(s, strategy='refills-first'), now)
    by_name = dict((j.get('name'), j) for j in ranked)
    best = [j.get('name') for j in ranked if j['usable']]
    given = [x.get('name') if isinstance(x, dict) else x for x in sequence]

    def run(names):
        t, used_up, seen = now, 0.0, set()
        for name in names:
            j = by_name.get(name)
            if j is None or not j['usable'] or name in seen:
                continue
            seen.add(name)
            room = max(0.0, s['weekly_line'] - _week(j.get('weekly_used_pct'), j.get('weekly_resets_at'), now)[0])
            if t >= j['resets_at']:
                continue
            spend = min(room, rate_per_hour * (j['resets_at'] - t) / HOUR)
            used_up += spend
            t += spend / rate_per_hour * HOUR
        return used_up

    total = 0.0
    for j in ranked:
        used = _week(j.get('weekly_used_pct'), j.get('weekly_resets_at'), now)[0]
        if not j.get('disabled') and _num(used):
            total += max(0.0, s['weekly_line'] - used)
    wasted, least = total - run(given), total - run(best)
    return {'wasted': wasted, 'best': least, 'extra': wasted - least, 'order': given, 'best_order': best,
            'rate_per_hour': rate_per_hour}
