"""Sign in to all: one press signs in again to every account that needs it, one after another.

Anthropic ends each sign-in by a set date, and only a new sign-in moves it. Signed in again together, the accounts'
dates fall together, so the next time is again a single press. Which accounts (pick): every kept account whose sign-in
has ended (or was refused), or ends within the week, or ends more than a day before the latest-ending account's date;
the ended ones first, then the soonest to end. An account already in step with the newest sign-in is left alone, and so
is one whose date is not known. This goes by the dates the engine gives (refresh_expires_at, the page's
signin_ends_at), never by an assumed length of a sign-in.

Each one is exactly a row's Sign in: the server hands START the same route (POST /api/connect, connect.start): Claude
Code's own sign-in in a throwaway folder, its link opened in that account's browser profile, the new login kept by the
engine. The next one starts when the one before is over: kept, or failed, stopped (its Cancel), or not approved within
connect.TIMES_OUT. One that is not kept is skipped, and the closing line names it by its whole email. Stop ends the
queue, cancels the sign-in still waiting (connect.stop leaves one that has just finished to be kept), and leaves what
is done as done. A second press while it runs starts nothing more.

The queue lives in this process's memory, so every open tab draws the same thing (view(), in /api/state). A restart
of the page's job leaves no half state: a sign-in the browser approved is taken up and kept at the next start
(connect.sweep), the rest is simply not started, and the note above the accounts comes back by itself.

THE ONE RULE NOBODY CHANGES HERE: every sign-in completes on Anthropic's own page, by the person's own click. This
queue only opens each sign-in link the way a row's Sign in does (connect.open_sign_in). It never presses, fills or
scripts anything on that page, never drives the browser beyond opening that link, and never keeps a password or a
browser cookie. A faster queue that approves for the person is not an improvement; it is not allowed.
"""
import threading
import time

from . import connect
from .core import SwitchError, log

WEEK = 7 * 86400            # an account whose sign-in ends within this is taken (the page's own "a week ahead")
IN_STEP = 86400             # an account whose date is within this of the latest-ending account's is in step: left alone
SHOW_DONE = 30              # seconds the closing line stays when every account was signed in
SHOW_SKIPPED = 600          # ... and when one was not (until hidden)
LOOK_EVERY = 0.5            # seconds between two looks at whether the sign-in under way is over
RESULT_GRACE = 5            # seconds an over sign-in is given to say how it ended, before it counts as stopped

START = None                # the server's: start(email) -> the key connect.start returned (the row's own route)
SAY = None                  # the server's: say(text, kind) into the page's History
WAKE = None                 # the server's: wake the page's loop (a closing line is drawn at once)

LOCK = threading.Lock()
RUN = None                  # the queue under way, or the last one while its closing line is shown


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and 0 < v < 1e11


def pick(accounts, now):
    """The emails Sign in to all takes, in order, from the page's accounts ({email, slot, needs_login, unmanaged,
    signin_ends_at, connected})."""
    kept = [a for a in accounts if a.get('email') and a.get('slot') and not a.get('unmanaged') and
            a.get('connected', True) is not False]
    dates = [a['signin_ends_at'] for a in kept if _num(a.get('signin_ends_at')) and not a.get('needs_login')]
    latest = max(dates) if dates else None
    ended, soon = [], []
    for a in kept:
        at = a['signin_ends_at'] if _num(a.get('signin_ends_at')) else None
        if a.get('needs_login'):
            ended.append((at or 0, a['email']))
        elif at is not None and (at - now <= WEEK or (latest is not None and latest - at > IN_STEP)):
            soon.append((at, a['email']))
    return [e for _, e in sorted(ended)] + [e for _, e in sorted(soon)]


class Queue(object):
    """One sitting of Sign in to all."""

    def __init__(self, emails):
        self.emails = list(emails)
        self.current = None             # the email being signed in now
        self.waiting = False            # its browser approved it: the engine is keeping it
        self.kept = []
        self.skipped = []               # [(email, why)]
        self.stopped = False
        self.started = time.time()
        self.ended = None
        self.line = None
        self.hidden = False


def running():
    q = RUN
    return q is not None and q.ended is None


def start(emails):
    """Begin a sitting over these emails (pick's order). While one runs, nothing more starts: returns that one."""
    global RUN
    with LOCK:
        if running():
            return RUN
        q = RUN = Queue(emails)
    if not q.emails:
        _close(q)
        return q
    threading.Thread(target=_work, args=(q,), name='sign-in-to-all', daemon=True).start()
    return q


def stop():
    """End the sitting: no more sign-ins start, and the one still waiting is cancelled (connect.stop keeps one that has
    just finished). What was kept stays kept."""
    q = RUN
    if q is not None and q.ended is None:
        q.stopped = True
    return q


def hide():
    """Take the closing line away now."""
    q = RUN
    if q is not None and q.ended is not None:
        q.hidden = True


def reset():
    """Forget every sitting (a fresh start of the page's job has none)."""
    global RUN
    with LOCK:
        RUN = None


def _first(text):
    text = (text or '').strip()
    i = text.find('. ')
    return (text[:i] if i > 0 else text).rstrip('.')


def _work(q):
    try:
        for email in q.emails:
            if q.stopped:
                break
            q.current, q.waiting = email, False
            try:
                key = START(email)
            except SwitchError as e:
                q.skipped.append((email, _first(str(e))))
                continue
            except Exception as e:  # noqa: BLE001 - one that could not start is skipped; the queue goes on
                log('sign in to all: the sign-in for one account did not start: %r' % e)
                q.skipped.append((email, 'the sign-in did not start'))
                continue
            got = _wait(q, key)
            if got.get('ok'):
                q.kept.append(email)
            elif q.stopped and got.get('why') == 'stopped':
                break                                   # the Stop cancelled it: not one that failed
            else:
                q.skipped.append((email, got.get('why') or 'the sign-in did not finish'))
    except Exception as e:  # noqa: BLE001 - the queue always ends with its line
        log('sign in to all stopped: %r' % e)
    finally:
        _close(q)


def _wait(q, key):
    """Wait until this sign-in is over, and return how it ended (connect's result on it). The Stop cancels it."""
    c = connect.RUNNING.get(key)
    if c is None:
        return {'ok': False, 'why': 'the sign-in did not start'}
    cancelled, over_at = False, None
    while True:
        if q.stopped and not cancelled and not c.get('over'):
            cancelled = True
            connect.stop(key)                           # one that has just finished is left to be kept
        if c.get('over'):
            if c.get('result') or (over_at is not None and time.time() - over_at > RESULT_GRACE):
                return c.get('result') or {'ok': False, 'why': 'stopped'}
            over_at = over_at or time.time()
        q.waiting = connect.under_way(c) == 'finished'
        time.sleep(LOOK_EVERY)


def _close(q):
    q.current, q.waiting = None, False
    q.line = closing(q)
    if q.emails:
        try:
            if SAY:
                SAY(q.line, 'warn' if q.skipped else 'info')
        except Exception as e:  # noqa: BLE001
            log('sign in to all: its line was not said: %r' % e)
    q.ended = time.time()
    try:
        if WAKE:
            WAKE()
    except Exception:  # noqa: BLE001
        pass


def closing(q):
    """The one line said when a sitting ends: who was signed in, and each one that was not, by its whole email."""
    n, k = len(q.emails), len(q.kept)
    if not n:
        return 'No account needs a sign-in now.'
    why = '; '.join('%s (%s)' % (e, w) for e, w in q.skipped)
    if q.stopped:
        return 'Stopped. Signed in again to %d of %d.%s' % (k, n, (' Not signed in: %s.' % why) if why else '')
    if not q.skipped:
        return 'Signed in again to %s.' % (q.kept[0] if n == 1 else 'all %d accounts' % n)
    return 'Signed in again to %d of %d. Not signed in: %s.' % (k, n, why)


def due(accounts, now):
    """The one source for the note above the page's accounts, its button, the menu bar and the palette: {'due': the
    emails a press of Sign in to all opens, in the order they open (pick), 'ended': those of them whose sign-in has
    ended, by pick's own rule (a sign-in is needed: its date passed, or Anthropic refused its login), 'week': those
    that still work and end within the week}. An account pick takes only because it ends well before the others is
    opened but in neither count: it does not end this week."""
    out = {'due': pick(accounts, now), 'ended': [], 'week': []}
    by = dict((a.get('email'), a) for a in accounts)
    for e in out['due']:
        a = by.get(e) or {}
        at = a['signin_ends_at'] if _num(a.get('signin_ends_at')) else None
        if a.get('needs_login'):
            out['ended'].append(e)
        elif at is not None and now < at and at - now <= WEEK:
            out['week'].append(e)
    return out


def view(accounts, now):
    """What every open tab draws (in /api/state, as 'signins'), with due()'s three lists in each: {'state': 'idle'};
    {'state': 'running', 'current', 'waiting', 'done', 'of', 'emails', 'kept', 'skipped'}; or {'state': 'ended',
    'line', 'skipped'} while its closing line is shown."""
    q = RUN
    lists = due(accounts, now)
    if q is None:
        return dict({'state': 'idle'}, **lists)
    skipped = [{'email': e, 'why': w} for e, w in q.skipped]
    if q.ended is None:
        return dict({'state': 'running', 'current': q.current, 'waiting': bool(q.waiting),
                     'done': len(q.kept) + len(q.skipped), 'of': len(q.emails), 'emails': list(q.emails),
                     'kept': list(q.kept), 'skipped': skipped}, **lists)
    shown = SHOW_SKIPPED if q.skipped else SHOW_DONE
    if q.hidden or not q.emails or time.time() - q.ended > shown:
        return dict({'state': 'idle'}, **lists)
    return dict({'state': 'ended', 'line': q.line, 'kept': list(q.kept), 'skipped': skipped}, **lists)
