"""What `switchboard` prints in a terminal: the accounts and their limits, and the lines of the first run.

Pure functions. The page's own state goes in (GET /api/state, made by server.render, so the terminal and the page
read the same numbers and the same "next"); lines of text come out. No files, no network, no clock: `now` comes
from the state. What it prints:

  Claude Code is on maya@northwind.example · auto-switch on at 90% 5-hour · 95% weekly

  1  maya@northwind.example  Max 20x · Northwind  in use
     5-hour  ━━━━━━━━━━━━━━━─────────   62%  resets 6:10 PM · in 2 hours
     weekly  ━━━━━━━━━━━━━━━━━───────   70%  resets Mon 12:00 PM · in 3 days

An account whose sign-in ends within the week, or has ended, says when after its age ('· Sign-in ends', a day and a time).

Colour only on a terminal and never with NO_COLOR set: blue for the account in use, red at or over a switch line
(red beats blue), dim for what is said quietly, bold for emails. A narrow terminal gets narrower bars.

An account has at most two limit lines: 5-hour only when there is a 5-hour number to show, weekly always (a dash when
not known). A model's own week has no line; one auto-switch counts that is at or over its line is a short red note at
the end of the weekly line ('Fable 97%').

When, at the pace of its use so far, an account's weekly limit would be full before it refills (pace.week), its weekly
line says when after its reset words ('· at this pace, full by Sat 4:23 PM'). When it lasts, nothing is said.
"""
import math
import re
from datetime import datetime

from . import pace, spend
from .core import parse_iso

BAR = 24                # cells in a bar
MIN_BAR = 8             # the narrowest a bar gets on a narrow terminal
LABEL = 6               # '5-hour', 'weekly', or a model's name, padded to this
MODEL_LINE = 95         # a model's own week (Fable) turns red at its line, 95% unless set
USED, LEFT = '━', '─'      # a bar: the part used, then the rest
SEP = ' · '
ARROW, DASH, CHECK, RANGE = '›', '—', '✓', '–'

BLUE, RED, DIM, BOLD = '94', '91', '2', '1'
TEAM_TYPES = ('claude_team', 'claude_enterprise')
PERSONAL_ORG = re.compile(r"['\u2019]s organi[sz]ation$", re.I)
DAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')


# ---------- small things ----------

def jsround(x):
    """Halves go up, as on the page (Math.round). Python's round() takes them to the even number."""
    return int(math.floor(x + 0.5))


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v


def _plural(n, word):
    return '%d %s%s' % (n, word, '' if n == 1 else 's')


def colour_ok(stream, environ):
    """Colour only on a terminal, and never with NO_COLOR set (or on a terminal that says it is dumb)."""
    if environ.get('NO_COLOR') or environ.get('TERM') == 'dumb':
        return False
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def paint(segs, colour):
    """A line given as [(text, SGR code or None)], with the codes or without."""
    if not colour:
        return ''.join(t for t, _ in segs)
    return ''.join('\x1b[%sm%s\x1b[0m' % (s, t) if s and t else t for t, s in segs)


def width_of(segs):
    return sum(len(t) for t, _ in segs)


def number(x):
    """A switch line as the page writes it: 90, 92.5."""
    return '%g' % x


# ---------- times ----------

def _minute(t):
    """To the nearest minute: Anthropic sends a reset as 11:59:59.6, which is noon."""
    return jsround(t / 60.0) * 60


def clock(t, now, tz=None):
    """'6:10 PM' today, 'Sat 12:00 PM' on any other day, in this Mac's time zone (or `tz`)."""
    d = datetime.fromtimestamp(_minute(t), tz)
    text = '%d:%02d %s' % (d.hour % 12 or 12, d.minute, 'AM' if d.hour < 12 else 'PM')
    return text if d.date() == datetime.fromtimestamp(now, tz).date() else '%s %s' % (DAYS[d.weekday()], text)


def until(t, now):
    """'in 40 min', 'in 3 hours', 'in 1 day': the nearest whole unit."""
    m = (_minute(t) - now) / 60.0
    if jsround(m) < 60:
        return 'in %d min' % max(1, jsround(m))
    h = jsround(m / 60.0)
    if h < 24:
        return 'in ' + _plural(h, 'hour')
    return 'in ' + _plural(jsround(m / 1440.0), 'day')


def ago(minutes):
    """'10 min'; an hour or more: '2 hours'; a day or more: '3 days' (each the nearest whole number)."""
    m = max(0.0, minutes)
    if m < 60:
        return '%d min' % jsround(m)
    if m < 1440:
        return _plural(jsround(m / 60.0), 'hour')
    return _plural(jsround(m / 1440.0), 'day')


AHEAD = 5               # seconds: a reading dated further than this past the clock gets no age


def age(at, now):
    """How long ago a reading was taken: 'just now' (under a minute), '3 min ago', '2 hours ago', '3 days ago'. ''
    when its time is not known, or is ahead of the clock."""
    if not _num(at) or not _num(now) or at - now > AHEAD:
        return ''
    if now - at < 60:
        return 'just now'
    return '%s ago' % ago((now - at) / 60.0)


def checked_at(a):
    """When an account was last checked: the last read Anthropic (or Claude Code's status line) answered for it, its
    checked_at, which is never older than its numbers' own time (usage_at); either when the other is not known."""
    times = [t for t in (a.get('checked_at'), a.get('usage_at')) if _num(t)]
    return max(times) if times else None


def age_words(a, now):
    """How old an account's numbers are, the same on the page (ageWords), in the menu bar and here: 'now' for the
    account in use while they are Claude Code's own from the last minute (the server's live mark), else
    'checked just now', 'checked 3 min ago' (checked_at). Each account's own reading, never the server's tick: a tick
    a minute ago can sit over a reading ten minutes old. '' when the reading's time is not known."""
    if a.get('live') is True and a.get('active'):
        return 'now'
    when = age(checked_at(a), now)
    if when and a.get('elsewhere'):
        return 'from another Mac, ' + when        # read there, taken here (readings.py): its age is its real age
    return 'checked ' + when if when else ''


# ---------- when a sign-in ends ----------
#
# Anthropic ends each sign-in by a set date: a renewal keeps that date, and only a new sign-in moves it (each saved
# login's refresh_expires_at, the page's signin_ends_at). That date is the latest the sign-in ends: Claude Code itself
# says it can end any time from SIGNIN_WINDOW before it ("Sign-in ends: between <that date less 3 days> and <that
# date>", `claude auth status --text`, read in Claude Code 2.1.329 and 2.1.330), so the words say "by" and never
# promise the moment. Every surface says it in these words, from a week ahead, in this Mac's own time zone: the page
# (signinWords in index.html, held to these by a test), the menu bar, this list, doctor and the notifications.

SIGNIN_SOON = 7 * 86400     # said from this long before the date (the engine calls such a login 'expiring')
SIGNIN_WINDOW = 3 * 86400   # a sign-in can end any time from this long before its date (Claude Code's own rule)
SIGNIN_NEAR = 3 * 86400     # at the top of the page and in a notification from this close to the earliest end
SIGNIN_WHY = 'Anthropic ends each sign-in by a set date. Renewing does not move it; signing in again does.'
# Signing in again, on every surface: the page's one button signs in again to each account that needs it, one after
# another, the account Claude Code is on too (signins.py); `switchboard signin` does the same through the page.
SIGNIN_ALL = 'Sign in to all'
SIGNIN_TODO = 'Open Switchboard and press Sign in to all.'


def _when_ok(t):
    """A sane time: after 1970 and before the year 5000. A corrupt saved date (negative, zero, huge) is not known."""
    return _num(t) and math.isfinite(t) and 0 < t < 1e11


def _hour_minute(t, tz=None):
    d = datetime.fromtimestamp(t, tz)
    return '%d:%02d %s' % (d.hour % 12 or 12, d.minute, 'AM' if d.hour < 12 else 'PM')


def signin_day(t, now, tz=None):
    """t's day as the sign-in words name it, counted in calendar days of this Mac's time zone (or `tz`): 'today',
    'tomorrow', 'yesterday'; a weekday's short name for another day under a week away, either way; else a month's
    short name and the day of the month."""
    d, n = datetime.fromtimestamp(t, tz).date(), datetime.fromtimestamp(now, tz).date()
    days = (d - n).days
    if -1 <= days <= 1:
        return ('yesterday', 'today', 'tomorrow')[days + 1]
    if -7 < days < 7:
        return DAYS[d.weekday()]
    return '%s %d' % (MONTHS[d.month - 1], d.day)


def signin_earliest(at):
    """The earliest a sign-in whose date is `at` can end (SIGNIN_WINDOW before it), or None when `at` is not known."""
    return at - SIGNIN_WINDOW if _when_ok(at) else None


def signin_words(at, now, needs_login=False, tz=None, short=False):
    """'Sign-in ends by ' and a day and a time for a sign-in whose date is within 7 days: 'today', 'tomorrow' or a
    weekday's short name, then the time (a date a week ahead, on today's weekday, is named by its month and day
    instead); 'Sign-in ended by ' and a day and a time for one whose date passed and so needs a sign-in ('today' at the
    latest), or over 6 days back its month and day alone. "by": the date is the latest it ends (SIGNIN_WINDOW). '' for
    a date over 7 days away or not known (term._when_ok), and for an account that needs a sign-in for another reason
    (refused before its date). short, for the menu bar: 'Sign-in ends by ' and the day alone, or 'ended by ' and the
    day. The time to its nearest minute, as clock()."""
    if not _when_ok(at) or not _when_ok(now):
        return ''
    t = _minute(at)
    if at > now:
        if needs_login or at - now > SIGNIN_SOON:
            return ''
        day = signin_day(t, now, tz)
        return 'Sign-in ends by ' + (day if short else '%s %s' % (day, _hour_minute(t, tz)))
    if not needs_login:
        return ''
    t = min(t, now - now % 60)          # ended: never a minute later than now's, so never "tomorrow" near midnight
    day = signin_day(t, now, tz)
    if short:
        return 'ended by ' + day
    return 'Sign-in ended by ' + (day if ' ' in day else '%s %s' % (day, _hour_minute(t, tz)))


def signin_line(a, now, tz=None):
    """An account's words (the page's state: signin_ends_at, needs_login), the same for the account Claude Code is on
    as for any other: it signs in again from the page like the rest. '' when there is nothing to say."""
    return signin_words(a.get('signin_ends_at'), now, bool(a.get('needs_login')), tz)


def signin_count(ended, soon):
    """How many sign-ins have ended and how many end this week, as the note above the page's accounts says it
    (signinCount in index.html): '2 sign-ins end this week.', '1 sign-in has ended and 2 end this week.'"""
    e = '1 sign-in has ended' if ended == 1 else '%d sign-ins have ended' % ended
    if not soon:
        return e + '.'
    if not ended:
        return ('1 sign-in ends' if soon == 1 else '%d sign-ins end' % soon) + ' this week.'
    return '%s and %d %s this week.' % (e, soon, 'ends' if soon == 1 else 'end')


# ---------- words ----------

def _text(v):
    return v.strip() if isinstance(v, str) else ''


def plan_words(a):
    """'Max 20x · Personal', 'Team · Northwind': the server's own words for it (plan_line) when it sends them, else
    worked out the way it works them out: a Team or Enterprise seat is that plan; otherwise the tier names Max 20x and
    Max 5x ("Ultra" is Anthropic's newer name for Max 20x), else the plan's own name (a login's own word for it, such
    as 'max', with its capital). Then the organization, Personal for an account's own ("you@mail.example's
    Organization")."""
    if _text(a.get('plan_line')):
        return _text(a.get('plan_line'))
    p = a.get('plan')
    label, tier = (p.get('label') or p.get('planDisplayName'), p.get('tier')) if isinstance(p, dict) else (p, None)
    label, tier, kind = _text(label), _text(a.get('tier') or tier).lower(), _text(a.get('org_type'))
    if label.isalpha() and label.islower():             # a login's own word for the plan ('max', 'team'): its capital
        label = label.capitalize()
    if kind in TEAM_TYPES:
        plan = label or ('Team' if kind == 'claude_team' else 'Enterprise')
    elif tier.endswith('max_20x'):
        plan = 'Max 20x'
    elif tier.endswith('max_5x'):
        plan = 'Max 5x'
    else:
        plan = 'Max 20x' if label == 'Ultra' else label
    org = _text(a.get('org'))
    return SEP.join(x for x in (plan, 'Personal' if PERSONAL_ORG.search(org) else org) if x)


def window(w, now, line, label, tz=None):
    """One limit: {label, pct (whole number, or None when not known), words, t (its reset), over}."""
    if not isinstance(w, dict):                         # no window open: Claude reports none
        return {'label': label, 'pct': 0, 'words': 'not started', 't': None, 'over': False}
    if not _num(w.get('pct')):                          # a window with no number: not known
        return unknown(label)
    pct = jsround(w['pct'])
    t = parse_iso(w.get('resets'))
    if t is None:                                       # 0% with no reset: how Anthropic reports one not started
        words = 'not started' if w['pct'] == 0 else 'started'
    elif t <= now:
        words = 'not started'
    else:
        words = 'resets %s%s%s' % (clock(t, now, tz), SEP, until(t, now))
    return {'label': label, 'pct': pct, 'words': words, 't': t, 'over': pct >= line}


def unknown(label):
    return {'label': label, 'pct': None, 'words': '', 't': None, 'over': False}


def counts(models, name):
    """Does automatic switching count this model's own week? `models` is the state's auto['models']: the engine counts
    the one it names, and 'Fable' counts 'Fable 5.2' too (accounts/policy.same_model). None, a state from before they
    were named: every model counts, as it did."""
    if models is None:
        return True
    want = ' '.join(str(name or '').lower().split())
    for m in models if isinstance(models, (list, tuple)) else ():
        have = ' '.join(str(m).lower().split())
        if have and want and (have == want or have.startswith(want + ' ') or want.startswith(have + ' ')):
            return True
    return False


def limits(a, now, line5, line_w, tz=None, line_m=MODEL_LINE, models=None):
    """At most two lines, as on the page: 5-hour, then weekly, each against its own line. The 5-hour line only when
    there is a 5-hour number to show: none for an account with no 5-hour limit (Ultra: its reading has no 5-hour
    window), for a 5-hour window sent with no number, and for a reading not known (too old to show: trusted False; or
    never read). The weekly line always, not known as a dash. A model's own week has no line: the weekly limit carries
    `note` (over_note), the counted model week at or over its line, or None."""
    u = a.get('usage') if a.get('trusted') is not False else None
    if not isinstance(u, dict):
        return [dict(unknown('weekly'), note=None, pace='')]
    week = dict(window(u.get('weekly'), now, line_w, 'weekly', tz), note=over_note(u, line_m, models),
                pace=pace_words(a, now, tz))
    five = u.get('session')
    shown = isinstance(five, dict) and _num(five.get('pct'))
    cost = spend.shown(u, five['pct'] if shown else None)
    if cost is not None:                    # a row has two lines at most: the spend limit stands in for the 5-hour one
        return [week, spend_line(cost, now, tz)]
    return ([window(five, now, line5, '5-hour', tz)] if shown else []) + [week]


def spend_line(s, now, tz=None):
    """A spend limit as a limit line (spend.py): its percent and bar, then '$412 of $1,000 · resets Dec 1'. No switch
    line counts it, so it is never red."""
    t = parse_iso(s['resets'])
    when = ''
    if t is not None and t > now:
        d = datetime.fromtimestamp(_minute(t), tz)
        when = 'resets ' + (clock(t, now, tz) if t - now < 6 * 86400 else '%s %d' % (MONTHS[d.month - 1], d.day))
    words = SEP.join(x for x in (spend.amounts(s), when) if x)
    return {'label': 'spend', 'pct': jsround(s['pct']), 'words': words, 't': t, 'over': False}


def pace_words(a, now, tz=None):
    """'at this pace, full by Sat 4:23 PM': the weekly limit is full before it refills at the pace of its use so far
    (pace.week, the one rule for every surface); '' when it lasts, or when too little is known to say."""
    t = pace.full_at(a, now)
    return 'at this pace, full by %s' % clock(t, now, tz) if t is not None else ''


# A model week auto-switch counts, at or over its switch line, is said at the end of the weekly line (term and menu
# bar): its name and its percent. False takes the note out of both; the page has its own (index.html,
# MODEL_OVER_NOTE).
MODEL_OVER_NOTE = True


def over_note(u, line_m=MODEL_LINE, models=None):
    """{label, pct}: the model week auto-switch counts (`models`: counts) that is at or over its line (`line_m`), the
    highest when several are; None when there is none, or when MODEL_OVER_NOTE is off. This is why auto-switch treats
    an account as full while its weekly limit is under its own line."""
    if not MODEL_OVER_NOTE:
        return None
    best = None
    for x in (u or {}).get('scoped') or []:
        if not isinstance(x, dict) or not _num(x.get('pct')) or not counts(models, x.get('name')):
            continue
        pct = jsround(x['pct'])
        if pct >= line_m and (best is None or pct > best['pct']):
            best = {'label': str(x.get('name') or 'Model'), 'pct': pct}
    return best


def has_five(accounts):
    """Does any account Switchboard keeps have a 5-hour limit? Its last reading has a 5-hour window, shown or not (a
    reading too old to show still tells the plan). The switch line names the 5-hour line only then."""
    for a in accounts or []:
        u = a.get('usage') if isinstance(a, dict) and not a.get('unmanaged') else None
        if isinstance(u, dict) and isinstance(u.get('session'), dict):
            return True
    return False


# ---------- the state, as the terminal shows it ----------

def model_words(auto):
    """', Fable too': the model week auto-switch also switches on, by name, as `switchboard auto` says it (auto_line);
    the page names it in Settings (index.html, modelNames). '' when it counts none."""
    said = [str(m) for m in auto.get('models')] if isinstance(auto.get('models'), (list, tuple)) else []
    said = [m for m in said if m.strip() and m.lower() not in ('all', 'none')]
    if not said:
        return ''
    return ', %s too' % (said[0] if len(said) == 1 else '%s and %s' % (', '.join(said[:-1]), said[-1]))


def read(view, live=None, tz=None):
    """The page's state as rows: {'head': {email, auto, line5, line_w, models}, 'accounts': [{slot, email, plan, active,
    next, needs_login, unmanaged, age, rule ('last', 'out' or '': its rule in auto-switch), limits, token (token_words
    of a one-year token kept for it, or '')}],
    'now'}. `live` is the email Claude Code is signed in to (for the header when no account here is in use)."""
    now = view.get('now') if _num(view.get('now')) else 0
    auto = view.get('auto') or {}
    line5 = auto.get('session_at') if _num(auto.get('session_at')) else 90
    line_w = auto.get('weekly_at') if _num(auto.get('weekly_at')) else 95
    line_m = auto.get('model_at') if _num(auto.get('model_at')) else MODEL_LINE
    on = bool(auto.get('enabled'))
    rules = auto.get('rules') if isinstance(auto.get('rules'), dict) else {}
    own_lines = auto.get('own_lines') if isinstance(auto.get('own_lines'), dict) else {}
    # Next: the server marks it (only while automatic switching runs); an older server leaves it to the plan.
    nxt = view.get('next') if 'next' in view else (view.get('plan') or {}).get('next') if auto.get('on') else None
    accounts = []
    # a one-year token kept for an account: on that account's row, matched by its id (two accounts can share an email)
    tokens = dict((t.get('for_id'), t) for t in view.get('tokens') or [] if isinstance(t, dict) and t.get('for_id'))
    for a in sorted(view.get('accounts') or [], key=lambda a: (a.get('slot') or 99, a.get('email') or '')):
        email = a.get('email') or ''
        active, out = bool(a.get('active')), bool(a.get('needs_login')) or bool(a.get('unmanaged'))
        accounts.append({'slot': a.get('slot'), 'email': email, 'plan': plan_words(a), 'active': active,
                         'needs_login': bool(a.get('needs_login')), 'next': on and email == nxt and not active and not out,
                         'unmanaged': bool(a.get('unmanaged')), 'age': age_words(dict(a, active=active), now),
                         'rule': rules.get(a.get('id')) if rules.get(a.get('id')) in RULE_NOTES else '',
                         'signin': '' if a.get('unmanaged') else signin_line(dict(a, active=active), now, tz),
                         'limits': limits(a, now, *own_lines_of(own_lines.get(a.get('id')), line5, line_w),
                                          tz=tz, line_m=line_m, models=auto.get('models')),
                         'token': token_words(tokens.get(a.get('id')) if a.get('id') else None, tz)})
    active = next((a['email'] for a in accounts if a['active']), None) or view.get('active') or live
    return {'head': {'email': active, 'auto': on, 'line5': line5, 'line_w': line_w, 'models': model_words(auto),
                     'five': has_five(view.get('accounts'))},
            'accounts': accounts, 'now': now}


def own_lines_of(own, line5, line_w):
    """(5-hour line, weekly line) for one account: its own where it has them (the auto settings' 'own_lines'), else
    the shared ones."""
    own = own if isinstance(own, dict) else {}
    return (own['session_at'] if _num(own.get('session_at')) else line5,
            own['weekly_at'] if _num(own.get('weekly_at')) else line_w)


def token_words(t, tz=None):
    """What an account's own row says of a one-year token kept for it: 'also a one-year token, added Nov 25' (the token
    has no end date of its own, so only the day it was kept), or '' when it has none."""
    if not isinstance(t, dict):
        return ''
    return 'also a one-year token' + (', added %s' % day(t['added_at'], tz) if _when_ok(t.get('added_at')) else '')


# ---------- lines ----------

def header(head, width=None):
    """'Claude Code is on {email} · auto-switch on at 90% 5-hour · 95% weekly' (two lines when it does not fit). It
    names only the lines the rows show, as the page's switch line: the 5-hour one only when an account has a 5-hour
    limit (head['five']), never a model's week (`switchboard auto` and the page's Settings say that one)."""
    lead = [('Claude Code is on ', None), (head['email'], BLUE)] if head.get('email') else \
        [('Claude Code is not signed in', None)]
    five = '%s%% 5-hour%s' % (number(head['line5']), SEP) if head.get('five', True) else ''
    rest = 'auto-switch %s at %s%s%% weekly' % ('on' if head.get('auto') else 'off', five, number(head['line_w']))
    one = lead + [(SEP + rest, DIM)]
    return [one] if width is None or width_of(one) <= width else [lead, [(rest, DIM)]]


def status(a):
    """`in use`, `sign in again` or `next`, then its rule in auto-switch ('used last', 'kept out of auto-switch'), then
    how old the account's numbers are ('now', 'checked 3 min ago'), then when its sign-in ends or ended
    (signin_line)."""
    word = [('in use', BLUE)] if a['active'] else [('sign in again', None)] if a['needs_login'] else \
        [('next', DIM)] if a['next'] else []
    for said, colour in ((RULE_NOTES.get(a.get('rule')), DIM), (a['age'], DIM), (a.get('signin'), None),
                         (a.get('token'), DIM)):
        if said:
            word = word + [(SEP, DIM), (said, colour)] if word else [(said, colour)]
    return word


def _pad(n):
    return [(' ' * n, None)] if n > 0 else []


def head_lines(a, slot_w, email_w, plan_w, width=None):
    """The account's first line: slot, email, plan · org, status. On a narrow terminal what does not fit goes to
    the next line, under its own column when it fits there."""
    first = [(str(a['slot'] or '').rjust(slot_w) + '  ', None), (a['email'], BOLD)]
    plan = [(a['plan'], DIM)] if a['plan'] else []
    st = status(a)
    gap = [('  ', None)]
    col = slot_w + 2 + email_w + 2                      # where the plan column starts
    line = list(first)
    if plan or (st and plan_w):
        line += _pad(email_w - len(a['email'])) + gap + plan
    if st:
        line += (_pad(plan_w - len(a['plan'])) if plan_w else _pad(email_w - len(a['email']))) + gap + st
    if width is None or width_of(line) <= width:
        return [line]
    indent = slot_w + 2
    with_plan = first + _pad(email_w - len(a['email'])) + gap + plan
    if plan and width_of(with_plan) <= width:
        out = [with_plan]
        if st:
            out.append(_pad(col if col + width_of(st) <= width else indent) + st)
        return out
    out = [first]
    rest = plan + (gap + st if plan and st else st)
    if not rest:
        return out
    if indent + width_of(rest) <= width or not (plan and st):
        out.append(_pad(indent) + rest)
    else:
        out += [_pad(indent) + plan, _pad(indent) + st]
    return out


def cells(pct, bar):
    """How many cells of a bar are filled: round(used% of the bar), at least one when anything is used, all of it
    only at 100%."""
    if pct is None or pct <= 0:
        return 0
    if pct >= 100:
        return bar
    return min(bar - 1, max(1, jsround(pct / 100.0 * bar)))


def limit_line(lim, indent, label_w, bar, active, width=None):
    n = cells(lim['pct'], bar)
    fill = RED if lim['over'] else BLUE if active else None
    pct = DASH if lim['pct'] is None else '%d%%' % lim['pct']
    segs = [(' ' * indent + lim['label'].ljust(label_w) + '  ', None)]
    if n:
        segs.append((USED * n, fill))
    if bar - n:
        segs.append((LEFT * (bar - n), DIM))
    segs += [('  ' + ' ' * (4 - len(pct)), None), (pct, RED if lim['over'] else None)]
    words, said_pace = fit(lim, indent + label_w + 2 + bar + 6, width)
    if words:
        segs += [('  ', None), (words, DIM)]
    return segs + pace_segs(dict(lim, pace=said_pace), after=bool(words)) + note_segs(lim)


def said(lim, before=0, width=None):
    """A limit's reset words (fit)."""
    return fit(lim, before, width)[0]


PACE_LEAD = 'at this pace, '


def fit(lim, before=0, width=None):
    """(reset words, pace words) for a limit's line. On a line with pace words or a note that would not fit `width`
    (`before`: the columns ahead of the words), each step says less until it fits, the note always whole: the reset
    without how far off ('resets Mon 1:00 PM'), then the pace words without 'at this pace, ', then no reset words."""
    words, said_pace = lim['words'], lim.get('pace') or ''
    note = width_of(note_segs(lim))
    if width is None or not (said_pace or note) or not words:
        return words, said_pace
    short, bare = words.split(SEP)[0], said_pace.replace(PACE_LEAD, '', 1)
    steps = [(words, said_pace), (short, said_pace)] + ([(short, bare), ('', bare)] if said_pace else [])
    for w, p in steps:
        if before + (2 + len(w) if w else 0) + width_of(pace_segs({'pace': p}, after=bool(w))) + note <= width:
            return w, p
    return steps[-1]


def pace_segs(lim, after=True):
    """The pace words on a weekly line, plain (never red, never green): it would be full before it refills
    (pace_words). After the reset words (`after`), or in their place on a narrow terminal (fit)."""
    words = lim.get('pace') or ''
    return [(SEP if after else '  ', DIM), (words, None)] if words else []


def note_words(lim):
    """'Fable 97%': the weekly line's note (over_note), or ''."""
    n = lim.get('note')
    return '%s %d%%' % (n['label'], n['pct']) if n else ''


def note_segs(lim):
    """The note at the end of a limit's line, red: a model week auto-switch counts is at or over its line."""
    words = note_words(lim)
    return [('  ', None), (words, RED)] if words else []


def bar_width(accounts, indent, label_w, width=None):
    """24 cells, fewer on a narrow terminal (never under 8), the same for every bar so the numbers line up."""
    if width is None:
        return BAR
    # a line with a note or pace words may say less (fit): they never take room from every bar
    words = max([width_of(pace_segs({'pace': lim['pace'].replace(PACE_LEAD, '', 1)}, after=False) + note_segs(lim)) - 2
                 if lim.get('pace') else len(lim['words'].split(SEP)[0]) + width_of(note_segs(lim)) if lim.get('note')
                 else len(lim['words']) for a in accounts for lim in a['limits']] + [0])
    fixed = indent + label_w + 2 + 2 + 4 + (2 + words if words else 0)
    return max(MIN_BAR, min(BAR, width - fixed))


def view(state, width=None, colour=False):
    """Every line of the view (the header, a blank line, then each account and its limits, a blank line between
    accounts), with no prompt."""
    accounts = state['accounts']
    out = [paint(x, colour) for x in header(state['head'], width)]
    if not accounts:
        return out
    slot_w = max(len(str(a['slot'] or '')) for a in accounts)
    email_w = max(len(a['email']) for a in accounts)
    plan_w = max(len(a['plan']) for a in accounts)
    indent = slot_w + 2
    label_w = max([LABEL] + [len(lim['label']) for a in accounts for lim in a['limits']])
    bar = bar_width(accounts, indent, label_w, width)
    for a in accounts:
        out.append('')
        out += [paint(x, colour) for x in head_lines(a, slot_w, email_w, plan_w, width)]
        out += [paint(limit_line(lim, indent, label_w, bar, a['active'], width), colour) for lim in a['limits']]
    return out


def prompt(slots, colour=False):
    """'Switch to 1–5, or Enter to stay › '"""
    lo, hi = min(slots), max(slots)
    which = str(lo) if lo == hi else '%d%s%d' % (lo, RANGE, hi)
    return paint([('Switch to %s, or Enter to stay ' % which, None), (ARROW, DIM), (' ', None)], colour)


# ---------- the first run, adding accounts, switching ----------

def _accounts(n):
    return _plural(n, 'account')


def another_account(colour=False):
    """Under the view, with one account: how to add the next one."""
    return run_this('Add another account with', 'switchboard add', colour)


def kept(email):
    """Bare `switchboard` or `switchboard status` kept the login Claude Code is on: 'Kept maya@ivy.example.'"""
    return 'Kept %s.' % email


AUTO_ON = ('Auto-switch is on: Claude Code moves to another account before a limit. Turn it off with '
           'switchboard auto off.')
ORDER_WORDS = {'refills-first': 'using what refills first', 'most-room': 'using the one with the most room',
               'stay-then-refills': 'staying on the account in use until a line, then using what refills first'}
# An account's rule in auto-switch, as a note on its line (the page's row says the same) and as one line of its own
RULE_NOTES = {'last': 'used last', 'out': 'kept out of auto-switch'}
RULE_SAID = {'last': 'Auto-switch now uses %s last: only when no other account has room.',
             'out': 'Auto-switch now leaves %s alone: it never moves Claude Code there, or away from it.',
             'in': 'Auto-switch uses %s as usual.'}


def rule_said(label, rule):
    """What a change of an account's rule does now, in one sentence (`switchboard auto last|out|in NAME`)."""
    return RULE_SAID[rule] % label


def rule_lines(accounts, rules):
    """`switchboard auto`, after its line: one line for each account with a rule, in the person's order:
    'maya@ivy.example is used last.' `accounts`: the engine's (id, email); `rules`: {account id: 'last' or 'out'}."""
    rules = rules if isinstance(rules, dict) else {}
    return ['%s is %s.' % (a.email or a.id, RULE_NOTES[rules[a.id]]) for a in accounts if rules.get(a.id) in RULE_NOTES]


def auto_line(auto, yours=False, reading=None):
    """`switchboard auto`: 'Auto-switch is on: at 90% 5-hour · 95% weekly, Fable too, using what refills first.' (the
    model weeks it counts, model_words) or 'Auto-switch is off. Turn it on: switchboard auto on'; when it is off by
    your own choice (`yours`: from then on it never turns on by itself, keep.py), 'Auto-switch is off until you turn it
    on: switchboard auto on'. Off with an account kept, the accounts are still read (engine.loop_wanted), and it says
    so; `reading` False leaves that out (the page that reads them is not running). `auto` is engine.auto_settings(),
    with the model weeks the accounts report (models_seen)."""
    if not auto.get('enabled'):
        if auto.get('reading_wanted') and reading is not False:
            return ('Auto-switch is off%s: the accounts are still read, and Claude Code moves only when you move it. '
                    'Turn it on: switchboard auto on' % (', as you set it' if yours else ''))
        if yours:
            return 'Auto-switch is off until you turn it on: switchboard auto on'
        return 'Auto-switch is off. Turn it on: switchboard auto on'
    five = auto.get('session_at') if _num(auto.get('session_at')) else 90
    week = auto.get('weekly_at') if _num(auto.get('weekly_at')) else 95
    return 'Auto-switch is on: at %s%% 5-hour%s%s%% weekly%s, %s.' % (
        number(five), SEP, number(week), model_words(auto),
        ORDER_WORDS.get(auto.get('strategy'), ORDER_WORDS['refills-first']))


# Links that open as their owner (`switchboard links`, the first run). macOS's own question names the button, as the
# page says it (index.html, actions.links).
LINKS_ASKING = {True: 'macOS is asking. Choose “Use Switchboard Links”.',
                False: 'macOS is asking. Choose your usual browser. Switchboard won’t offer it again; '
                       'switchboard links on turns it back on.'}
LINKS_NEEDS = "Add the link app with: switchboard update --link-app (it needs Apple's command line tools)."


def links_line(st, wanted=None, holder=None, said=None):
    """`switchboard links`: 'Links open as their owner: on', or 'off' with why and the one thing to do. `st` is
    linkapp.status(), `wanted` the links_wanted setting (False: turned off on purpose with `switchboard links off`),
    `holder` the default browser's name, when known. `said`: the page's words for an app in the seat that is not a
    browser (linkapp.banner: one that sends clicked links to the page, so they do open as their owner while it runs,
    or one not seen doing it), said in place of "off"."""
    if st.get('on'):
        return 'Links open as their owner: on. Switchboard Links is the default browser.'
    if not st.get('installed'):
        return 'Links open as their owner: off. ' + LINKS_NEEDS
    if wanted is False:
        return 'Links open as their owner: off, as you set it. Turn it on with: switchboard links on'
    if said:
        return '%s Make Switchboard Links the default with: switchboard links on' % said
    return 'Links open as their owner: off%s. Turn it on with: switchboard links on' % (
        ' (%s is the default browser)' % holder if holder else '')


# After the Sonnet switch changes while chats are open: the page's own words (index.html, MODELS_NOTE).
MODELS_NOTE = {'next': 'Open chats use it for their next helper.',
               'restart': 'Open chats keep their model until they restart.'}


def models_line(v):
    """`switchboard models`: 'Sonnet for helpers: on' or 'off', with what else decides it or why it can't go on, as the
    page's switch says beside it (index.html, settingsHTML); 'unknown' with why when the settings can't be read: never
    off while its key may be there. `v` is GET /api/models (server.models_view)."""
    name = '%s for helpers' % (v.get('model') or 'Sonnet')
    if v.get('enabled') is None:
        return '%s: unknown. %s' % (name, v.get('why') or "Claude Code's settings file can't be read.")
    by = v.get('decided_by')
    why = ('%s%s.' % ('One model for every helper is set in ' if by.get('what') == 'force' else 'Already set in ',
                      by.get('where'))) if by else (v.get('blocked') or '') if not v.get('enabled') else ''
    return '%s: %s%s' % (name, 'on' if v.get('enabled') else 'off', '. ' + why if why else '')


NOTIFY_ON = 'Notifications are on.'
STATUSLINE_THEIRS = ("You already have a status line in Claude Code, so Switchboard left it. To use Switchboard's "
                     "instead: switchboard statusline on --replace")


def on_words(auto=False, notify=False, statusline=False):
    """What is on at the end of the first run, after its count: ' Auto-switch, notifications and the status line are
    on.', ' Auto-switch and notifications are on.', ' Auto-switch is on.', ' Notifications are on.', ... or ''."""
    on = [w for w, yes in (('auto-switch', auto), ('notifications', notify), ('the status line', statusline)) if yes]
    if not on:
        return ''
    said = on[0] if len(on) == 1 else '%s and %s' % (', '.join(on[:-1]), on[-1])
    return ' %s%s %s on.' % (said[0].upper(), said[1:], 'are' if len(on) > 1 or on == ['notifications'] else 'is')


def saved(email):
    return '%s Saved %s' % (CHECK, email)


def add_another(colour=False):
    return paint([('Add another? ', None), ('[y/N]', DIM), (' ', None)], colour)


def all_accounts(n, colour=False):
    """The add loop's last line: '3 accounts. Type switchboard to see them.'"""
    return paint([('%s. Type ' % _accounts(n), None), ('switchboard', BOLD),
                  (' to see %s.' % ('it' if n == 1 else 'them'), None)], colour)


def now_on(email, colour=False):
    """After a switch: 'Claude Code is on {email}.'"""
    return paint([('Claude Code is on ', None), (email, BLUE), ('.', None)], colour)


def run_this(words, command, colour=False):
    """A line that ends in a command to run, the command in bold: ('Add another account with', 'switchboard add')."""
    return paint([(words + ' ', None), (command, BOLD), ('.', None)], colour)


# ---------- `switchboard chats`: what is going on in each account ----------
#
#   maya@northwind.example   Claude Code is here
#     working   Billing export fix   c0ffee01   started on maya.reyes@mail.example · here since 2:21 AM · moved 9 times
#                                               used Google Drive as maya.reyes@mail.example; maya@northwind.example
#                                               has not used it on this Mac in the last 7 days
#     idle 3h   Onboarding copy      77b0e4d1   started here
#     2 chats · 41 turns in the last day
#   maya.reyes@mail.example
#     no chat is on it now · 1 chat that started here is on maya@northwind.example now
#
# A chat the look has not read to its end is listed last, under "Not read to the end yet", with only its title, id
# and "still reading"; while any is, an account's foot counts the chats read so far.
#
# The view is chats.view()'s (GET /api/chats); nothing here reads a file or the clock.

MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
CHAT_TITLE = 30         # the title column at most
QUIET = 60              # seconds: a look older than this says how old it is


def quiet_for(seconds):
    """How long a chat has been quiet, short for its column: '12 min', '3h', '2d'."""
    m = max(0.0, seconds) / 60.0
    if m < 60:
        return '%d min' % max(1, jsround(m))
    if m < 1440:
        return '%dh' % jsround(m / 60.0)
    return '%dd' % jsround(m / 1440.0)


def day(t, tz=None):
    """'Nov 11', in this Mac's time zone (or `tz`)."""
    d = datetime.fromtimestamp(t, tz)
    return '%s %d' % (MONTHS[d.month - 1], d.day)


def account_words(a):
    """A chat's account as it is named: its email; an account Switchboard doesn't keep; or not known."""
    if not isinstance(a, dict):
        return 'an account not known'
    if a.get('email'):
        return a['email']
    if a.get('kept'):
        return 'one of the accounts that share organization %s' % a.get('org')
    return "an account Switchboard doesn't keep"


def chat_state(c, now):
    if c.get('state') == 'working':
        return 'working'
    return 'idle %s' % quiet_for(now - c['last']) if _num(c.get('last')) else 'idle'


def chat_details(c, now, tz=None):
    """What follows a chat's id: its login, where it started, since when it is here, how often it moved, its
    folder, its helpers, where its next turn goes."""
    on, started = c.get('on'), c.get('started')
    out = []
    if c.get('own_login'):
        out.append('its own login: a switch never moves it')
    if started is not None or on is not None:
        out.append('started here' if started == on else 'started on %s' % account_words(started))
    if c.get('moves'):
        if _num(c.get('since')):
            out.append('here since %s' % clock(c['since'], now, tz))
        out.append('moved %s' % _plural(c['moves'], 'time'))
    if c.get('title') and c.get('folder'):
        out.append('in %s' % c['folder'])
    if c.get('helpers'):
        out.append(_plural(c['helpers'], 'helper'))
    if c.get('next_on'):
        out.append('its next turn goes to %s' % c['next_on'])
    return out


def window_words(since, at):
    """'the last 7 days': the window a look read whole (chats.json's since, up to the look's time), or None."""
    if not _num(since) or not _num(at) or at < since:
        return None
    days = int((at - since) / 86400.0 + 1e-6)
    if days >= 1:
        return 'the last %s' % _plural(days, 'day')
    hours = int((at - since) / 3600.0 + 1e-6)
    return 'the last %s' % _plural(hours, 'hour') if hours >= 1 else None


def chat_holds(c, since=None, tz=None, at=None):
    """What a chat holds that belongs to another account than the one it is on: pages it made, connectors that
    answered it, and whether the account it is on has had that connector answer on this Mac: "no" only over the
    window the look read whole (`since` up to `at`), and named."""
    here = account_words(c.get('on'))
    out = ['made %s as %s' % (_plural(p['n'], 'page'), account_words(p['owner']))
           for p in (c.get('holds') or {}).get('pages') or []]
    window = window_words(since, at)
    for h in (c.get('holds') or {}).get('connectors') or []:
        said = 'used %s as %s; ' % (h['name'], account_words(h['as']))
        if h.get('here') == 'yes':
            said += '%s has used it on this Mac too' % here
        elif h.get('here') == 'no' and window:
            said += '%s has not used it on this Mac in %s' % (here, window)
        else:
            said += 'not known yet whether %s has used it on this Mac' % here
        if h.get('failed'):
            said += ' · %s failed here' % _plural(h['failed'], 'call')
        out.append(said)
    return out


def group_head(a):
    if a.get('email'):
        segs = [(a['email'], BOLD)]
    elif a.get('known'):
        segs = [(account_words(a)[0].upper() + account_words(a)[1:], None)] + \
            ([(' (organization %s)' % a['org'], DIM)] if a.get('org') and not a.get('kept') else [])
    else:
        segs = [('Account not known', None), (" (the chat's own file names none)", DIM)]
    if a.get('active'):
        segs.append(('   Claude Code is here', BLUE))
    return segs


def group_foot(a, now, partial=False):
    """Under an account's chats: none on it now, the chats that started here and are on another account now, its
    turns in the last day, its last turn when no chat is on it, the connectors that answered under it. `partial`:
    the look has not read every chat yet, so a count is of the chats read so far."""
    out = []
    if not a.get('on'):
        out.append('no chat read so far is on it' if partial else 'no chat is on it now')
    for m in a.get('moved_to') or []:
        out.append('%s that started here %s on %s now' % (_plural(m['n'], 'chat'), 'is' if m['n'] == 1 else 'are',
                                                         account_words(m['account'])))
    if a.get('turns'):
        out.append('%s%s in the last day' % ('at least ' if partial else '', _plural(a['turns'], 'turn')))
    if not a.get('on') and _num(a.get('last')) and not partial:
        out.append('last turn here %s' % age(a['last'], now))
    if a.get('connectors'):
        out.append('connectors that answered here: %s' % ', '.join(x['name'] for x in a['connectors']))
    return out


def _wrapped(words, indent, width):
    """Phrases joined by ' · ', on as many lines as `width` needs, each line indented; a phrase longer than a line
    is broken between its words."""
    room = None if width is None else max(24, width - indent)
    lines, line = [], ''
    for w in words:
        nxt = w if not line else line + SEP + w
        if room is not None and len(nxt) > room:
            if line:
                lines.append(line)
            while len(w) > room and w.rfind(' ', 0, room + 1) > 0:
                cut = w.rfind(' ', 0, room + 1)
                lines.append(w[:cut])
                w = w[cut + 1:]
            nxt = w
        line = nxt
    if line:
        lines.append(line)
    return [' ' * indent + x for x in lines]


def chats_lines(v, width=None, colour=False, tz=None):
    """Every line `switchboard chats` prints for a view (chats.view): how fresh it is, then each account (the one
    Claude Code is on first) with the chats on it and a line about the rest."""
    now = v.get('now') if _num(v.get('now')) else 0
    out = []
    reading = v.get('reading') or {}
    if not reading.get('done') and reading.get('of'):
        out.append('Read %d of %d chats so far; the rest come with the next looks.' % (reading.get('read') or 0,
                                                                                      reading['of']))
    if _num(v.get('at')) and now - v['at'] > QUIET:
        out.append('As of %s.' % age(v['at'], now))
    rows = v.get('chats') or []
    if not rows and not v.get('account'):
        out.append('No chat in the last %s.' % ('day' if (v.get('days') or 1) <= 1 else
                                                  _plural(jsround(v['days']), 'day')) +
                   (' switchboard chats --all shows the last 7 days.' if (v.get('days') or 1) <= 1 else ''))
    title_w = min(CHAT_TITLE, max([len(c.get('title') or c.get('folder') or 'no title yet') for c in rows] + [8]))
    short_w = max([len(c.get('short') or '') for c in rows] + [8])
    state_w = max([len(chat_state(c, now)) for c in rows] + [7])
    title_col = 2 + state_w + 2
    id_col = title_col + title_w + 2
    narrow = width is not None and width - (id_col + short_w + 3) < 56
    details_col = title_col if narrow else id_col + short_w + 3     # on a narrow terminal, under the title
    holds_col = title_col if narrow else id_col
    groups = sorted(v.get('accounts') or [], key=lambda a: not a.get('active'))
    unread = [c for c in rows if c.get('read') is False]           # not read to its end: no account said for it

    def chat_rows(c, details, holds):
        title = c.get('title') or c.get('folder') or 'no title yet'
        if len(title) > title_w:
            title = title[:title_w - 1] + '…'
        state = chat_state(c, now)
        head = [('  ', None), (state.ljust(state_w), BLUE if state == 'working' else DIM), ('  ', None),
                (title.ljust(title_w), None), ('  ', None), ((c.get('short') or '').ljust(short_w), DIM)]
        first = paint(head, colour)
        rest = _wrapped(details, details_col, width)
        if rest and not narrow:
            first += paint([('   ' + rest[0].lstrip(), DIM)], colour)
            rest = rest[1:]
        lines = [first.rstrip()] + [paint([(x, DIM)], colour) for x in rest]
        for held in holds:
            lines += [paint([(x, DIM)], colour) for x in _wrapped([held], holds_col, width)]
        return lines

    for a in groups:
        mine = [c for c in rows if c.get('read') is not False and (c.get('on') or None) == _key(a)]
        if out:
            out.append('')
        out.append(paint(group_head(a), colour))
        for c in mine:
            out += chat_rows(c, chat_details(c, now, tz), chat_holds(c, v.get('since'), tz, at=v.get('at')))
        foot = group_foot(a, now, partial=not reading.get('done'))
        out += [paint([(x, DIM)], colour) for x in _wrapped(foot, 2, width)]
    if unread:
        if out:
            out.append('')
        out.append(paint([('Not read to the end yet', None)], colour))
        for c in unread:
            out += chat_rows(c, ['still reading'], [])
    return out


def _key(a):
    """The account a group stands for, as a chat's `on` names it (chats.view)."""
    if a.get('email'):
        return {'email': a['email'], 'kept': True}
    if a.get('known'):
        return {'email': None, 'kept': bool(a.get('kept')), 'org': a.get('org')}
    return None


# ---------- the other assistants: the group after the accounts (switchboard status) ----------

def providers_lines(board, now, width=None, colour=False):
    """The group of providers after the accounts, from the state's `providers` (providers/view.py's board): a blank
    line, 'Providers, in your order', each added one on a line (its name, then its state, plan and windows; the one
    whose rows are the accounts above says so), then a blank line and the line to type to carry a chat on, and a line
    for each provider on this Mac not added yet. On a narrow terminal a provider's words go on under its name column.
    [] when there is nothing to say."""
    from .providers import view as provider_view
    rows = provider_view.lines(board, now)
    if not rows:
        return []
    named = [(name, text) for kind, name, text in rows if kind == 'provider']
    name_w = max([len(name) for name, _ in named] + [0])
    out = [[('', None)]]
    if named:
        out.append([('Providers', BOLD), (', in your order', DIM)])
        for name, text in named:
            lead = '  ' + name.ljust(name_w) + '  '
            for i, piece in enumerate(_provider_pieces(text, len(lead), width)):
                out.append([((lead if i == 0 else ' ' * len(lead)) + piece, None)])
    rest = [text for kind, _, text in rows if kind != 'provider']
    if rest and named:
        out.append([('', None)])
    for text in rest:                       # a command to type is never broken across two lines
        glued = _COMMAND.sub(lambda m: m.group(0).replace(' ', '\x00'), text)
        out += [[(x.replace('\x00', ' '), None)] for x in _wrapped([glued], 0, width)]
    return [paint(x, colour) for x in out]


_COMMAND = re.compile(r'switchboard (add [a-z][a-z0-9]*|continue)')


def _provider_pieces(text, indent, width):
    """A provider's words in lines that fit width after indent, broken between its ' · ' parts (a part longer than
    the room is broken by words)."""
    if width is None or indent + len(text) <= width:
        return [text]
    room = max(10, width - indent)
    out, line = [], ''
    for part in text.split(SEP):
        joined = line + SEP + part if line else part
        if len(joined) <= room:
            line = joined
            continue
        if line:
            out.append(line)
        line = ''
        for word in part.split(' '):
            joined = line + ' ' + word if line else word
            if len(joined) <= room or not line:
                line = joined
            else:
                out.append(line)
                line = word
    if line:
        out.append(line)
    return out
