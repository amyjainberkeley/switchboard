"""Signing in is all it takes: a login Claude Code signs in to is kept by itself, and with a second account
auto-switch turns on by itself, using what refills first.

Run /login in Claude Code as another account and Switchboard keeps that login, the way `switchboard add` and the
page's Add keep it (engine.add_current), and says so: one line on the page and a notification. look() runs in the
page's background tick, after the engine's rows are read. A login is kept only when:

- the same login was seen not held on two looks in a row, at least KEEP_GAP seconds apart (by then Claude Code has
  finished writing it). "Held": the engine keeps an account with the same email in the same organization;
- it was not let go: an account removed in Switchboard is remembered as its email and organization (the same email
  in another organization is another account), and only `switchboard add` or the page's Add keep it again (and
  forget that it was let go);
- the engine still does not hold it when it is kept: the look reads the engine's accounts again first, and a keep
  here and the page's Add (which `switchboard add` goes through while the page runs) take turns (ADDING), so a login
  the Add kept meanwhile is not kept, said and notified twice;
- after a failure (a locked Keychain, say), the same login is tried again only after RETRY seconds (RETRY_SLOW when
  the Keychain was only slow), and the failure is said once.

While the login Claude Code is on is held, each look also hands the engine one pass of login keeping
(api.keep_pass): Claude Code renews its login by itself, and the engine keeps the renewed one. Never while that login
is one the person removed: the engine would keep it again.

The loop looks again sooner while a new login waits for its second look (due, wait_for), and a change of the
account Claude Code is signed in to wakes it at once (login_changed), so a login is kept within seconds of /login.

Auto-switch (auto_by_itself): once the engine holds two or more accounts it can move Claude Code to (held, not
needing a fresh sign-in), while it is off and the person never turned it on or off in Switchboard (auto_set_by_you,
marked by the page's switch, the menu bar's and `switchboard auto on|off`), it is turned on the way the page's switch
turns it on, and said once. Not while another program switches Claude Code's login too (engine.foreign_switcher):
the page says that instead. The person's choice is marked before it is made (by_you), and it and turning on by
itself take turns (CHOICE, a lock every Switchboard process shares), so a look in between can never turn it back on.
The order is the engine's own default, what refills first, until the person picks another. Reading the accounts
waits for none of this: it starts with the first account kept, and goes on whether auto-switch is on or off
(engine.loop_wanted); auto-switch on adds switching.

Notifications (notify_by_itself): Switchboard's own notes are on until turned off, and at the first run (setup, the
terminal's first run, the page's first run) Claude Code's "done" and "needs you" hooks go in too, so the
Notifications switch shows what is true. Once: only when the person never turned Notifications on or off
(notify_set_by_you, marked by the page's switch and `switchboard notify on|off`), it never went on by itself before,
and Claude Code's settings file can be read with nothing in it that stops hooks (disableAllHooks). Anything that
stops it changes nothing and says nothing.

The session hooks (session_hooks_by_itself): at the same first runs, after the status line, the hooks that tell an open
Claude Code session what a switch means for it go in too, by the same rules (once; never after the person set them;
never over the person's own hooks).

The status line (statusline_by_itself): at the same first runs, after notifications, Switchboard's status line goes
into Claude Code's settings too, so the account in use has Claude Code's own numbers, as fresh as its last answer
(statusline.py saves them; server.apply_live_numbers uses them). The same rules: once; never after the person turned
it on or off (statusline_set_by_you, marked by the page's switch and `switchboard statusline on|off`; a switch from
before this was kept left its install record); only when Claude Code's settings file can be read and Claude Code
would run it. A status line of the person's own is never replaced: it stays, and one line says so.

Links: the first run at a terminal says what the link app does and asks macOS to make it the default browser only after
the person presses Enter (firstrun.py), and marks links_by_itself when it asked. links_by_itself() below asks without
that question; the first run no longer calls it. The page's banner offers it until it is the default.
"""
import os
import threading
import time

from . import core, engine, notify
from .accounts import api

KEEP_GAP = 5.0          # seconds between the two looks that see the same new login
RETRY = 300.0           # after a failure, seconds before that login is tried again
RETRY_SLOW = 60.0       # ... when the Keychain was only slow to answer
SOON = 0.5              # the least wait before a look that is due
AUTO_ON = notify.AUTO_ON
NOT_SWITCHABLE = ('needs_login', 'conflict')    # the engine's account states no switch can move Claude Code to

SEEN = {'who': None, 'at': 0.0}         # the login seen not held on the last look, and when it was first seen
FAILED = {}                             # who -> (when keeping it last failed, seconds before trying again)
SAID = set()                            # whose failure was said already
LOOK = {'started': False, 'stat': None, 'who': None}    # what login_changed last saw of Claude Code's file
FIRST = {'none': False}                 # the page's last look saw no account held: its first run is showing
LINKED = set()                          # linked settings files already named in the log
ADDING = threading.Lock()               # a keep in the look and the page's Add, one at a time
CHOICE = '.auto-choice.lock'            # the person's choices and Switchboard's own, one at a time
STATUSLINE_ON = 'Status line in Claude Code: on'
STATUSLINE_THEIRS = 'You already have a status line in Claude Code, so Switchboard left it.'


def reset():
    """Forget what the looks saw (a fresh page; tests)."""
    SEEN.update(who=None, at=0.0)
    FAILED.clear()
    SAID.clear()
    LOOK.update(started=False, stat=None, who=None)
    FIRST.update(none=False)
    LINKED.clear()


# ---------- who is who ----------

def live():
    """(email, organization) of the account Claude Code's config names, or None."""
    return engine.identity()


def who_of(row):
    return ((row.get('email') or '').strip().lower(), row.get('org_id') or '')


def account_of(acct):
    """(email, organization) of an engine Account, or None when it names no email."""
    email = (getattr(acct, 'email', None) or '').strip().lower()
    return (email, getattr(acct, 'org_id', None) or '') if email else None


def held(who, rows):
    """Does the engine keep this login? The same email in the same organization (one email can be in two
    organizations, and each is an account of its own)."""
    return any(not r.get('unmanaged') and who_of(r) == who for r in rows or [])


def accounts(rows):
    """How many accounts the engine keeps."""
    return len([r for r in rows or [] if not r.get('unmanaged')])


def switchable(rows):
    """How many accounts automatic switching can move Claude Code to: kept, not needing a fresh sign-in."""
    return len([r for r in rows or [] if not r.get('unmanaged') and r.get('status') not in NOT_SWITCHABLE])


# ---------- let go: removed in Switchboard, so never kept by itself ----------
#
# settings.json's let_go: [email, organization] for each account removed here. An email alone is an entry from before
# accounts were told apart by organization: it stands for that email in every organization.

def _account(who):
    """[email, organization] of a login given as (email, organization)."""
    return [(who[0] or '').strip().lower(), who[1] or '']


def let_go_list(v=None):
    """The let_go entries in settings.json (or in `v`), leaving out anything that is not one."""
    v = core.settings().get('let_go') if v is None else v
    return [x for x in v if isinstance(x, str) or (isinstance(x, list) and len(x) == 2)] if isinstance(v, list) else []


def _covers(entry, account):
    return entry == account[0] if isinstance(entry, str) else [entry[0], entry[1] or ''] == account


def is_let_go(who):
    """Was this login, (email, organization), removed in Switchboard?"""
    account = _account(who)
    return any(_covers(x, account) for x in let_go_list())


def let_go(*whos):
    """Accounts removed in Switchboard, each (email, organization): never kept by itself again, until `switchboard
    add` or the page's Add keeps that one."""
    more = []
    for w in whos:
        if w and isinstance(w[0], str) and w[0].strip() and _account(w) not in more:
            more.append(_account(w))

    def change(now):
        return sorted([x for x in let_go_list(now) if x not in more] + more,
                      key=lambda x: (x, '') if isinstance(x, str) else (str(x[0]), str(x[1] or '')))
    if more:
        core.update_setting('let_go', change)


def forget(who):
    """`switchboard add` or the page's Add kept this login, (email, organization): it may be kept by itself again.
    An entry for its email alone (from before organizations were told apart) goes too."""
    account = _account(who)

    def change(now):
        if not isinstance(now, list) or not any(_covers(x, account) for x in now):
            return now                          # nothing to forget: nothing is written
        return [x for x in now if not _covers(x, account)]
    core.update_setting('let_go', change)


# ---------- the look, in the page's background tick ----------

def _stop_watching():
    SEEN.update(who=None, at=0.0)


def look(rows, now=None, run=None):
    """After the engine's rows are read (rows None when they were not): keep Claude Code's login when the engine does
    not hold it, and hand the engine a pass of login keeping while it does. `run` shows a notification (None: none).
    Returns the email kept, or None."""
    now = time.time() if now is None else now
    if core.DEMO or rows is None:
        _stop_watching()
        return None
    who = live()
    if who is not None and is_let_go(who):
        _stop_watching()                        # removed here: never kept again by itself, not even renewed
        return None
    if who is None or held(who, rows):
        _stop_watching()
        keep_pass()
        return None
    if SEEN['who'] != who:
        SEEN.update(who=who, at=now)          # first seen: Claude Code may still be writing it
        return None
    when, wait = FAILED.get(who, (-RETRY, RETRY))
    if now - SEEN['at'] < KEEP_GAP or now - when < wait:
        return None
    with ADDING:
        if who in engine.saved_identities():    # the page's Add (or a terminal's) kept it since the rows were read
            _stop_watching()
            return None
        got = None
        try:
            got = engine.add_current()
        except Exception as e:  # noqa: BLE001 - any failure waits, so the loop never looks again at once
            failed = e
        else:
            failed = None
    if failed is not None:
        slow = getattr(failed, 'detail', None) in ('keychain_slow', 'claude_busy')
        FAILED[who] = (now, RETRY_SLOW if slow else RETRY)
        core.log('could not keep %s: %r' % (who[0], failed))
        if who not in SAID:
            SAID.add(who)
            why = _first(str(failed)) if isinstance(failed, core.SwitchError) else 'Switchboard could not ask the engine'
            with core.locked() as st:
                core.event(st, 'Could not keep %s: %s. Trying again in %s.' % (
                    who[0], why, 'a minute' if slow else '5 minutes'), 'warn')
        return None
    kept = account_of(got) or who
    _stop_watching()
    if kept != who:
        # Claude Code's settings name `who`, but its login is another account's: the engine kept (or only brought up
        # to date) that one. `who` stays not held, so it is looked at again only after a while, and never said as kept.
        FAILED[who] = (now, RETRY)
        if held(kept, rows):
            return None
        who = kept
    else:
        FAILED.pop(who, None)
        SAID.discard(who)
    n = len(engine.saved_identities()) or accounts(rows) + 1
    with core.locked() as st:
        core.event(st, kept_line(who[0], n), 'switch')
    if run is not None:
        try:
            notify.send(notify.kept(notify.account_label(st, who[0]), n), run=run, now=now)
        except Exception as e:  # noqa: BLE001 - the line on the page is said either way
            core.log('notification failed: %r' % e)
    return who[0]


def keep_pass():
    """One pass of the engine's login keeping (Claude Code renews its login by itself; the engine keeps the renewed
    one). What it says goes to the log. Never raises."""
    try:
        note = api.keep_pass()
    except NotImplementedError:
        return None
    except Exception as e:  # noqa: BLE001 - the page's tick goes on either way
        core.log('keeping logins: %r' % e)
        return None
    if note:
        core.log('keeping logins: %s' % note)
    return note


def kept_line(email, n):
    return 'Kept %s · %d account%s' % (email, n, '' if n == 1 else 's')


def _first(text):
    """The first sentence of a refusal, without its full stop: "macOS's Keychain did not let the login be saved"."""
    text = (text or '').strip()
    i = text.find('. ')
    return (text[:i] if i > 0 else text).rstrip('.')


# ---------- auto-switch on by itself ----------

def set_by_you():
    """The person turned auto-switch on or off in Switchboard (the page, the menu bar, `switchboard auto on|off`):
    from now on it never turns on by itself."""
    if core.settings().get('auto_set_by_you') is not True:
        core.set_setting('auto_set_by_you', True)


def by_you(change):
    """The person turns auto-switch on or off: marked as theirs first (set_by_you), then `change()` makes it, while
    auto_by_itself waits. So a look can never turn it back on in between, and a change that fails part-way is still
    theirs. Returns what `change` returns."""
    with core.file_lock(CHOICE):
        set_by_you()
        return change()


def auto_by_itself(count, now=None, run=None, turn_on=None):
    """Turn auto-switch on when `count` accounts can be switched to (two or more), it is off, the person never turned
    it on or off in Switchboard, and no other program switches Claude Code's login: `turn_on` (the page's switch:
    engine.set_auto), while the person's own choice (by_you) waits. Said each time on the page's list, and once as a
    notification (`run`; None: none). Returns True when it was turned on."""
    if core.DEMO or count < 2 or engine.foreign_switcher():
        return False
    with core.file_lock(CHOICE):
        if engine.auto_settings().get('enabled') or core.settings().get('auto_set_by_you') is True:
            return False
        (turn_on or (lambda: engine.set_auto(enabled=True)))()
    with core.locked() as st:
        core.event(st, AUTO_ON, 'info', reason='%d accounts to switch between' % count)
    if not core.settings().get('auto_on_said'):
        core.set_setting('auto_on_said', time.time())
        if run is not None:
            try:
                notify.send(notify.auto_on(), run=run, now=now)
            except Exception as e:  # noqa: BLE001 - the line on the page is said either way
                core.log('notification failed: %r' % e)
    return True


# ---------- notifications on by themselves, at the first run ----------

def notify_set_by_you():
    """The person turned Notifications on or off (the page's switch, `switchboard notify on|off`): from now on they
    never go on by themselves."""
    if core.settings().get('notify_set_by_you') is not True:
        core.set_setting('notify_set_by_you', True)


def notify_by_you(change):
    """The person turns Notifications on or off: marked as theirs first (notify_set_by_you), then `change()` makes it,
    while notify_by_itself waits. So a first run can never put the hooks back in between, and a change that fails
    part-way is still theirs. Returns what `change` returns."""
    with core.file_lock(CHOICE):
        notify_set_by_you()
        return change()


def notify_by_itself(now=None):
    """At the end of a first run: Claude Code's "done" and "needs you" hooks go in (notify_hooks.turn), once. Not when
    the person ever set Notifications (a `notify` setting from before this was kept counts as theirs), not when they
    went on by themselves before, not without Claude Code's settings folder, not when its settings file can't be read
    or a key there stops hooks (disableAllHooks, or managed allowManagedHooksOnly), and not when they are in already.
    A refusal or a failure is only logged. The person's own switch (notify_by_you) waits while this looks and turns,
    and this waits for it. Returns True when the hooks went in."""
    if core.DEMO:
        return False
    from . import notify_hooks
    try:
        with core.file_lock(CHOICE):
            s = core.settings()                 # read inside the turn: a switch of the person's just before counts
            if s.get('notify_set_by_you') is True or 'notify' in s or s.get('notify_by_itself'):
                return False
            if not os.path.isdir(os.path.dirname(notify_hooks.settings_path())):
                return False                    # Claude Code has not made its folder: nothing is created for it
            if _linked(notify_hooks.settings_path()):
                return False
            now_in = notify_hooks.status()
            if now_in['in'] is None or now_in['off_by'] or (now_in['in'] and now_in['works']):
                return False
            notify_hooks.turn(True)
            try:
                core.set_setting('notify_by_itself', time.time() if now is None else now)
            except Exception as e:  # noqa: BLE001 - they are in; only the note of it failed, and the first run goes on
                core.log('notifications went on, not noted: %r' % e)
    except Exception as e:  # noqa: BLE001 - the first run goes on either way, and says nothing about it
        core.log('notifications were not turned on: %r' % e)
        return False
    try:
        with core.locked() as st:
            core.event(st, 'Notifications: on', 'info', h='setting', by='auto', reason='first run')
    except Exception as e:  # noqa: BLE001 - they are in; only the note of it failed, and the first run goes on
        core.log('notifications went on, not noted: %r' % e)
    return True


def _linked(path):
    """Claude Code's settings file is a symbolic link (kept in a dotfiles folder, say): nothing goes into it by itself,
    since the paths Switchboard writes are this Mac's and the file may be shared. Turning a piece on by hand still
    works. Said in the log once."""
    if not os.path.islink(path):
        return False
    if path not in LINKED:
        LINKED.add(path)
        core.log('%s is a link, so the status line and hooks did not go on by themselves; turning them on still works'
                 % path)
    return True


def first_run_tick(rows, kept=None):
    """The page's first run ends when a look finds an account held after one that found none (the page's Add or its
    sign-in, a /login kept, or `switchboard setup` while the page ran): notifications go on by
    themselves then (notify_by_itself), the status line (statusline_by_itself), the session hooks and Switchboard's
    skill (skillfile.by_itself). `kept`: a login keep.look kept on
    this tick. Returns True when notifications went on."""
    if accounts(rows) + (1 if kept else 0) == 0:
        FIRST['none'] = True
        return False
    if not FIRST['none']:
        return False
    FIRST['none'] = False
    notified = notify_by_itself()
    statusline_by_itself()
    session_hooks_by_itself()
    from . import skillfile
    skillfile.by_itself()                       # Switchboard's skill for coding agents, by the same rules
    return notified


# ---------- links open as their owner, asked for without a question first ----------
#
# Opening links as their owner is what Switchboard is for, so this asks macOS to make Switchboard Links the default
# browser, as the page's banner does: macOS asks, and only the person's yes changes it. (The first run in the terminal
# asks through firstrun.py instead, after saying what the link app does.) Once: never after the person turned links on or off (links_wanted: the banner, `switchboard links on|off`), never
# after it was asked for by itself before (links_by_itself), not without the link app, not when it is the default
# already. The page's banner offers it again until it is the default, unless it was turned off on purpose.

def links_by_itself(now=None):
    """At the end of a first run at a terminal: asks macOS once to make the link app the default browser
    (linkapp.turn). A failure is only logged. Returns True when macOS was asked."""
    if core.DEMO:
        return False
    from . import linkapp, server
    try:
        with core.file_lock(CHOICE):
            s = core.settings()
            if server.LINKS_WANTED in s or s.get('links_by_itself'):
                return False
            st = linkapp.status(fresh=True)
            if not st.get('installed') or st.get('on'):
                return False
            linkapp.turn(True)
            core.set_setting('links_by_itself', time.time() if now is None else now)
    except Exception as e:  # noqa: BLE001 - the first run goes on either way
        core.log('links were not asked for: %r' % e)
        return False
    return True


# ---------- the session hooks on by themselves, at the first run ----------
#
# session_hooks.py: what tells an open Claude Code session, in plain words, what a switch means for it. The same rules
# as the notification hooks: once; never after the person turned them on or off
# (session_hooks_set_by_you, marked by `switchboard hooks on|off`, or any "session_hooks" setting); only when Claude
# Code's settings file can be read and nothing there stops hooks. Never over the person's own hooks: Switchboard's go
# beside theirs, which stay byte for byte, and taking Switchboard's out puts the file back exactly, in whatever order
# the status line and the notification hooks come and go (session_hooks' plan and apply).

SESSION_HOOKS_ON = 'Session hooks in Claude Code: on'


def session_hooks_set_by_you():
    """The person turned the session hooks on or off (`switchboard hooks on|off`): from now on they never go on by
    themselves."""
    if core.settings().get('session_hooks_set_by_you') is not True:
        core.set_setting('session_hooks_set_by_you', True)


def session_hooks_by_you(change):
    """The person turns the session hooks on or off: marked as theirs first, then `change()` makes it, while
    session_hooks_by_itself waits (CHOICE). Returns what `change` returns."""
    with core.file_lock(CHOICE):
        session_hooks_set_by_you()
        return change()


def session_hooks_by_itself(now=None):
    """At the end of a first run, after notifications and the status line: Switchboard's session hooks go into Claude
    Code's settings (session_hooks.turn), once. Not when the person ever set them (session_hooks_set_by_you, or a
    "session_hooks" setting: false keeps them quiet), not when they went on by themselves before, not without Claude
    Code's settings folder, not when its settings file can't be read or a key there stops hooks (disableAllHooks, or
    managed allowManagedHooksOnly), and not when they are in already. A refusal or a failure is only logged. Returns
    True when the hooks went in."""
    if core.DEMO:
        return False
    from . import session_hooks
    try:
        with core.file_lock(CHOICE):
            s = core.settings()                 # read inside the turn: a choice of the person's just before counts
            if s.get('session_hooks_set_by_you') is True or 'session_hooks' in s or s.get('session_hooks_by_itself'):
                return False
            if not os.path.isdir(os.path.dirname(session_hooks.settings_path())):
                return False                    # Claude Code has not made its folder: nothing is created for it
            if _linked(session_hooks.settings_path()):
                return False
            now_in = session_hooks.status()
            if now_in['in'] is None or now_in['off_by'] or (now_in['in'] and now_in['works']):
                return False
            session_hooks.turn(True)
            try:
                core.set_setting('session_hooks_by_itself', time.time() if now is None else now)
            except Exception as e:  # noqa: BLE001 - they are in; only the note of it failed
                core.log('the session hooks went on, not noted: %r' % e)
    except Exception as e:  # noqa: BLE001 - the first run goes on either way, and says nothing about it
        core.log('the session hooks were not turned on: %r' % e)
        return False
    try:
        with core.locked() as st:
            core.event(st, SESSION_HOOKS_ON, 'info', h='setting', by='auto', reason='first run')
    except Exception as e:  # noqa: BLE001
        core.log('the session hooks went on, not noted: %r' % e)
    return True


# ---------- the status line on by itself, at the first run ----------

def statusline_set_by_you():
    """The person turned the status line on or off (the page's switch, `switchboard statusline on|off`): from now on it
    never goes on by itself."""
    if core.settings().get('statusline_set_by_you') is not True:
        core.set_setting('statusline_set_by_you', True)


def statusline_by_you(change):
    """The person turns the status line on or off: marked as theirs first (statusline_set_by_you), then `change()`
    makes it, while statusline_by_itself waits (CHOICE). So a first run can never put it back in between, and a change
    that fails part-way is still theirs. Returns what `change` returns."""
    with core.file_lock(CHOICE):
        statusline_set_by_you()
        return change()


def statusline_stopped():
    """Claude Code would not run a status line from the user settings file: disableAllHooks there, the managed
    settings' disableAllHooks or allowManagedHooksOnly, or a status line in the managed settings, which wins over
    yours. (Claude Code's settings reference: "When allowManagedHooksOnly is on, or disableAllHooks is set outside
    managed settings, only the managed settings value runs.")"""
    from . import notify_hooks, statusline
    f = statusline._settings_file(statusline.settings_path())
    user = None if f.get('error') else f['obj']
    if isinstance(user, dict) and user.get('disableAllHooks') is True:
        return True
    return bool(notify_hooks._off_by(user)) or any('statusLine' in d for d in notify_hooks._managed())


def statusline_by_itself(now=None):
    """At the end of a first run, after notifications: Switchboard's status line goes into Claude Code's settings
    (server.statusline_turn), once. Not when the person ever turned it on or off (statusline_set_by_you, or the install
    record a switch from before this was kept left), not when it went on by itself before, not without Claude Code's
    settings folder, not when its settings file can't be read, and not when Claude Code would not run it
    (statusline_stopped). A status line of the person's own stays as it is: one line on the page's list says so,
    once. A refusal or a failure is only logged. The person's own switch (statusline_by_you) waits while this looks
    and turns, and this waits for it. Returns 'on' (it went on), 'theirs' (theirs is kept) or None."""
    if core.DEMO:
        return None
    from . import server, statusline
    said = None
    try:
        with core.file_lock(CHOICE):
            s = core.settings()                 # read inside the turn: a switch of the person's just before counts
            if s.get('statusline_set_by_you') is True or s.get('statusline_by_itself') or (
                    'statusline_by_itself' not in s and os.path.exists(statusline.record_path())):
                return None
            if not os.path.isdir(os.path.dirname(statusline.settings_path())):
                return None                     # Claude Code has not made its folder: nothing is created for it
            if _linked(statusline.settings_path()):
                return None
            plan = server.statusline_plan(True, replace=False)
            if not plan.get('ok') or not (plan.get('change') or plan.get('kept_theirs')) or statusline_stopped():
                return None                     # can't be read, on already, or Claude Code would not run it
            if plan.get('kept_theirs'):
                if s.get('statusline_theirs_said'):
                    return 'theirs'             # said on the page's list once; the terminal says it each first run
                said, key = 'theirs', 'statusline_theirs_said'
            else:
                server.statusline_turn(True, plan=plan)
                said, key = 'on', 'statusline_by_itself'
            try:
                core.set_setting(key, time.time() if now is None else now)
            except Exception as e:  # noqa: BLE001 - only the note of it failed, and the first run goes on
                core.log('the status line was not noted: %r' % e)
    except Exception as e:  # noqa: BLE001 - the first run goes on either way, and says nothing about it
        core.log('the status line was not turned on: %r' % e)
        return None
    try:
        with core.locked() as st:
            core.event(st, STATUSLINE_ON if said == 'on' else STATUSLINE_THEIRS, 'info', h='setting', by='auto',
                       reason='first run')
    except Exception as e:  # noqa: BLE001 - only the note of it failed, and the first run goes on
        core.log('the status line was not noted: %r' % e)
    return said


# ---------- looking again soon ----------

def _waiting(who, now):
    when, wait = FAILED.get(who, (-RETRY, RETRY))
    return now - when < wait


def due(now=None):
    """A new login waits for its second look, and that look is due now."""
    now = time.time() if now is None else now
    who = SEEN['who']
    return bool(who) and now - SEEN['at'] >= KEEP_GAP and not _waiting(who, now)


def wait_for(default, now=None):
    """How long the loop may wait before its next pass: no longer than the second look of a new login needs."""
    now = time.time() if now is None else now
    who = SEEN['who']
    if not who or _waiting(who, now):
        return default
    return min(default, max(SOON, SEEN['at'] + KEEP_GAP + SOON - now))


def login_changed():
    """Has Claude Code been signed in to another account since the last call? Claude Code's own file is read only
    when it changed. Signing out is not a change worth a look (there is nothing to keep)."""
    path = core.claude_json()
    try:
        st = os.stat(path)
        stat = (st.st_mtime_ns, st.st_size)
    except OSError:
        stat = None
    if not LOOK['started']:                     # the first call only remembers
        LOOK.update(started=True, stat=stat, who=live() if stat is not None else None)
        return False
    if stat == LOOK['stat']:
        return False
    LOOK['stat'] = stat
    who = live() if stat is not None else None
    changed = who is not None and who != LOOK['who']
    LOOK['who'] = who
    return changed
