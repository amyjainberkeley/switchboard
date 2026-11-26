"""Mac notifications for the moments that matter, at most one for each change.

notify(title, body) shows one through osascript with a fixed `on run argv` script and the title and body as
its arguments, run directly, never through a shell. The functions below are pure: each turns one change into at
most one Note, or None. send() drops a Note that was already shown (a small JSON file remembers, written before
osascript runs, so a cut-off run never shows it twice) and shows the rest, at most RATE in RATE_WINDOW seconds; the
ones over that are held and said together by flush_held().

"Done" and "needs you" come from Claude Code hooks, which the docs describe as supported signals
(https://code.claude.com/docs/en/hooks, checked 2026-11-14):
- Stop: "Runs when the main Claude Code agent has finished responding. Does not run if the
  stoppage occurred due to a user interrupt." Its `background_tasks` "let hooks distinguish
  'session is done' from 'session is paused waiting for background work to wake it back up'".
- Notification, by `notification_type`: `permission_prompt` "Claude needs you to approve a tool
  use [...]", `elicitation_dialog` and `elicitation_url_dialog` (an MCP server asks for input), and
  `agent_needs_input` (a background session waits on you). `idle_prompt` ("Claude finished
  responding about 60 seconds ago and you haven't typed since") follows every answer, short ones
  too, so it is not a "done": Stop is, for a turn of a minute or more.
- `agent_id`: "Unique identifier for the subagent or in-process teammate the hook fires in." Helpers
  and workflow agents never get a note.
from_hook() turns those hook inputs into a Note. The hooks themselves are an entry in Claude Code's
settings, which notify_hooks.py adds and takes out when Notifications is turned on and off; it also
notes when each turn starts, so a short answer stays quiet.
"""
import collections
import fcntl
import json
import os
import subprocess
import time
from datetime import datetime

try:
    from .statusline import data_dir, when
except ImportError:         # a Claude Code hook loads this file on its own, with its folder on the path (notify_hooks)
    from statusline import data_dir, when

OSASCRIPT = '/usr/bin/osascript'
LINE = 90                   # percent used at which an account is "near its line"
PLUG_IN_EVERY = 30 * 60
KEEP = 8 * 86400            # forget shown notes after this long
SAME_WINDOW = 3600          # two reset times this close are the same window
AHEAD = 5                   # a remembered time further than this past the clock is not trusted
WINDOW_NAMES = {'five_hour': '5-hour', 'seven_day': 'weekly'}
WINDOW_SECONDS = {'five_hour': 5 * 3600, 'seven_day': 7 * 86400}
NEEDS_YOU = ('permission_prompt', 'elicitation_dialog', 'elicitation_url_dialog', 'agent_needs_input')
# The words of these come from outside Claude Code (an MCP server, a background session), and a note shows on the lock
# screen: a fixed sentence instead.
OUTSIDE = {'elicitation_dialog': 'An MCP server is asking you something.',
           'elicitation_url_dialog': 'An MCP server is asking you to open a link.',
           'agent_needs_input': 'A background session is waiting for you.'}
LONG_TURN = 60              # seconds: a turn this long or longer gets a "done"

# key: what the note is about. instance: which occurrence (a reset time, a prompt id); the same key
# and instance is never shown twice. every: for notes without an instance, the least time between two.
Note = collections.namedtuple('Note', 'key title body instance every')


# ---------- showing one ----------

# The title and body reach osascript only as arguments of a fixed script, never as part of its source, so no
# quote of any kind (straight, curly, guillemets) in a name can end a string and run something else.
# `--` ends osascript's options, so a title that starts with "-e" is still only text (without it, osascript reads
# it as one more line of script).
SCRIPT = ['-e', 'on run argv', '-e', 'display notification (item 2 of argv) with title (item 1 of argv)', '-e', 'end run',
          '--']


def _clean(text, limit):
    """Text safe to show: control and separator characters (C0, DEL, C1, line and paragraph separators) become
    spaces, runs of spaces close up, and text longer than limit is cut with an ellipsis."""
    text = ''.join(c if c >= ' ' and c != '\x7f' and not '\x80' <= c <= '\x9f' and c not in '  ' else ' '
                   for c in str(text))
    text = ' '.join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


TIMEOUT = 'timeout'         # _run's answer when osascript did not finish in time: the note may have shown


def _run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, timeout=10).returncode
    except subprocess.TimeoutExpired:
        return TIMEOUT
    except (OSError, subprocess.SubprocessError):
        return None


def _show(title, body, run=_run):
    """osascript's answer for one notification: 0 (it took it), TIMEOUT, another exit code, or None (it could not
    start)."""
    return run([OSASCRIPT] + SCRIPT + [_clean(title, 80), _clean(body, 240)])


def notify(title, body, run=_run):
    """Show one notification. True when osascript said it worked. No shell is involved, and the text is passed
    as arguments only."""
    return _show(title, body, run) == 0


# macOS shows a notification from osascript as Script Editor's, and osascript says it worked (exit 0) even when Script
# Editor's notifications are turned off: only a person can tell whether one appeared. So the page sends a test and asks.
SCRIPT_EDITOR_OFF = ("macOS shows Switchboard's notifications as Script Editor's. Turn on notifications for Script "
                     "Editor in System Settings › Notifications, and check that no Focus is on.")
TEST_TITLE, TEST_BODY = 'Switchboard', 'A test notification. If you can read this, notifications work.'


def send_test(run=_run):
    """The page's "Send a test": one notification now, never held back or remembered. Returns (shown, words): shown
    is True when osascript took it (then only the person can say whether macOS showed it), with one plain line
    otherwise."""
    code = _show(TEST_TITLE, TEST_BODY, run)
    if code == 0:
        return True, 'Sent. Did a notification appear?'
    if code == TIMEOUT:
        return False, 'macOS took too long to show it. Try again in a moment.'
    if code is None:
        return False, "Switchboard couldn't start osascript, which shows its notifications."
    return False, 'osascript could not show it (it ended with %s).' % code


def account_label(st, email):
    """An account's name in a notification: its whole email, as on every other surface (core.name_of), never a
    short name. Notifications show on the lock screen and in screen shares, and they carry the email there too."""
    if isinstance(email, str) and email.strip():
        return _clean(email, 80)
    return 'An account'



# ---------- the changes worth a notification ----------

def window_of(w):
    """(used percent, reset time) from Claude Code's shape {used_percentage, resets_at (epoch)} or
    Switchboard's {pct, resets (ISO time)}; (None, None) when there is no number."""
    if not isinstance(w, dict):
        return None, None
    used = w.get('used_percentage', w.get('pct'))
    resets = w.get('resets_at', w.get('resets'))
    if isinstance(resets, str):
        try:
            resets = datetime.fromisoformat(resets.replace('Z', '+00:00')).timestamp()
        except ValueError:
            resets = None
    return _finite(used), _finite(resets)


def _finite(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return float(x) if x == x and -1e15 < x < 1e15 else None


def switched(from_name, to_name, why=None, at=None):
    """Claude Code moved to another account."""
    if not to_name or to_name == from_name:
        return None
    body = ' '.join(x for x in (('From %s.' % from_name) if from_name else '', why or '') if x) or 'Claude Code switched.'
    return Note('switch:%s>%s' % (from_name or '', to_name), 'Switched to %s' % to_name, body,
                at, None if at is not None else 60)


def nearing(account, window, w, line=LINE, now=None):
    """An account has reached its line in one window. Once per account per window."""
    now = time.time() if now is None else now
    used, resets = window_of(w)
    if used is None or used < line or window not in WINDOW_NAMES or (resets is not None and resets <= now):
        return None
    left = max(0, int(100 - used))
    body = '%d%% of its %s limit left.' % (left, WINDOW_NAMES[window])
    if resets is not None:
        body += ' Refills %s.' % when(resets, now)
    return Note('near:%s:%s' % (account, window), '%s is near its limit' % account, body,
                resets, None if resets is not None else WINDOW_SECONDS[window])


def refilled(account, window, before, after, line=LINE, now=None):
    """A window that had reached the line has reset. Quiet for windows that never got close."""
    now = time.time() if now is None else now
    used0, resets0 = window_of(before)
    used1, resets1 = window_of(after)
    if used0 is None or used0 < line or window not in WINDOW_NAMES:
        return None
    reset = (resets0 is not None and resets0 <= now) or \
            (resets0 is not None and resets1 is not None and resets1 - resets0 > SAME_WINDOW)
    if not reset or (used1 is not None and used1 >= used0):
        return None
    return Note('refill:%s:%s' % (account, window), '%s refilled' % account,
                'Its %s limit is fresh again.' % WINDOW_NAMES[window], resets0, None)


def plug_in(reason):
    """From awake.should_warn(): the Mac needs power. At most once per half hour."""
    if not reason:
        return None
    return Note('plug-in', 'Plug in your Mac', reason, None, PLUG_IN_EVERY)


DISK_FULL_EVERY = 6 * 3600
# What waits while the disk is almost full (core.DISK_LOW), in the page's own names for those parts: no new previews
# (previews._draw), and no new look at the artifacts, the chats or the week (server.tick, chats_tick, usage_view).
# The page (index.html notesHTML), History (server.disk_tick) and this note say it in these words.
DISK_PAUSED = 'Switchboard paused new previews and updates to Artifacts, Chats and Your week'


def disk_full():
    """The disk is almost full (core.disk_low): Switchboard pauses what it can do without. At most every six hours."""
    return Note('disk-full', 'Your disk is almost full', DISK_PAUSED + ' until there is room. Free some disk space.',
                None, DISK_FULL_EVERY)


AUTO_ON = ('Auto-switch is on: Claude Code moves to another account before a limit. Turn it off with '
           'switchboard auto off.')


def auto_on():
    """Auto-switch was turned on by itself (keep.auto_by_itself). Shown once."""
    return Note('auto-on', 'Switchboard', AUTO_ON, None, None)


def port_taken(words, since):
    """Another program holds the page's port (watch.port_words). Once for each spell: `since` is when it began."""
    return Note('port-taken', 'Switchboard', words, 'since %s' % since, None)


# The times Switchboard is not doing its job (watch.troubles, sent by the page's tick through watch.Spells). Each is
# said once for each spell (`since`: when that spell began), with the one thing to do; "works again" only for the first
# two. "The page is down" is the menu bar's to say (page_back, page_down): the page can't say it of itself.

DOCTOR = 'If it stays like this, run: switchboard doctor'


def auto_stuck(problem, since):
    """Auto-switch has been stopped, stuck or restarting for minutes, or has decided nothing for minutes. `problem` is
    the page's own line for it (engine.AutoHost.status, or watch.decided_words)."""
    said = ' '.join(str(problem or 'Auto-switch is not running.').split())
    if not said.endswith(('.', '!', '?')):
        said += '.'
    if 'switchboard doctor' not in said:
        said += ' ' + DOCTOR
    return Note('auto-stuck', 'Auto-switch is not working', said, 'since %s' % since, None)


def auto_fine(since):
    """The spell auto_stuck said is over."""
    return Note('auto-fine', 'Auto-switch works again', 'Switchboard reads the accounts and switches again.',
                'since %s' % since, None)


def blocked(name, why, minutes, reason, todo, since):
    """The account in use can't work (`why`: at a limit, or its login refused) and no switch was possible for
    `minutes`: Claude Code is not answering. `reason` is why no switch (the policy's own words), `todo` the one thing to
    do. `name` is the account's whole email, as in every other note."""
    body = '%s. No switch was possible for %d min%s. %s' % (
        why, minutes, (': ' + reason.rstrip('.')) if reason else '', todo)
    return Note('blocked:%s' % name, "Claude Code can't work on %s" % name, body, 'since %s' % since, None)


def blocked_fine(name, since):
    """The account blocked() was about can work again, and Claude Code is still on it."""
    return Note('unblocked:%s' % name, 'Claude Code can work again', 'Claude Code can work on %s again.' % name,
                'since %s' % since, None)


KEYCHAIN_WAITING = ("A Keychain dialog has waited for an answer for %d min, so Switchboard can't read or switch "
                    "logins. Answer it: it may be behind other windows.")


def keychain_waiting(minutes, since):
    """macOS's Keychain has waited for the person's answer for minutes."""
    return Note('keychain-waiting', "macOS's Keychain is waiting for you", KEYCHAIN_WAITING % minutes,
                'since %s' % since, None)


# Where the person sees why: the page's rows say when each account was last checked, and the reason a read failed on
# hover (engine.note_of). Doctor has no line for old numbers.
NUMBERS_OLD = ("Every account's numbers are over an hour old, so auto-switch can't tell when to move Claude Code. "
               "Open Switchboard: each account says when it was last checked, and why on hover.")


def numbers_old(since):
    """Every account's numbers are more than an hour old while auto-switch is on."""
    return Note('numbers-old', 'Switchboard has no fresh numbers', NUMBERS_OLD, 'since %s' % since, None)


# The one press on the page that gets Claude Code signed in again in every state this note is sent in (the put-back
# may not act, holds nothing usable, gave up for now, or its writes fail): Use on an account whose kept sign-in works
# writes it whole, from no login too; when none works, Sign in on the account the settings name puts its new sign-in
# in use at once. Not "Sign in to all": that shows only while sign-ins have ended or end soon.
SIGNIN_LOST_TODO = 'Open Switchboard and press Use on an account, or Sign in on %s.'
SIGNIN_LOST = 'Claude Code has had no sign-in for %d min, and Switchboard could not put %s back. ' + SIGNIN_LOST_TODO


def signin_lost(name, minutes, since):
    """Claude Code lost its sign-in, its settings still name `name` (a kept account, by its whole email), and the
    put-back could not put it back for `minutes` (watch.troubles 'signin'). Once for each spell; never for a sign-out
    the person chose."""
    return Note('signin-lost', 'Claude Code is not signed in', SIGNIN_LOST % (minutes, name, name),
                'since %s' % since, None)


def signin_back(name, since):
    """The spell signin_lost said is over: Claude Code is signed in to `name` (whole email). Not sent when the
    put-back's own note already said so."""
    return Note('signin-back', 'Claude Code is signed in again', 'Claude Code is signed in to %s.' % name,
                'since %s' % since, None)


PAGE_BACK = 'Switchboard had stopped. It is running again.'
PAGE_DOWN = 'Switchboard has stopped and could not be started. Run: switchboard doctor'


def page_back(since):
    """The menu bar started the page by itself and it answers again (watch.PageKeeper). `since`: that start."""
    return Note('page-back', 'Switchboard', PAGE_BACK, 'since %s' % since, None)


def page_down(since):
    """The page stopped and the menu bar could not start it (watch.PageKeeper). Once for each spell."""
    return Note('page-down', 'Switchboard', PAGE_DOWN, 'since %s' % since, None)


def kept(name, n):
    """A login Claude Code was signed in to was kept by itself (keep.look). `name` is the account's account_label, as
    in every other note: they show on the lock screen."""
    return Note('kept:%s' % name, 'Switchboard', 'Kept %s · %d account%s' % (name, n, '' if n == 1 else 's'),
                None, None)


def from_hook(event, started=None, now=None, long_turn=LONG_TURN):
    """A Stop or Notification hook input -> "done" or "needs you". Never shows conversation text.

    started: when the turn began (notify_hooks notes it at UserPromptSubmit), or None when that isn't known. A
    Stop under long_turn seconds after it is quiet; with no start known, it is a "done" (one note too many is
    better than a long run's end never said)."""
    if not isinstance(event, dict) or event.get('agent_id') is not None:
        return None                         # a helper, an in-process teammate or a workflow agent: never
    name, session = event.get('hook_event_name'), str(event.get('session_id') or '')
    folder = os.path.basename(str(event.get('cwd') or '').rstrip('/'))
    where = ('In %s.' % folder) if folder else ''
    if name == 'Stop':
        if event.get('stop_hook_active') or event.get('background_tasks'):
            return None                     # continuing, or background work still in flight: not done yet
        began = _finite(started)
        now = time.time() if now is None else now
        if began is not None and now - began < long_turn:
            return None                     # a short turn: you saw it end
        return Note('done:' + session, 'Claude Code is done', where or 'Your turn.', event.get('prompt_id'), 5)
    if name == 'Notification':
        kind = event.get('notification_type')
        if kind in NEEDS_YOU:
            text = event.get('message').strip() if isinstance(event.get('message'), str) else ''
            text = OUTSIDE.get(kind, text)
            if text and where and text[-1] not in '.!?…':
                text += '.'                 # "... to use Bash. In my-app.", not "... to use Bash In my-app."
            return Note('needs:%s:%s' % (session, kind), 'Claude Code needs you',
                        ' '.join(x for x in (text, where) if x) or 'It is waiting for you.', None, 60)
    return None


def _line(line, window):
    """The used percent that counts as near: one number for every window, or {'five_hour': n, 'seven_day': n}."""
    return line.get(window, LINE) if isinstance(line, dict) else line


def from_states(old, new, line=LINE, now=None, name=None):
    """Every note for the change between two Switchboard states ({'active', 'accounts': {email:
    {'usage': {'session', 'weekly'}}}}). Each note is about one change. `line` is one number for
    every window or a number per window ({'five_hour': n, 'seven_day': n})."""
    now = time.time() if now is None else now
    name = name or account_label
    notes = []
    a0, a1 = old.get('active'), new.get('active')
    if a1 and a0 and a1 != a0:
        why = None
        for window, key in (('seven_day', 'weekly'), ('five_hour', 'session')):
            used, _ = window_of((((old.get('accounts') or {}).get(a0) or {}).get('usage') or {}).get(key))
            if used is not None and used >= _line(line, window):
                why = '%s had used %d%% of its %s limit.' % (name(old, a0), int(used), WINDOW_NAMES[window])
                break
        was, now_on = name(old, a0), name(new, a1)
        notes.append(switched(was if was != now_on else None, now_on, why))   # two accounts can share a label
    for email, acct in (new.get('accounts') or {}).items():
        before = ((old.get('accounts') or {}).get(email) or {}).get('usage') or {}
        after = (acct or {}).get('usage') or {}
        for window, key in (('five_hour', 'session'), ('seven_day', 'weekly')):
            notes.append(nearing(name(new, email), window, after.get(key), _line(line, window), now))
            notes.append(refilled(name(new, email), window, before.get(key), after.get(key), _line(line, window), now))
    return [n for n in notes if n is not None]


# ---------- remembering what was shown ----------

def state_path():
    """$SWITCHBOARD_NOTIFY_STATE, else ~/.switchboard/notify.json."""
    return os.environ.get('SWITCHBOARD_NOTIFY_STATE') or os.path.join(data_dir(), 'notify.json')


def _shown_before(seen, note, now):
    if not seen or not isinstance(seen, dict):          # nothing, or a memory file someone broke: not shown
        return False
    if note.instance is not None:
        before = seen.get('instance')
        if isinstance(note.instance, (int, float)) and isinstance(before, (int, float)):
            return abs(note.instance - before) < SAME_WINDOW
        return note.instance == before
    # a note remembered as shown in the future (the clock went back) does not silence it
    return note.every is not None and -AHEAD <= now - (_num(seen.get('at')) or 0) < note.every


# At most RATE notes in any RATE_WINDOW seconds (a flap or a burst of finished chats is not a stream of
# notifications). The rest are held, each remembered as told, and said together by flush_held() as one note ("4 more
# things happened") once there is room. Wall-clock times, because the memory is shared by the page, the hooks Claude
# Code runs and a terminal; a time dated past the clock (it was set back) counts for nothing.
RATE = 3
RATE_WINDOW = 5 * 60
_HERE = {}                  # key -> instance, for a memory file that can't be written (a full disk): no double here


def _load(path):
    try:
        with open(path) as f:
            store = json.load(f)
    except (OSError, ValueError):
        store = {}
    store = store if isinstance(store, dict) else {}
    seen = store.get('seen') if isinstance(store.get('seen'), dict) else {}
    recent = [t for t in store.get('recent') or [] if isinstance(t, (int, float)) and not isinstance(t, bool)] \
        if isinstance(store.get('recent'), list) else []
    held = store.get('held') if isinstance(store.get('held'), dict) else {}
    n = held.get('n') if isinstance(held.get('n'), int) and not isinstance(held.get('n'), bool) else 0
    titles = [t for t in held.get('titles') or [] if isinstance(t, str)] if isinstance(held.get('titles'), list) else []
    return {'v': 1, 'seen': seen, 'recent': recent, 'held': {'n': max(0, n), 'titles': titles[-3:]}}


def _save(path, store, now):
    store['seen'] = {k: v for k, v in store['seen'].items()
                     if isinstance(v, dict) and -AHEAD <= now - (_num(v.get('at')) or 0) < KEEP}
    store['recent'] = _recent(store['recent'], now)
    folder = os.path.dirname(path) or '.'
    tmp = os.path.join(folder, '.%s.%d.%d.tmp' % (os.path.basename(path), os.getpid(), int(now * 1000) % 100000))
    try:
        with open(tmp, 'w') as f:
            json.dump(store, f)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _recent(times, now):
    return [t for t in times if -AHEAD <= now - t < RATE_WINDOW]


class _Held(object):
    """notify.json, locked for one decision (two senders can't both decide a note is new)."""

    def __init__(self, path):
        self.path = path
        self.lock = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path) or '.', mode=0o700, exist_ok=True)
        self.lock = open(self.path + '.lock', 'a')
        fcntl.flock(self.lock, fcntl.LOCK_EX)
        return _load(self.path)

    def __exit__(self, *exc):
        self.lock.close()               # closing it lets the lock go
        return False


def send(note, run=_run, path=None, now=None):
    """Show a note unless it was shown already. True when it was shown now.

    It is remembered before osascript runs, so a run cut off (a timeout, the hook's own time limit) never shows it a
    second time; when osascript says no (or can't start), it is forgotten again and tried at the next chance. Past the
    rate limit it is held, not shown (flush_held says the held ones as one)."""
    return send_together([note], None, run=run, path=path, now=now)


def send_together(notes, together, run=_run, path=None, now=None):
    """Notes that fell due at the same moment, as one notification (so several never become a burst): those shown
    before are dropped; one left is shown as itself; more are shown as together(those left), one Note, and each of
    them is remembered as shown, so none comes again on its own. It counts once toward the rate limit, and past it is
    held as one. Remembered before osascript runs and forgotten again when osascript says no, as send() is. True when
    it was shown now."""
    notes = [n for n in notes if n is not None]
    if not notes:
        return False
    now = time.time() if now is None else now
    path = path or state_path()
    try:
        with _Held(path) as store:
            seen = store['seen']
            new = [n for n in notes if not _shown_before(seen.get(n.key), n, now)]
            if not new:
                return False
            one = new[0] if len(new) == 1 else together(new)
            if len(_recent(store['recent'], now)) >= RATE:
                store['held']['n'] += 1
                store['held']['titles'] = (store['held']['titles'] + [one.title])[-3:]
                for n in new:
                    seen[n.key] = {'at': now, 'instance': n.instance, 'held': True}
                _save(path, store, now)
                return False
            before = dict((n.key, seen.get(n.key)) for n in new)
            for n in new:
                seen[n.key] = {'at': now, 'instance': n.instance}
            store['recent'].append(now)
            _save(path, store, now)
    except OSError:
        # The memory can't be read or written (a full disk): each shown at most once by this process.
        new = [n for n in notes if not (n.key in _HERE and (n.instance is None or _HERE[n.key] == n.instance))]
        if not new:
            return False
        for n in new:
            _HERE[n.key] = n.instance
        one = new[0] if len(new) == 1 else together(new)
        return _show(one.title, one.body, run) == 0
    code = _show(one.title, one.body, run)
    if code == 0 or code == TIMEOUT:
        return code == 0
    try:                                # osascript said no: not shown, so it may be tried again
        with _Held(path) as store:
            for key, was in before.items():
                if was is None:
                    store['seen'].pop(key, None)
                else:
                    store['seen'][key] = was
            if now in store['recent']:
                store['recent'].remove(now)
            _save(path, store, now)
    except OSError:
        pass
    return False


def held_words(n, titles):
    """'4 more things happened. The last: Claude Code is done.' The titles are the notes' own (names and states)."""
    head = '%d more thing%s happened.' % (n, '' if n == 1 else 's')
    return head + (' The last: %s.' % titles[-1].rstrip('.') if titles else '')


def flush_held(run=_run, path=None, now=None):
    """The notes held back by the rate limit, said as one, once there is room. True when it was shown."""
    now = time.time() if now is None else now
    path = path or state_path()
    try:
        with _Held(path) as store:
            n, titles = store['held']['n'], store['held']['titles']
            if not n or len(_recent(store['recent'], now)) >= RATE:
                return False
            store['held'] = {'n': 0, 'titles': []}
            store['recent'].append(now)
            _save(path, store, now)
    except OSError:
        return False
    code = _show('Switchboard', held_words(n, titles), run)
    if code == 0 or code == TIMEOUT:
        return code == 0
    try:                                # not shown: kept for the next chance
        with _Held(path) as store:
            store['held'] = {'n': store['held']['n'] + n, 'titles': (titles + store['held']['titles'])[-3:]}
            if now in store['recent']:
                store['recent'].remove(now)
            _save(path, store, now)
    except OSError:
        pass
    return False
