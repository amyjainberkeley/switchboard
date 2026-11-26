"""The menu bar: two small bars for the account Claude Code is on, and a menu with every account.

`switchboard menubar` runs it, on the menu bar's Python (3.12 to 3.14, ~/.switchboard/runtime/env), where the
installer puts PyObjC (menubar-requirements.txt). It has one brain, the page's: it reads the running page's GET /api/state, the same
numbers the page and the terminal show, Next included, and changes accounts and settings only through the page's own
POSTs (/api/switch, /api/auto, /api/signins), sent the way `switchboard` sends them. It runs no switching loop and asks
Anthropic nothing. Two things it does by itself (watch.py): when the page has refused every connection for minutes and
was not stopped on purpose, it starts the page's background job again through launchd; and Quit leaves a mark so that
neither launchd nor the page starts it again.

The item: two 18 x 4 pt bars, 2 pt apart, 5-hour over weekly, for the account in use, with no
words; with no 5-hour number to show (an account with no 5-hour limit, Ultra, or a reading not known) its weekly bar
alone, in the middle. A template image, so macOS tints it for a light or dark menu bar; when a bar is at or over its
switch line, a colour image of the same shape with that bar's fill in the system red. A weekly limit not known is a
bare track; with no account in use, or no page, there are two bare tracks.

The menu: a row per account in slot order (its email; how long ago it was checked, "just now", "3 min ago", or Now for
the account in use while its numbers are Claude Code's own from the last minute; when its sign-in ends, a week ahead,
or ended, "Sign-in ends" or "ended" and a day; In use, Next and Use, or Sign in again; its 5-hour and weekly limits with
their %, no 5-hour one for an account that has none; the grey reset line; a model's week at the right), with "Sign in
again as …" under a row whose sign-in is about to end, and "Sign in to all" once under the rows (the page's queue),
then a line for each other assistant added (the page's board), then Auto-switch, with a line under it only when
something is off ("Auto-switch last decided 14 minutes ago.", a page that keeps restarting), Open Switchboard and Quit.
While another program holds the page's port, a line at the top says which port and what to do. While the page is not
running: one line, "Switchboard isn't running", and Start.

It reads the state every 15 s while the menu is closed and every 2 s while it is open, never more often.

A row has at most two bar rows, as the page: 5-hour only when there is a 5-hour number to show, weekly always. A
model's own week has no row; the one auto-switch counts, when it is at or over its line, is said in red at the right
of the reset line ("Fable 97%"), and nothing else is. The Auto-switch item names only the lines the rows show.

Everything above the AppKit part is plain Python that imports on 3.9 with no PyObjC: the page, the terminal and
the tests never need PyObjC. The drawing is worked out here too, as lists of shapes and words in points (item_ops,
row_ops), so the tests can read it; the AppKit part only paints them.
"""
import errno
import fcntl
import json
import math
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request

from . import core, spend, term
from .core import parse_iso

AGENT = 'io.github.amyjainberkeley.switchboard.menubar'     # the menu bar's LaunchAgent (the installer adds it)
PAGE_AGENT = 'io.github.amyjainberkeley.switchboard'         # the page's
EVERY_CLOSED = 15.0     # seconds between readings while the menu is closed
EVERY_OPEN = 2.0        # and while it is open; never more often than this
STARTING = 20.0         # after Start, the page is looked for this long at the open pace
NOTE_FOR = 60.0         # a failed click's line stays at the top of the menu this long
LOCK_WAIT = 2.0         # a menu bar starting waits this long for the lock (doctor's look holds it for an instant)
MODEL_LINE = term.MODEL_LINE        # a model's own week (Fable) is red from 90%
DEFAULT_LINE = 90.0                 # the 5-hour switch line when the state names none
DEFAULT_WEEKLY = 95.0               # the weekly one
NOT_RUNNING = "Switchboard isn't running"
UNREADABLE = "the page's answer could not be read"     # cut off, or another program answered on the page's port
NOT_HERE = 'The menu bar is not set up here. Add it with: switchboard update --menu-app'
HOPPED = 'SWITCHBOARD_MENUBAR_HOPPED'   # set before handing over to the menu bar's launcher, so it never loops

# The item, in points: two bars, 5-hour on top, fully round, the track at 30% of the ink.
BAR_W, BAR_H, GAP = 18.0, 4.0, 2.0
IMAGE_W, IMAGE_H = BAR_W, 2 * BAR_H + GAP           # 18 x 10
ITEM_W = BAR_W + 2 * 8                              # 34: 8 on each side, the image centred
TRACK = 0.3

# A row in the menu's panel, in points: 360 wide; the hover fill inset 6 from the sides with
# radius 8; content 16 in. From the top: 8, the email line 20, the 5-hour bar row 18 (none with no 5-hour number to
# show), the weekly bar row 18, the reset line 16, 8. A bar row: label 48, 8, the bar (4 high, the rest of the
# width), 8, the % in 32.
ROW_W, ROW_H = 360.0, 88.0
INSET, PAD, HOVER_R = 6.0, 16.0, 8.0
LABEL_W, PCT_W, SPACE = 48.0, 32.0, 8.0
ROW_BAR_H, BAR_ROW = 4.0, 18.0


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v not in (float('inf'), float('-inf'))


# ---------- the state, as the menu bar reads it (the page's own rules, web/index.html) ----------

def lines_of(a, auto):
    """(5-hour line, weekly line): automatic switching's own, each window against its own line; the account's own
    lines where it has them (term.own_lines_of)."""
    five = auto['session_at'] if _num(auto.get('session_at')) else DEFAULT_LINE
    week = auto['weekly_at'] if _num(auto.get('weekly_at')) else DEFAULT_WEEKLY
    own = auto.get('own_lines') if isinstance(auto.get('own_lines'), dict) else {}
    return term.own_lines_of(own.get(a.get('id')), five, week)


def _window(u, key):
    """One limit's reading: {used, started, resets}. used is None when it is not known. 0% with no reset has not
    started: how Anthropic reports a 5-hour window nothing has used yet."""
    if u is None:
        return {'used': None, 'started': False, 'resets': None}
    w = u.get(key)
    if not isinstance(w, dict):                     # no window open: Claude reports none
        return {'used': 0, 'started': False, 'resets': None}
    used = w['pct'] if _num(w.get('pct')) else None
    return {'used': used, 'started': not (used == 0 and not w.get('resets')), 'resets': w.get('resets') or None}


def _limit(kind, label, w, line):
    used = w['used']
    pct = None if used is None else term.jsround(used)
    return {'kind': kind, 'label': label, 'used': used, 'pct': pct, 'unknown': used is None,
            'over': pct is not None and pct >= line, 'line': line, 'started': w['started'], 'resets': w['resets']}


def limits(a, auto):
    """At most two bar rows, as the page has them: 5-hour, then weekly. The 5-hour row only when there is a 5-hour
    number to show: none for an account with no 5-hour limit (Ultra: no 5-hour window), for a 5-hour window with no
    number, or for a reading not known (too old to show, trusted false, or never read). The weekly row always: a
    reading not known is a bare track and a dash, never an old number. A model's own week has no row; after the
    weekly one comes, at most, the model week auto-switch counts that is at or over its line (term.over_note), the
    red note at the right of the reset line."""
    u = a.get('usage') if a.get('trusted') is not False else None
    u = u if isinstance(u, dict) else None
    five, week = lines_of(a, auto)
    s = (u or {}).get('session')
    out = [_limit('five', '5-hour', _window(u, 'session'), five)] if isinstance(s, dict) and _num(s.get('pct')) else []
    out.append(_limit('week', 'Weekly', _window(u, 'weekly'), week))
    line_m = auto['model_at'] if _num(auto.get('model_at')) else MODEL_LINE
    note = term.over_note(u, line_m, auto.get('models'))
    if note:
        out.append(_limit('model', note['label'], {'used': note['pct'], 'started': True, 'resets': None}, line_m))
    return out


def split(lims):
    """(the 5-hour limit, or None with no 5-hour number to show; the weekly one; the note, [] or the one counted model
    week at or over its line) of limits()."""
    five = lims[0] if lims[0]['kind'] == 'five' else None
    return (five, lims[1], lims[2:]) if five else (None, lims[0], lims[1:])


def pct_text(lim):
    return '—' if lim['unknown'] else '%d%%' % lim['pct']


def reset_line(lim, now, tz=None):
    """'Not started', 'Started' or 'Resets Sat 12:00 PM · in 20 hours'; '' when the reading is not known."""
    if lim['unknown']:
        return ''
    if not lim['started']:
        return 'Not started'
    t = parse_iso(lim['resets'])
    if t is None:
        return 'Started'
    if t <= now:
        return 'Not started'
    return 'Resets %s%s%s' % (term.clock(t, now, tz), term.SEP, term.until(t, now))


def foot(five, week, now, tz=None):
    """The reset line under a row's bars: while a 5-hour window runs (used, its reset known), both resets,
    '5-hour resets 6:10 PM · weekly Sat 12:00 PM'; otherwise the weekly reset line."""
    t5, tw = parse_iso((five or {}).get('resets')), parse_iso(week['resets'])
    if five and not five['unknown'] and five['used'] > 0 and t5 is not None and t5 > now:
        weekly = '%sweekly %s' % (term.SEP, term.clock(tw, now, tz)) if tw is not None and tw > now else ''
        return '5-hour resets %s%s' % (term.clock(t5, now, tz), weekly)
    return reset_line(week, now, tz)


def needs_setup(state):
    """Nothing to show yet: no accounts, no engine, or no account Switchboard holds (the page's first run)."""
    s = state.get('setup') or {}
    return not state.get('accounts') or not s.get('engine') or not s.get('accounts')


def next_email(state):
    """The account marked Next: the server's, only while automatic switching runs, never the one in use or one
    that needs a fresh sign-in."""
    auto = state.get('auto') or {}
    if not auto.get('enabled'):
        return None
    n = state.get('next') if 'next' in state else (state.get('plan') or {}).get('next') if auto.get('on') else None
    a = next((x for x in state.get('accounts') or [] if x.get('email') == n), None) if n else None
    return n if a and n != state.get('active') and not a.get('needs_login') else None


def _now(state):
    return state['now'] if _num(state.get('now')) else time.time()


def by_slot(state):
    return sorted(state.get('accounts') or [], key=lambda a: a.get('slot') or 99)


def rows(state, tz=None):
    """Every account as a menu row, in slot order: {slot, email, in_use, live, age, next, action ('use', 'signin' or
    None), marks [(words, style)], five, week, models, foot, signin, again}. live: the account in use is on Claude
    Code's own numbers from the last minute (the page's "now", server.live_now). age: how long ago it was checked
    ('just now', '3 min ago': term.age of term.checked_at), shown first among its marks, or Now while live. signin:
    when its sign-in ends or ended, in the page's words (term.signin_words), its short words a mark after the age
    ('Sign-in ends' or 'ended', and a day). An account's rule in auto-switch is no mark here: the row would cut its
    email short to make room, and an email is always shown whole. again: signing in again is offered beside the row's
    own action, as on the page: a sign-in that ends within the week (it still works), and the account in use's
    whenever it has words: it signs in again from the page like any other. pace: 'At this pace, full by Sat 4:23 PM'
    when the weekly limit would be full before it refills (term.pace_words), drawn at the right of the reset line,
    which then leaves out how far off its reset is; else ''."""
    now, auto, nx = _now(state), state.get('auto') or {}, next_email(state)
    out = []
    for a in by_slot(state):
        email = a.get('email') or ''
        five, week, models = split(limits(a, auto))
        cost = spend_row(a, five)
        five = None if cost else five       # two bar rows at most: the spend limit stands in for the 5-hour one
        on = email == state.get('active')
        live = on and a.get('live') is True
        age = term.age(term.checked_at(a), now)
        when = [('Now', 'next')] if live else [(age, 'next')] if age else []
        ends, need = a.get('signin_ends_at'), bool(a.get('needs_login'))
        signin = '' if a.get('unmanaged') else term.signin_words(ends, now, need, tz)
        when += [(term.signin_words(ends, now, need, tz, short=True), 'next')] if signin else []
        if on:
            marks, action = when + [('In use', 'in-use')], None
        elif a.get('unmanaged'):
            marks, action = when, None
        elif need:
            marks, action = when + [('Sign in again', 'use')], 'signin'
        else:
            marks, action = when + ([('Next', 'next')] if email == nx else []) + [('Use', 'use')], 'use'
        out.append({'slot': a.get('slot'), 'email': email, 'in_use': on, 'live': live, 'age': age,
                    'next': email == nx and not on, 'action': action, 'marks': marks, 'five': five, 'week': week,
                    'models': models, 'foot': foot(five, week, now, tz), 'signin': signin,
                    'again': bool(signin) and (on or not need), 'pace': pace_words(a, now, tz),
                    'spend': cost})
        if out[-1]['pace'] and out[-1]['foot'].startswith('Resets '):     # room for the pace words: no "in 3 days"
            out[-1]['foot'] = out[-1]['foot'].split(term.SEP)[0]
    return out


def spend_row(a, five):
    """The spend limit as a bar row under the weekly one (spend.shown: it has a number, and more of it is used than of
    the 5-hour limit), with its amounts ('$412 of $1,000') for the right of the reset line; None otherwise. It has no
    switch line: never red, no mark."""
    u = a.get('usage') if a.get('trusted') is not False and isinstance(a.get('usage'), dict) else None
    s = spend.shown(u, five['used'] if five and not five['unknown'] else None) if u else None
    if s is None:
        return None
    return dict(_limit('spend', 'Spend', {'used': s['pct'], 'started': True, 'resets': s['resets']}, 101.0),
                line=None, amounts=spend.amounts(s))


def pace_words(a, now, tz=None):
    """'At this pace, full by Sat 4:23 PM' (term.pace_words, the one rule, with its capital: it starts its own words
    at the right of the reset line); '' when the week lasts or too little is known."""
    words = term.pace_words(a, now, tz)
    return words[:1].upper() + words[1:]


def number(v):
    """A switch line as the page writes it: 90, 92.5."""
    return '%g' % (math.floor(v * 10 + 0.5) / 10) if _num(v) else ''


def switch_words(state):
    """The Auto-switch item's words, as the page's switch line: 'at 90% 5-hour · 95% weekly', the 5-hour line only
    when an account has a 5-hour limit (term.has_five), never a model's week (the page's Settings and `switchboard
    auto` say that one)."""
    auto = state.get('auto') or {}
    five = '%s%% 5-hour%s' % (number(auto.get('session_at')), term.SEP) if term.has_five(state.get('accounts')) else ''
    return 'at %s%s%% weekly' % (five, number(auto.get('weekly_at')))


def first_line(state):
    """What the menu says before there is an account to show (the page's first run, in one line)."""
    if not (state.get('setup') or {}).get('engine'):
        return 'Finish installing Switchboard'
    cur = next((a for a in state.get('accounts') or [] if a.get('unmanaged')), None)
    return 'Claude Code is on %s' % cur['email'] if cur and cur.get('email') else 'Add your first account'


def menu(state, note=None, tz=None, status=None):
    """The menu, top to bottom: ('note', words), ('line', words), ('row', row), ('separator',),
    ('auto', on, words, can), ('do', title, what). state None: the page is not running. status: Watch.status(), when
    the page has not answered lately: its line goes first; rows drawn from an old state are grey; Start when it is not
    running."""
    out = [('note', note)] if note else []
    if state is None:
        if status and status.get('kind') == 'busy':
            return out + [('line', status['line']), ('separator',), ('do', 'Quit', 'quit')]
        return out + [('line', NOT_RUNNING), ('do', 'Start', 'start'), ('separator',), ('do', 'Quit', 'quit')]
    if status:
        out.append(('line', status['line']))
    start = [('do', 'Start', 'start')] if status and status.get('kind') == 'down' else []
    if needs_setup(state):
        return out + start + [('line', first_line(state)), ('separator',), ('do', 'Open Switchboard', 'open'),
                              ('do', 'Quit', 'quit')]
    auto = state.get('auto') or {}
    can = auto.get('available', True) is not False         # no engine: Auto-switch can't be changed
    grey = bool(status and status.get('stale'))
    out += start
    rs = rows(state, tz)
    for r in rs:
        out.append(('row', dict(r, stale=True) if grey else r))
        if r['again']:                                  # beside the row's own action (it opens the page, as Sign in again)
            out.append(('do', 'Sign in again as %s' % r['email'], 'signin'))
    out += signin_items(state, rs)
    out += provider_items(state.get('providers'), _now(state))
    said = auto_note(auto)
    return out + [('separator',), ('auto', bool(auto.get('enabled')), switch_words(state), can)] + \
        ([('line', said)] if said else []) + watch_lines(auto) + \
        [('separator',), ('do', 'Open Switchboard', 'open'), ('do', 'Quit', 'quit')]


def signin_items(state, rs):
    """Sign in to all, once under the rows while the note above the page's accounts offers it: the page's own lists
    (signins.due, in the state's `signins`: a sign-in that ended, or one that ends this week), so the menu bar and the
    page offer it at the same time. The page's own queue (POST /api/signins), one account after another, each approved
    in the browser. While it runs: where it is, in the page's words. A page from before those lists: while any row
    offers a sign-in, as then."""
    q = state.get('signins') or {}
    if q.get('state') == 'running':
        who = q.get('current')
        return [('line', ('Signing in to %s: approve it in the tab that opened.' % who) if who else 'Signing in again…')]
    if isinstance(q.get('ended'), list) and isinstance(q.get('week'), list):
        offered = bool(q['ended'] or q['week'])
    else:
        offered = any(r['again'] or r['action'] == 'signin' for r in rs)
    return [('do', term.SIGNIN_ALL, 'signinall')] if offered else []


def provider_items(board, now):
    """The other assistants after the account rows, from the state's `providers` (providers/view.py's board): a
    separator, then a line for each added one but the one whose rows are the account rows above (its state, plan and
    windows), the line to carry a chat on, and a line for each one on this Mac not added. Only 'line' items, which do
    nothing when clicked. [] when there is nothing to say."""
    from .providers import view as provider_view, words as provider_words
    out = []
    shown = provider_view._listed(board, 'providers') if isinstance(board, dict) else []
    for kind, name, text in provider_view.lines(board, now):
        if kind == 'provider':
            p = next((x for x in shown if provider_words.one_line(x.get('name') or x.get('id') or '', 30) == name),
                     None)
            if p is not None and not p.get('account_list'):
                out.append(('line', provider_words.MENU_PROVIDER.format(name=name,
                                                                         words=provider_view.summary(p, now))))
        elif kind == 'carry':
            out.append(('line', provider_words.MENU_CARRY))
        else:
            out.append(('line', text))
    return [('separator',)] + out if out else []


def auto_note(auto):
    """One line under Auto-switch, in the page's own words. While it is on: why it is not running, a restart of its
    loop, or what it waits for (the page's auto line: no internet since 5:19 PM, Waiting: Claude Code is working.).
    While it is off the accounts are still read (engine.loop_wanted): a line only when that reading is stuck or
    stopped, or was restarted."""
    if not auto.get('enabled'):
        return auto.get('reading_problem') or (auto.get('restarted') if auto.get('reading_wanted') else None) or None
    if auto.get('problem') and not auto.get('on'):
        return auto['problem']
    return auto.get('restarted') or auto.get('line') or None


def watch_lines(auto):
    """Under Auto-switch, only when something is off, in the page's own words (server.watch_words): "Auto-switch last
    decided 12 minutes ago." while it is on and running but decided nothing for minutes (a stopped or stuck loop has its
    own line, auto_note), and "Switchboard restarted 4 times in the last 10 minutes.", the last of them with the one
    thing to do (watch.WATCH_TODO)."""
    out = []
    if auto.get('decided') and auto.get('enabled') and auto.get('on') and not auto.get('problem'):
        out.append(('line', auto['decided']))
    if auto.get('restarts'):
        out.append(('line', auto['restarts']))
    return out


def watch_note():
    """The line at the top of the menu while the page waits for its port, which another program holds (the page's mark,
    watch.port_mark): which port, that auto-switch still works, and the one thing to do. None otherwise."""
    from . import watch
    try:
        mark = watch.port_mark()
    except Exception:  # noqa: BLE001 - the menu shows without it
        return None
    return watch.port_words(mark['port']) if mark and mark['port'] == core.port() else None


def watch_page():
    """The menu bar's watch on the page (watch.PageKeeper) on a daemon thread: it starts the page through launchd when
    the page has refused every connection for minutes and was not stopped on purpose, and says so in a notification
    either way. Returns the thread."""
    from . import notify, watch
    keeper = watch.PageKeeper(
        answers=lambda: watch.page_answers(core.port()),
        start=lambda: watch.start_job(PAGE_AGENT, agent_plist(), launchctl, domain()),
        send=lambda note: notify.send(note))
    t = threading.Thread(target=keeper.run, name='switchboard-page-watch', daemon=True)
    t.start()
    return t


# ---------- what the menu bar shows across readings ----------

STALE_AFTER = 60.0      # seconds (monotonic) without a good reading: the bars go grey and the menu says since when
DOWN_AFTER = 2          # refused readings in a row before "isn't running" (a page restarting is refused for a moment)
UNREADABLE_LINE = "Switchboard's answer can't be read"     # cut off, or another program answers on its port


def clock_words(wall, tz=None):
    """'2:02 PM', for "not updated since 2:02 PM" (the Mac's own time zone)."""
    from datetime import datetime
    t = datetime.fromtimestamp(wall, tz) if tz else datetime.fromtimestamp(wall)
    return '%d:%02d %s' % (t.hour % 12 or 12, t.minute, 'AM' if t.hour < 12 else 'PM')


class Watch(object):
    """The readings, as the menu bar draws them. The last good state stays on screen when a reading fails; a page that
    refuses the connection isn't running, one that does not answer in time (or answers 503) is busy. Past STALE_AFTER
    seconds without a good reading, or when the page's own answer is that old, the bars go grey and the menu says since
    when. took() and status() take the monotonic clock (a clock set back never freezes or ages it) and the wall clock
    only for the words."""

    def __init__(self):
        self.state = None           # the last good state
        self.good = None            # monotonic time it was current
        self.good_wall = None       # and the wall time, for the words
        self.why = None             # the last reading's failure: None, 'down', 'busy' or 'unreadable'
        self.refused = 0            # refused readings in a row

    def took(self, state, why, mono, wall):
        """One reading: the state, or None and why it failed."""
        if state is not None:
            age = (state.get('answer') or {}).get('age') if isinstance(state.get('answer'), dict) else None
            age = float(age) if _num(age) and age > 0 else 0.0
            self.state, self.good, self.good_wall, self.why, self.refused = state, mono - age, wall - age, None, 0
            return
        self.why = why or 'unreadable'
        self.refused = self.refused + 1 if self.why == 'down' else 0

    def status(self, mono, tz=None):
        """None while the numbers are current; else {'kind': 'down' or 'busy', 'stale': grey the bars, 'line'}."""
        old = self.good is not None and not 0 <= mono - self.good < STALE_AFTER
        down = self.why == 'down' and self.refused >= DOWN_AFTER
        if self.why is None and not old:
            return None
        since = (' · not updated since %s' % clock_words(self.good_wall, tz)) if old and self.good_wall else ''
        if down:
            return {'kind': 'down', 'stale': old, 'line': NOT_RUNNING + since}
        if self.why is None or self.why == 'down':      # answering, but its numbers are old; or one refusal so far
            if not old:
                return None
            return {'kind': 'busy', 'stale': True, 'line': 'Not updated since %s' % clock_words(self.good_wall, tz)}
        words = UNREADABLE_LINE if self.why == 'unreadable' else 'Switchboard is busy'
        return {'kind': 'busy', 'stale': old, 'line': words + since}

    def view(self, mono, tz=None):
        """(the state to draw or None, its status)."""
        return self.state, self.status(mono, tz)


def shape(items):
    """What a menu is made of, without its numbers: a menu whose shape is the same is changed in place. A row's
    action and slot are part of it: they set its item's enabled state and its 1-9 key, which only a new item gets."""
    return [(x[0], x[1]['email'], x[1]['action'], x[1]['slot'], row_height(x[1])) if x[0] == 'row'
            else (x[0],) if x[0] in ('separator', 'auto') else x[:2] for x in items]


# ---------- the item ----------

def reading(state, status=None):
    """What the item shows: {email, five, week, red, label, stale}. five and week are None for two bare tracks; five
    alone is None with no 5-hour number to show (its weekly bar alone). status (Watch.status): numbers that are
    not current are drawn grey, never red, and the label says so."""
    if state is None:
        words = status['line'] if status and status.get('kind') == 'busy' else NOT_RUNNING
        return {'email': None, 'five': None, 'week': None, 'red': False, 'label': words, 'stale': False}
    a = None if needs_setup(state) else \
        next((x for x in state.get('accounts') or [] if x.get('email') and x.get('email') == state.get('active')), None)
    if a is None:
        words = 'Switchboard: no accounts yet' if needs_setup(state) else 'Switchboard: no account in use'
        return {'email': None, 'five': None, 'week': None, 'red': False, 'label': words, 'stale': False}
    five, week, _ = split(limits(a, state.get('auto') or {}))
    said = ('5-hour %s, ' % pct_text(five) if five else '') + 'weekly %s' % pct_text(week)
    stale = bool(status and status.get('stale'))
    return {'email': a['email'], 'five': five, 'week': week,
            'red': not stale and (bool(five and five['over']) or week['over']), 'stale': stale,
            'label': 'Switchboard: %s, %s%s' % (a['email'], said, (' · %s' % status['line']) if stale else '')}


def fill_width(used, width=BAR_W, height=BAR_H):
    """How much of a bar is filled, in points: used % of it, capped at all of it, and at least its height (one
    round end) when anything is used; none at 0 or when not known."""
    if not _num(used) or used <= 0:
        return 0.0
    return max(height, min(float(used), 100.0) / 100.0 * width)


def item_ops(r):
    """The item's image, 18 x 10 pt, top-left origin: ('box', x, y, w, h, radius, colour) for each track and fill.
    Colours: 'ink30' the track, 'ink' a fill, 'sysred' a fill at or over its line (only in the colour image). An
    account with no 5-hour number to show has its weekly bar alone, in the middle."""
    ops = []
    one = r['five'] is None and r['week'] is not None
    for lim, y in [(r['week'], (IMAGE_H - BAR_H) / 2)] if one else [(r['five'], 0.0), (r['week'], BAR_H + GAP)]:
        ops.append(('box', 0.0, y, BAR_W, BAR_H, BAR_H / 2, 'ink30'))
        f = fill_width(lim['used']) if lim else 0.0
        if f:
            ops.append(('box', 0.0, y, f, BAR_H, BAR_H / 2, 'ink50' if r.get('stale') else
                        'sysred' if r['red'] and lim['over'] else 'ink'))
    return ops


# ---------- a row's drawing, in points, top-left origin ----------

def row_height(row):
    """88 pt; 72 when there is no reset line and no note (a reading not known), as on the page; a bar row less with
    no 5-hour number to show."""
    return (ROW_H if row['foot'] or row['models'] else ROW_H - 16.0) - (0.0 if row['five'] or row.get('spend')
                                                                        else BAR_ROW)


def row_ops(row, hover=False, width=ROW_W):
    """A menu row as shapes and words:
      ('box', x, y, w, h, radius, colour)
      ('text', x, y, w, h, [(words, size, weight, colour)], align)       one line, cut short with … when too wide
      ('pair', x, y, w, h, left runs, [right pieces], gap)                left cut short to leave the right whole;
                                                                          pieces right-aligned, gap apart, all on
                                                                          one baseline
    Colours are tokens (TOKENS): ink, ink2, ink3, track, fill, accent, red, hover."""
    ops = []
    if hover and row['action']:
        ops.append(('box', INSET, 0.0, width - 2 * INSET, row_height(row), HOVER_R, 'hover'))
    right = [[(words, 12.0, 400 if style == 'next' else 500, 'ink2' if style == 'next' else 'accent')]
             for words, style in row['marks']]
    ops.append(('pair', PAD, 8.0, width - 2 * PAD, 20.0, [(row['email'], 13.0, 500, 'ink')], right, SPACE))
    bar_x = PAD + LABEL_W + SPACE
    bar_w = width - PAD - PCT_W - SPACE - bar_x
    bars = [lim for lim in (row['five'], row['week'], row.get('spend')) if lim is not None]     # two at most
    for i, lim in enumerate(bars):
        y = 28.0 + BAR_ROW * i
        ops.append(('text', PAD, y, LABEL_W, 18.0, [(lim['label'], 12.0, 400, 'ink2')], 'left'))
        top = y + (18.0 - ROW_BAR_H) / 2
        ops.append(('box', bar_x, top, bar_w, ROW_BAR_H, ROW_BAR_H / 2, 'track'))
        f = fill_width(lim['used'], bar_w, ROW_BAR_H)
        stale = row.get('stale')                    # numbers that are not current: grey, never blue or red
        if f:
            ops.append(('box', bar_x, top, f, ROW_BAR_H, ROW_BAR_H / 2,
                        'fill' if stale else 'red' if lim['over'] else 'accent' if row['in_use'] else 'fill'))
        ops.append(('text', width - PAD - PCT_W, y, PCT_W, 18.0,
                    [(pct_text(lim), 12.0, 400 if lim['unknown'] or stale else 500,
                      'ink3' if lim['unknown'] or stale else 'red' if lim['over'] else 'ink')], 'right'))
    models = []
    for i, m in enumerate(row['models']):
        models += [((term.SEP if i else '') + m['label'] + ' ', 11.0, 400, 'ink2'),
                   (pct_text(m), 11.0, 400, 'red' if m['over'] else 'ink2')]
    said = [[(row['pace'], 11.0, 400, 'ink2')]] if row.get('pace') else []     # at this pace, full before it refills
    if row.get('spend') and row['spend']['amounts']:
        said.append([(row['spend']['amounts'], 11.0, 400, 'ink2')])
    if row['foot'] or models or said:
        ops.append(('pair', PAD, 28.0 + BAR_ROW * len(bars), width - 2 * PAD, 16.0, [(row['foot'], 11.0, 400, 'ink2')],
                    said + ([models] if models else []), SPACE))
    return ops


def row_label(row):
    """What VoiceOver says for a row: 'Use maya@northwind.example, checked 3 min ago, …', 'maya@… in use now, 5-hour
    62%, weekly 70%' (no 5-hour part with no 5-hour number to show)."""
    head = {'use': 'Use %s', 'signin': 'Sign in again as %s'}.get(row['action'], '%s') % row['email']
    state = (', in use now' if row.get('live') else ', in use') if row['in_use'] else ', next' if row['next'] else ''
    when = ', checked %s' % row['age'] if row.get('age') and not row.get('live') else ''
    signin = ', %s' % row['signin'] if row.get('signin') else ''
    five = '5-hour %s, ' % pct_text(row['five']) if row['five'] else ''
    said = ', ' + row['pace'][:1].lower() + row['pace'][1:] if row.get('pace') else ''
    cost = ', spend %s' % pct_text(row['spend']) + (', %s' % row['spend']['amounts'] if row['spend']['amounts'] else '') \
        if row.get('spend') else ''
    return '%s%s%s%s, %sweekly %s%s%s' % (head, state, when, signin, five, pct_text(row['week']), cost, said)


# ---------- when to read ----------

class Pace(object):
    """When to read the page's state: every 15 s while the menu is closed, every 2 s while it is open (and for a
    little while after Start), never more often than every 2 s, whatever opens and closes."""

    def __init__(self):
        self.last = None            # when the last reading started
        self.fast_until = 0.0       # after Start: look at the open pace until then
        self.wanted = False         # a click changed something: read as soon as the floor allows

    def every(self, now, menu_open):
        return EVERY_OPEN if menu_open or self.wanted or now < self.fast_until else EVERY_CLOSED

    def wait(self, now, menu_open):
        """Seconds until the next reading is due; 0 when it is due now."""
        if self.last is None:
            return 0.0
        return max(0.0, self.last + self.every(now, menu_open) - now)

    def due(self, now, menu_open):
        return self.wait(now, menu_open) <= 0

    def started(self, now):
        self.last = now
        self.wanted = False

    def soon(self):
        """Read again as soon as the 2 s floor allows (after a click changed something)."""
        self.wanted = True

    def hurry(self, now, seconds=STARTING):
        self.fast_until = now + seconds


# ---------- the page: the same calls `switchboard` makes ----------

def page_url(path=''):
    """The running page, on the port `switchboard page` opens (the page's own setting, read each time)."""
    return 'http://127.0.0.1:%d%s' % (core.port(), path)


def direct():
    """An opener that goes straight to the page on this Mac, never through a proxy: http_proxy, or a system proxy
    that does not list 127.0.0.1, would take the menu bar's calls elsewhere and show a running page as stopped."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _refused(e):
    return isinstance(e, ConnectionRefusedError) or (isinstance(e, OSError) and e.errno == errno.ECONNREFUSED)


def get_state(timeout=5, why=None):
    """GET /api/state, or None when the page does not answer with a state. Never raises: an answer cut off part-way
    (the page restarting) or one that is not HTTP (another program on the port) is no state too. why: a list that gets
    the reason a reading failed: 'down' (the connection was refused: nothing listens), 'busy' (no answer in time, or
    the page's own 503) or 'unreadable' (an answer that is not the page's)."""
    def failed(reason):
        if why is not None:
            why.append(reason)
        return None
    try:
        with direct().open(page_url('/api/state'), timeout=timeout) as r:
            got = json.load(r) if r.status == 200 else None
    except urllib.error.HTTPError as e:
        busy = e.code == 503
        e.close()
        return failed('busy' if busy else 'unreadable')
    except urllib.error.URLError as e:
        reason = e.reason
        if _refused(reason):
            return failed('down')
        return failed('busy' if isinstance(reason, (socket.timeout, TimeoutError)) else 'unreadable')
    except (socket.timeout, TimeoutError):
        return failed('busy')
    except Exception as e:  # noqa: BLE001 - refused, timed out, cut off (http.client.HTTPException), not JSON: no state
        return failed('down' if _refused(e) else 'unreadable')
    if isinstance(got, dict) and isinstance(got.get('accounts'), list):
        return got
    return failed('unreadable')


def post(path, body, timeout=120):
    """POST to the page as the page itself does: JSON, with the X-Switchboard header its server asks for, from this
    Mac. Returns (True, answer) or (False, the page's own words). Never raises."""
    req = urllib.request.Request(page_url(path), data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json', 'X-Switchboard': '1'})
    try:
        with direct().open(req, timeout=timeout) as r:
            return True, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            said = json.load(e)
        except Exception:  # noqa: BLE001 - a refusal whose words can't be read is still a refusal
            said = {}
        finally:
            e.close()
        return False, (said.get('error') if isinstance(said, dict) else None) or 'The page said no.'
    except OSError:
        return False, NOT_RUNNING
    except Exception:  # noqa: BLE001 - an answer cut off part-way, or not the page's (http.client.HTTPException)
        return False, UNREADABLE


def in_background(work, done, failed, call_after):
    """work() on a thread of its own, its answer handed to done() through call_after (AppKit's main thread). Always:
    when work() raises, done() gets failed(the error) instead, so the item never waits for good on a reading or a
    click that died."""
    def body():
        got = None
        try:
            got = work()
        except Exception as e:  # noqa: BLE001 - whatever it was, the menu bar must hear back; the log says what
            traceback.print_exc()
            got = failed(e)
        finally:
            call_after(done, got)
    try:
        threading.Thread(target=body, daemon=True).start()
    except RuntimeError as e:       # no thread could be started: the answer is the failure, at once
        call_after(done, failed(e))


def click(row):
    """A click on a row: (path, body) to send, or None (the account in use, or one Switchboard does not hold)."""
    return ('/api/switch', {'email': row['email']}) if row['action'] == 'use' else None


def failed(what, words):
    """The line a failed click leaves at the top of the menu, short enough for one line."""
    text = '%s: %s' % (what, words)
    return text if len(text) <= 72 else text[:71].rstrip() + '…'


# ---------- starting the page, as its LaunchAgent does ----------

LAUNCHD_PATH = ('{home}/.local/bin', '{home}/.npm-global/bin', '/opt/homebrew/bin', '/usr/local/bin', '/usr/bin',
                '/bin', '/usr/sbin', '/sbin')       # the page's LaunchAgent's PATH (install.sh)


def agent_plist(label=PAGE_AGENT):
    return os.path.join(core.home(), 'Library', 'LaunchAgents', label + '.plist')


def launchctl(*args):
    """launchctl's exit status (127 when it could not run)."""
    try:
        return subprocess.run(['launchctl'] + list(args), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=20).returncode
    except (OSError, subprocess.TimeoutExpired):
        return 127


def domain():
    return 'gui/%d' % os.getuid()


def run_command(what):
    """`switchboard <what>` the way the installer's launcher runs it, else this program on the Python behind it."""
    launcher = core.data_path(os.path.join('bin', 'switchboard'))
    if os.path.isfile(launcher) and os.access(launcher, os.X_OK):
        return [launcher, what]
    python = os.path.realpath(getattr(sys, '_base_executable', None) or sys.executable)
    return [python, '-I', '-B', '-c', RUNPY, os.path.dirname(core.PACKAGE), what]


RUNPY = ('import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); '
         'runpy.run_module("switchboard", run_name="__main__", alter_sys=True)')


def start_page():
    """Start the page the way its LaunchAgent does: through that job when the installer added it (started, or
    loaded again), else `switchboard serve` in the background, with the job's PATH and log. Returns 'job' or
    'serve'."""
    plist = agent_plist()
    if os.path.exists(plist):
        if launchctl('kickstart', '%s/%s' % (domain(), PAGE_AGENT)) == 0 or \
                launchctl('bootstrap', domain(), plist) == 0:
            return 'job'
    home = core.home()
    env = dict(os.environ)
    env.pop('SWITCHBOARD_DEBUG', None)          # (debug.VARIABLE) a trace is for a command at a terminal, never the page
    path = [p.format(home=home) for p in LAUNCHD_PATH]
    env['PATH'] = os.pathsep.join(path + [p for p in env.get('PATH', '').split(os.pathsep) if p and p not in path])
    os.makedirs(core.data_dir(), mode=0o700, exist_ok=True)
    with open(core.data_path('agent.log'), 'a') as log:
        subprocess.Popen(run_command('serve'), stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env,
                         cwd=home, start_new_session=True)
    return 'serve'


# ---------- one menu bar at a time, and what doctor reads ----------

def folder():
    """<data>/menubar: the wrapper app, the lock and the running stamp."""
    return core.data_path('menubar')


def lock_path():
    return os.path.join(folder(), 'lock')


def stamp_path():
    return os.path.join(folder(), 'running.json')


_HELD = []


def take_lock(wait=None):
    """Hold the menu bar's lock for as long as this process runs. False when another menu bar holds it. A look from
    running_pid() (doctor) holds it shared for an instant, so a menu bar starting then tries again for LOCK_WAIT
    seconds before it steps back: under its job, stepping back means no menu bar until the next login."""
    os.makedirs(folder(), mode=0o700, exist_ok=True)
    f = open(lock_path(), 'a')
    give_up = time.time() + (LOCK_WAIT if wait is None else wait)
    while True:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.time() >= give_up:
                f.close()
                return False
            time.sleep(0.05)
    _HELD.append(f)
    core.write_json(stamp_path(), {'pid': os.getpid(), 'started': time.time(), 'python': sys.executable})
    return True


def release_lock():
    """Let the lock go (it goes by itself when the process ends)."""
    while _HELD:
        _HELD.pop().close()


def running_pid():
    """The pid of the menu bar that runs now (0 when one runs but its pid is not known), or None. Only looks: a
    lock that is not there is not made."""
    try:
        fd = os.open(lock_path(), os.O_RDONLY)
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError:
            pid = core.read_json(stamp_path()).get('pid')
            return pid if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 else 0
        fcntl.flock(fd, fcntl.LOCK_UN)
        return None
    finally:
        os.close(fd)


def launcher():
    """<data>/bin/switchboard-menubar: the installer's launcher, on the menu bar's Python, where PyObjC is."""
    return core.data_path(os.path.join('bin', 'switchboard-menubar'))


def our_job():
    """The menu bar's LaunchAgent when it runs this data folder's launcher (its plist's path), else None: a menu
    bar run for another folder (a try with a throwaway one) never starts the installed one instead."""
    import plistlib
    plist = agent_plist(AGENT)
    try:
        with open(plist, 'rb') as f:
            args = plistlib.load(f).get('ProgramArguments')
    except Exception:  # noqa: BLE001 - missing, unreadable or not a plist: not a job this can start
        return None
    mine = os.path.realpath(launcher())
    return plist if isinstance(args, list) and any(isinstance(a, str) and os.path.realpath(a) == mine
                                                    for a in args) else None


def appkit_here():
    """Can this Python draw the menu bar (is PyObjC here)? Looks without importing it."""
    import importlib.util
    try:
        return importlib.util.find_spec('objc') is not None and importlib.util.find_spec('AppKit') is not None
    except (ImportError, ValueError):
        return False


def libraries(timeout=30):
    """{pyobjc, python, framework} for the menu bar's Python the launcher runs, or None when it can't load PyObjC.
    For doctor; it starts nothing."""
    python = core.data_path(os.path.join('runtime', 'env', 'bin', 'python'))
    if not os.access(python, os.X_OK):
        return None
    try:
        r = subprocess.run([python, '-I', '-B', '-c', 'import platform, sys, objc, AppKit; print(objc.__version__, '
                            'platform.python_version(), int(bool(getattr(sys, "_framework", ""))))'],
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    words = r.stdout.split()
    if r.returncode != 0 or len(words) != 3:
        return None
    return {'pyobjc': words[0], 'python': words[1], 'framework': words[2] == '1'}


FRAMEWORK = ("Its Python is a framework build (python.org's or Homebrew's), which can't run from an app of its own, "
             "and macOS 26 may not show its item. Run: switchboard update")      # it moves the menu bar off one


def macos_major():
    import platform
    head = (platform.mac_ver()[0] or '').split('.')[0]
    return int(head) if head.isdigit() else None


# ---------- `switchboard menubar` ----------

USAGE = 'switchboard menubar [--applications]'


def terminal():
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def main(args):
    """`switchboard menubar`: run the menu bar (its LaunchAgent does this at login). In a terminal, with that job
    added, the job is started instead, so the item outlives the terminal. `--applications` puts its app in
    Applications, as the installer does, where Spotlight finds it and menu bar managers can keep it in view.
    `--opened` is what the app runs when it is opened from Finder, Spotlight or Launchpad (menubar_wrap.py)."""
    if [a for a in args if a not in ('--applications', '--opened')] or len(args) > 1:
        print(USAGE)
        return 2
    if not appkit_here():
        hop = launcher()
        if not os.environ.get(HOPPED) and os.path.isfile(hop) and os.access(hop, os.X_OK):
            os.environ[HOPPED] = '1'        # the launcher's Python has PyObjC; if it does not, the line below says so
            os.execv(hop, [hop] + list(args))
        print(NOT_HERE)
        return 1 if terminal() else 0       # under its job 0: launchd starts a job that failed again every 20 s
    if '--applications' in args:
        return to_applications()
    if '--opened' in args:
        return opened()
    job = our_job() if terminal() else None
    if job:
        if running_pid() is not None:
            print('The menu bar is already running.')
            return 0
        if launchctl('kickstart', '%s/%s' % (domain(), AGENT)) == 0 or launchctl('bootstrap', domain(), job) == 0:
            print('The menu bar is on.')
            return 0
    return run()


def to_applications():
    """The app in Applications, where the installer puts it (menubar_wrap.place), and the item started again from
    there when it runs under its job."""
    from . import menubar_wrap as wrap
    path, said = wrap.place()
    for line in said:
        print(line)
    if path is None:
        print("The menu bar's app stays in %s." % wrap.short(folder()))
        return 1
    print("The menu bar's app is in %s." % wrap.short(path))
    if running_pid() is not None and our_job() and launchctl('kickstart', '-k', '%s/%s' % (domain(), AGENT)) == 0:
        print('The menu bar started again from there.')
    elif running_pid() is not None:
        print('Quit the menu bar and start it again to run it from there.')
    return 0


def open_page():
    """The page in the browser, as Open Switchboard in the menu opens it. True when macOS took it."""
    try:
        return subprocess.call(['/usr/bin/open', 'http://localhost:%d/' % core.port()], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=30) == 0
    except (OSError, subprocess.SubprocessError):
        return False


def opened():
    """Switchboard.app was opened from Finder, Spotlight or Launchpad: the page when the menu bar runs (never a second
    item), else the menu bar, through its job when there is one (so it runs as it does after a login), else here."""
    if running_pid() is not None:
        return 0 if open_page() else 1
    job = our_job()
    if job and (launchctl('kickstart', '%s/%s' % (domain(), AGENT)) == 0 or launchctl('bootstrap', domain(), job) == 0):
        return 0
    return run()


def run():
    """The menu bar in this process, until Quit: into its wrapper app first (before AppKit loads), then one menu
    bar per data folder."""
    from . import menubar_wrap as wrap
    wrap.relaunch_inside(['menubar'])          # returns only when staying in this interpreter
    if not take_lock():
        print('The menu bar is already running.')
        return 0
    rotate_log()
    _watch_start()
    here = wrap.running_app()
    if here and wrap.in_applications(here):
        wrap.tidy(here)                     # its copies elsewhere: none runs now, this one holds the lock
    if wrap.framework() and (macos_major() or 0) >= 26:
        sys.stderr.write(FRAMEWORK + '\n')  # it fails without a word otherwise: the process runs, no item shows
        sys.stderr.flush()
    try:
        ui = _ui()
    except Exception as e:  # noqa: BLE001 - PyObjC is here but does not load (a wheel for another Python, say)
        print(cant_start(e))
        return 1 if terminal() else 0       # under its job 0: launchd starts a job that failed again every 20 s
    if terminal():
        print('The menu bar is on. Ctrl-C stops it.')
        sys.stdout.flush()
    return ui['run']()


LOG = 'menubar.log'         # the menu bar job's own output (install.sh: its StandardOutPath and StandardErrorPath)


def _watch_start():
    """A menu bar that runs: a quit mark from before is gone (it runs again), and its watch on the page starts.
    Nothing here can stop the menu bar."""
    from . import watch
    try:
        watch.clear_quit()
        watch_page()
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("The menu bar's watch on the page did not start: %r\n" % e)


def rotate_log():
    """menubar.log never grows without bound: past 1 MB, while it is this process's output, it becomes
    menubar.log.1 and a fresh file takes its place (core.rotate_own_output). Never raises."""
    try:
        return core.rotate_own_output(core.data_path(LOG))
    except Exception:  # noqa: BLE001 - the menu bar runs on whatever its log does
        return False


def cant_start(error):
    """One line for a menu bar whose drawing side does not load."""
    said = (str(error).strip().splitlines() or [''])[0] or error.__class__.__name__
    return "The menu bar can't start: %s. Run: switchboard update" % said.rstrip('.')


# ---------- AppKit: imported only here, when the menu bar runs ----------

TOKENS = {      # (light, dark); ink, ink2 and ink3 are the system's label colours in a menu
    'track': ((0, 0, 0, 0.09), (1, 1, 1, 0.12)),
    'fill': ((0x8e / 255.0, 0x8e / 255.0, 0x93 / 255.0, 1), (0x8e / 255.0, 0x8e / 255.0, 0x93 / 255.0, 1)),
    'accent': ((0x00 / 255.0, 0x71 / 255.0, 0xe3 / 255.0, 1), (0x3b / 255.0, 0x9e / 255.0, 0xff / 255.0, 1)),
    'red': ((0xd7 / 255.0, 0x00 / 255.0, 0x15 / 255.0, 1), (0xff / 255.0, 0x61 / 255.0, 0x66 / 255.0, 1)),
    'hover': ((0, 0, 0, 0.06), (1, 1, 1, 0.09)),
}

_UI = {}


def _ui():
    """The AppKit side, made once per process (Objective-C class names are global)."""
    if _UI:
        return _UI
    import objc
    import AppKit
    from AppKit import (NSApp, NSApplication, NSAttributedString, NSBezierPath, NSColor, NSFont,
                        NSImage, NSMenu, NSMenuItem, NSMutableParagraphStyle, NSStatusBar, NSView, NSWorkspace)
    from Foundation import NSObject, NSRunLoop, NSRunLoopCommonModes, NSTimer, NSURL, NSMakeRect, NSMakeSize
    from PyObjCTools import AppHelper

    dark_names = (AppKit.NSAppearanceNameDarkAqua, AppKit.NSAppearanceNameVibrantDark,
                  AppKit.NSAppearanceNameAccessibilityHighContrastDarkAqua,
                  AppKit.NSAppearanceNameAccessibilityHighContrastVibrantDark)

    def is_dark(appearance):
        if appearance is None:
            return False
        name = appearance.bestMatchFromAppearancesWithNames_([AppKit.NSAppearanceNameAqua,
                                                              AppKit.NSAppearanceNameDarkAqua])
        return name in dark_names

    def rgba(c):
        return NSColor.colorWithSRGBRed_green_blue_alpha_(*c)

    def token(name):
        light, dark = TOKENS[name]
        return NSColor.colorWithName_dynamicProvider_('switchboard.' + name,
                                                      lambda ap: rgba(dark) if is_dark(ap) else rgba(light))

    colours = {'ink': NSColor.labelColor(), 'ink2': NSColor.secondaryLabelColor(),
               'ink3': NSColor.tertiaryLabelColor()}
    colours.update((k, token(k)) for k in TOKENS)

    weights = {400: AppKit.NSFontWeightRegular, 500: AppKit.NSFontWeightMedium, 600: AppKit.NSFontWeightSemibold}
    fonts = {}

    def font(size, weight):
        key = (size, weight)
        if key not in fonts:       # the system font, with digits of one width (the page's tabular figures)
            fonts[key] = NSFont.monospacedDigitSystemFontOfSize_weight_(size, weights[weight])
        return fonts[key]

    def runs(parts, cols):
        text = AppKit.NSMutableAttributedString.alloc().init()
        for words, size, weight, colour in parts:
            text.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(
                words, {AppKit.NSFontAttributeName: font(size, weight),
                        AppKit.NSForegroundColorAttributeName: cols[colour]}))
        return text

    def biggest(parts):
        return max((font(size, weight) for _, size, weight, _ in parts), key=lambda f: f.ascender() - f.descender())

    def draw_line(text, parts, x, y, w, h, align, baseline=None):
        """One line of text in the box, cut short with … when too wide. The baseline is the box's middle for the
        biggest font in it, unless given."""
        f = biggest(parts)
        if baseline is None:
            baseline = y + (h - (f.ascender() - f.descender())) / 2 + f.ascender()
        style = NSMutableParagraphStyle.alloc().init()
        style.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        style.setAlignment_(AppKit.NSTextAlignmentRight if align == 'right' else AppKit.NSTextAlignmentLeft)
        text.addAttribute_value_range_(AppKit.NSParagraphStyleAttributeName, style, (0, text.length()))
        top = baseline - f.ascender()
        text.drawWithRect_options_context_(NSMakeRect(x, top, max(0.0, w), f.ascender() - f.descender() + 2),
                                           AppKit.NSStringDrawingUsesLineFragmentOrigin
                                           | AppKit.NSStringDrawingTruncatesLastVisibleLine, None)
        return baseline

    def paint(ops, cols):
        for op in ops:
            if op[0] == 'box':
                _, x, y, w, h, r, colour = op
                cols[colour].setFill()
                NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(x, y, w, h), r, r).fill()
            elif op[0] == 'text':
                _, x, y, w, h, parts, align = op
                draw_line(runs(parts, cols), parts, x, y, w, h, align)
            elif op[0] == 'pair':
                _, x, y, w, h, left, pieces, gap = op
                every = left + [p for piece in pieces for p in piece]
                f = biggest(every)
                baseline = y + (h - (f.ascender() - f.descender())) / 2 + f.ascender()
                right = x + w
                for piece in reversed(pieces):
                    text = runs(piece, cols)
                    width = text.size().width
                    draw_line(text, piece, right - width, y, width + 1, h, 'left', baseline)
                    right -= width + gap
                if left and left[0][0]:
                    draw_line(runs(left, cols), left, x, y, right - x if pieces else w, h, 'left', baseline)

    def item_image(r):
        """The item: a template image, or at a switch line a colour one (the system red and the label colour,
        worked out each time it is drawn, so it follows the menu bar's own light or dark)."""
        ops, red = item_ops(r), r['red']

        def draw(rect):
            if red:
                ink = NSColor.labelColor().colorUsingColorSpace_(AppKit.NSColorSpace.sRGBColorSpace()) \
                    or NSColor.labelColor()
                cols = {'ink': ink, 'ink30': ink.colorWithAlphaComponent_(ink.alphaComponent() * TRACK),
                        'sysred': NSColor.systemRedColor()}
            else:
                cols = {'ink': NSColor.blackColor(), 'ink30': NSColor.colorWithWhite_alpha_(0, TRACK),
                        'ink50': NSColor.colorWithWhite_alpha_(0, 0.5)}     # numbers that are not current
            paint(ops, cols)
            return True
        image = NSImage.imageWithSize_flipped_drawingHandler_(NSMakeSize(IMAGE_W, IMAGE_H), True, draw)
        image.setTemplate_(not red)
        image.setCacheMode_(AppKit.NSImageCacheNever)
        image.setAccessibilityDescription_(r['label'])
        return image

    class SwitchboardRow(NSView):
        """One account in the menu: drawn from row_ops; a click uses it."""

        def initWithFrame_(self, frame):
            self = objc.super(SwitchboardRow, self).initWithFrame_(frame)
            if self is None:
                return None
            self.row = None
            self.hover = False
            area = AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                AppKit.NSZeroRect, AppKit.NSTrackingMouseEnteredAndExited | AppKit.NSTrackingActiveAlways
                | AppKit.NSTrackingInVisibleRect, self, None)
            self.addTrackingArea_(area)
            self.setAccessibilityElement_(True)
            self.setAccessibilityRole_(AppKit.NSAccessibilityButtonRole)
            return self

        def isFlipped(self):
            return True

        @objc.python_method
        def show(self, row):
            self.row = row
            self.setAccessibilityLabel_(row_label(row))
            self.setNeedsDisplay_(True)

        def drawRect_(self, rect):
            if self.row is None:
                return
            item = self.enclosingMenuItem()
            lit = self.hover or (item is not None and item.isHighlighted())
            paint(row_ops(self.row, hover=lit, width=self.bounds().size.width), colours)

        def mouseEntered_(self, event):
            self.hover = True
            self.setNeedsDisplay_(True)

        def mouseExited_(self, event):
            self.hover = False
            self.setNeedsDisplay_(True)

        def mouseUp_(self, event):
            self.press()

        def accessibilityPerformPress(self):
            return self.press()

        @objc.python_method
        def press(self):
            """Close the menu, then do what the row's item does (a view in a menu gets the click, not the item)."""
            item = self.enclosingMenuItem()
            if self.row is None or not self.row['action'] or item is None:
                return False
            item.menu().cancelTracking()
            NSApp().sendAction_to_from_(item.action(), item.target(), item)
            return True

    class SwitchboardMenuBar(NSObject):
        """The item, its menu and the readings behind them."""

        def init(self):
            self = objc.super(SwitchboardMenuBar, self).init()
            if self is None:
                return None
            self.pace = Pace()
            self.watch = Watch()        # the last good state and how old it is
            self.state = None
            self.reading = False
            self.open = False
            self.timer = None
            self.note = None            # (words, until)
            self.items = []
            self.views = {}
            self.auto_item = None
            self.lit = None
            return self

        # -- set up --

        def applicationDidFinishLaunching_(self, notification):
            self.status = NSStatusBar.systemStatusBar().statusItemWithLength_(ITEM_W)
            self.status.setAutosaveName_(AGENT)
            button = self.status.button()
            button.setImagePosition_(AppKit.NSImageOnly)
            button.setImageScaling_(AppKit.NSImageScaleNone)
            self.menu = NSMenu.alloc().initWithTitle_('Switchboard')
            self.menu.setAutoenablesItems_(False)
            self.menu.setDelegate_(self)
            self.status.setMenu_(self.menu)
            self.show_item({'email': None, 'five': None, 'week': None, 'red': False, 'label': 'Switchboard'})
            self.fill([('do', 'Quit', 'quit')])         # until the first reading, a moment from now
            centre = NSWorkspace.sharedWorkspace().notificationCenter()
            centre.addObserver_selector_name_object_(self, 'didWake:', AppKit.NSWorkspaceDidWakeNotification, None)
            self.read()

        # -- reading --

        @objc.python_method
        def schedule(self):
            if self.timer is not None:
                self.timer.invalidate()
            now = time.monotonic()      # durations: a clock set back never freezes the readings
            wait = self.pace.wait(now, self.open)
            t = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(max(wait, 0.05), self, 'tick:',
                                                                                None, False)
            t.setTolerance_(0.2 if self.pace.every(now, self.open) <= EVERY_OPEN else 1.5)
            NSRunLoop.mainRunLoop().addTimer_forMode_(t, NSRunLoopCommonModes)     # fires while the menu is open
            self.timer = t

        def tick_(self, timer):
            self.timer = None
            if self.reading:
                return                  # the reading under way schedules the next one when it is back
            if self.pace.due(time.monotonic(), self.open):
                self.read()
            else:
                self.schedule()

        @objc.python_method
        def read(self):
            self.reading = True
            self.pace.started(time.monotonic())
            # took() always runs, whatever the reading met: it is what sets reading back and schedules the next one

            def look():
                why = []
                return get_state(why=why), (why[0] if why else None)
            in_background(look, self.took, lambda e: None, AppHelper.callAfter)

        @objc.python_method
        def took(self, got):
            self.reading = False
            state, why = got if isinstance(got, tuple) and len(got) == 2 else (None, 'unreadable')
            self.watch.took(state, why, time.monotonic(), time.time())
            rotate_log()
            self.state = self.watch.state       # the last good state stays on screen when a reading fails
            try:
                self.redraw()
            except Exception:  # noqa: BLE001 - an answer it can't draw is logged; the next reading may be fine
                import traceback
                traceback.print_exc()
            self.schedule()

        def didWake_(self, notification):
            if not self.reading and self.pace.due(time.monotonic(), True):
                self.read()

        # -- drawing --

        @objc.python_method
        def redraw(self):
            state, status = self.watch.view(time.monotonic())
            self.show_item(reading(state, status))
            note = self.note[0] if self.note and self.note[1] > time.monotonic() else watch_note()
            self.fill(menu(state, note, status=status))

        @objc.python_method
        def show_item(self, r):
            button = self.status.button()
            button.setImage_(item_image(r))
            button.setToolTip_(r['label'])
            button.setAccessibilityLabel_(r['label'])

        @objc.python_method
        def fill(self, items):
            if shape(items) == shape(self.items):
                for x in items:                         # the same menu: new numbers, in place
                    if x[0] == 'row':
                        self.views[x[1]['email']].show(x[1])
                    elif x[0] == 'auto':
                        self.show_auto(self.auto_item, x)
                self.items = items
                return
            self.items, self.views, self.auto_item = items, {}, None
            self.menu.removeAllItems()
            for x in items:
                if x[0] == 'separator':
                    self.menu.addItem_(NSMenuItem.separatorItem())
                    continue
                if x[0] in ('note', 'line'):
                    mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(x[1], None, '')
                    mi.setEnabled_(False)
                elif x[0] == 'row':
                    row = x[1]
                    key = str(row['slot']) if row['action'] and isinstance(row['slot'], int) and 0 < row['slot'] < 10 \
                        else ''
                    mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(row['email'], 'use:', key)
                    mi.setKeyEquivalentModifierMask_(0)
                    mi.setTarget_(self)
                    mi.setRepresentedObject_(row['email'])
                    mi.setEnabled_(bool(row['action']))
                    view = SwitchboardRow.alloc().initWithFrame_(NSMakeRect(0, 0, ROW_W, row_height(row)))
                    view.show(row)
                    mi.setView_(view)
                    self.views[row['email']] = view
                elif x[0] == 'auto':
                    mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_('Auto-switch', 'auto:', '')
                    mi.setTarget_(self)
                    self.auto_item = mi
                    self.show_auto(mi, x)
                else:
                    mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(x[1], x[2] + ':', '')
                    mi.setTarget_(self)
                self.menu.addItem_(mi)

        @objc.python_method
        def show_auto(self, mi, x):
            _, on, words, can = x
            lit = mi is self.lit and can
            # An attributed title keeps its colours when highlighted: white on the highlight, grey when it can't
            # be changed (no engine), the words quieter while it is off, as on the page.
            main = NSColor.selectedMenuItemTextColor() if lit else NSColor.labelColor() if can \
                else NSColor.tertiaryLabelColor()
            quiet = main if lit or not can else NSColor.secondaryLabelColor() if on else NSColor.tertiaryLabelColor()
            size = NSFont.menuFontOfSize_(0).pointSize()
            title = AppKit.NSMutableAttributedString.alloc().init()
            for text, colour in (('Auto-switch', main), ('  ' + words, quiet)):
                title.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(
                    text, {AppKit.NSFontAttributeName: NSFont.menuFontOfSize_(size),
                           AppKit.NSForegroundColorAttributeName: colour}))
            mi.setAttributedTitle_(title)
            mi.setState_(AppKit.NSControlStateValueOn if on else AppKit.NSControlStateValueOff)
            mi.setEnabled_(can)

        # -- the menu opening and closing --

        def menuWillOpen_(self, m):
            self.open = True
            if not self.reading:
                self.schedule()         # due at once when the last reading is 2 s old or more

        def menuDidClose_(self, m):
            self.open = False
            self.lit = None
            if not self.reading:
                self.schedule()

        def menu_willHighlightItem_(self, m, item):
            was, self.lit = self.lit, item
            for x in self.items:
                if x[0] == 'auto' and self.auto_item is not None and (was is self.auto_item or item is self.auto_item):
                    self.show_auto(self.auto_item, x)

        # -- what the menu does --

        @objc.python_method
        def send(self, what, path, body):
            def back(answer):
                ok, said = answer if isinstance(answer, tuple) and len(answer) == 2 else (False, UNREADABLE)
                self.sent(what, ok, said)
            in_background(lambda: post(path, body), back, lambda e: (False, UNREADABLE), AppHelper.callAfter)

        @objc.python_method
        def sent(self, what, ok, said):
            self.note = None if ok else (failed(what, said), time.monotonic() + NOTE_FOR)
            self.pace.soon()
            if not self.reading:
                self.schedule()
            if not ok:
                try:
                    self.redraw()
                except Exception:  # noqa: BLE001
                    import traceback
                    traceback.print_exc()

        def use_(self, sender):
            email = sender.representedObject()
            row = next((x[1] for x in self.items if x[0] == 'row' and x[1]['email'] == email), None)
            if row is None:
                return
            if row['action'] == 'signin':           # a fresh sign-in starts on the page
                self.open_(sender)
                return
            sent = click(row)
            if sent:
                self.send("Couldn't switch", *sent)

        def auto_(self, sender):
            x = next((x for x in self.items if x[0] == 'auto'), None)
            if x is not None:
                self.send("Couldn't change Auto-switch", '/api/auto', {'enabled': not x[1]})

        def open_(self, sender):
            NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_('http://localhost:%d/' % core.port()))

        def signin_(self, sender):
            """Sign in again as …: a fresh sign-in starts on the page, as a row that needs one does."""
            self.open_(sender)

        def signinall_(self, sender):
            """Sign in to all: the page's own queue (POST /api/signins); each sign-in opens in the browser for its yes,
            and the page shows where it is."""
            self.send("Couldn't start signing in", '/api/signins', {'do': 'start'})

        def applicationShouldHandleReopen_hasVisibleWindows_(self, app, visible):
            """Switchboard.app opened again while this runs (Finder, Spotlight, Launchpad): macOS hands the open to
            this copy, so no second item starts. It opens the page."""
            self.open_(None)
            return False

        def start_(self, sender):
            def back(why):
                if why:
                    self.sent("Couldn't start Switchboard", False, why)
            in_background(lambda: start_page() and None, back, lambda e: str(e) or e.__class__.__name__,
                          AppHelper.callAfter)
            self.pace.hurry(time.monotonic())
            self.pace.soon()
            if not self.reading:
                self.schedule()

        def quit_(self, sender):
            try:
                from . import watch
                watch.note_quit()               # chosen: neither launchd nor the page starts it again
            except Exception:  # noqa: BLE001 - Quit always quits
                pass
            NSApp().terminate_(None)

    def run():
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)    # no Dock icon, no Cmd-Tab
        bar = SwitchboardMenuBar.alloc().init()
        app.setDelegate_(bar)
        AppHelper.installMachInterrupt()        # Ctrl-C in a terminal quits at once
        _UI['bar'] = bar
        app.run()
        return 0

    _UI.update(run=run, paint=paint, item_image=item_image, colours=colours, Row=SwitchboardRow,
               MenuBar=SwitchboardMenuBar, is_dark=is_dark)
    return _UI
