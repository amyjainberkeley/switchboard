"""What keeps the menu bar item on screen: turning it off and on, where it is put the first time, and the one notice
when a full menu bar hides it.

Off is a choice made in the terminal, never a slip in a menu: the menu has no Quit. `switchboard menubar off` takes the
item's job out of LaunchAgents (so no login starts it), stops the item, and records the installer's own --no-menu-app
choice in <data>/app/choices, which `switchboard update` and Homebrew's upgrade both read back, so no update brings it
back; such an update also takes its files out, as the installer always does with that choice. `switchboard menubar on`
takes the choice away, writes the job as the installer writes it and starts it, or runs `switchboard update
--menu-app` when an update took the item's files out. Anything else that stops the item is undone: the job's KeepAlive
is always (launchd starts it again within 20 s), and the page starts the job when launchd did not (watch.MenuBarKeeper),
both only while it is not off.

Where it goes: macOS lets an app ask for a place in the menu bar only through the item's saved position in the app's
own preferences, "NSStatusItem Preferred Position <autosave name>", a distance in points from the right end of the
screen, which macOS reads when the item is made (not checked on every version of macOS). The first time, with nothing saved, the item asks to be at the
right end, toward Control Centre and the clock, where the notch of a MacBook's screen does not cover it. A saved place,
the person's or the one macOS saved, is never changed; the setting "menubar_position": false leaves the place to macOS.

Hidden: on a MacBook with a notch, a menu bar that is full puts the items that do not fit behind the notch, with no
sign. When the item's window is under the notch of a screen whose menu bar is shown, for more than HIDDEN_FOR seconds of
awake time, one notification says so and what to do, once per login, and never while notifications are turned off in
Switchboard. A window that is not shown or is on no screen is not counted: macOS does that too for an item the person
took off the menu bar, in a full-screen app, and for a menu bar that hides by itself, and none of those is a full one.

All of this is plain Python 3.9; the menu bar's AppKit part only reads and writes the preferences and looks at the
window, and passes what it finds here.
"""
import os
import plistlib
import signal
import subprocess
import time

from . import core, notify

CHOICE = '--no-menu-app'        # the installer's choice, kept in <data>/app/choices

OFF = 'The menu bar item is off. Turn it on with: switchboard menubar on'
ON = 'The menu bar item is on, and starts at every login.'
NO_JOB = 'Without the background job, start the menu bar item with: switchboard menubar'
NOT_STARTED = "Added the menu bar item's job, but macOS would not start it. switchboard doctor says why."
OTHER_JOB = ("The menu bar item's job in ~/Library/LaunchAgents runs another Switchboard folder's copy, so it was "
             "left as it is.")
NOT_YET = "The menu bar item's job is in place, but the item has not started yet. switchboard doctor says why."
STILL_RUNNING = ("The menu bar item stays off from the next login, but it is still running and could not be stopped. "
                 "switchboard doctor says why.")

CANT_DRAW_WAIT = 600.0      # seconds a menu bar that can't draw waits under its job before it exits (KeepAlive)
STOP_WAIT = 5.0             # seconds `off` and `on` wait for the item to stop or to start
LOOK_EVERY = 0.25

POSITION_KEY = 'NSStatusItem Preferred Position %s'
NEXT_TO_CLOCK = 0.0         # points from the right end of the screen: as far right as macOS lets a status item go

HIDDEN_FOR = 60.0           # awake seconds hidden before it is said
# An item behind the notch cannot be reached to drag it. Finder's own menus are short, so with Finder in front the menu
# bar has room and the item shows again.
HIDDEN = ("Switchboard's menu bar item is behind the notch because the menu bar is full. Click the desktop so Finder "
          "is in front; when the S shows, hold Command and drag it toward the clock. Your accounts are also in the "
          "terminal: switchboard")


def pause(seconds):
    time.sleep(seconds)


def under_launchd():
    """This process was started by launchd (its job): something starts it again when it exits."""
    from . import watch
    return watch.background()


# ---------- the off choice ----------

def choices_path():
    return core.data_path(os.path.join('app', 'choices'))


def is_off():
    """The item was turned off on purpose: `switchboard menubar off`, or the installer's --no-menu-app."""
    return CHOICE in core.read_text(choices_path()).split()


def set_off(off):
    """Record the choice in <data>/app/choices, the installer's own list, where updates read it back."""
    words = [w for w in core.read_text(choices_path()).split() if w != CHOICE] + ([CHOICE] if off else [])
    os.makedirs(os.path.dirname(choices_path()), mode=0o700, exist_ok=True)
    core.write_text(choices_path(), ' '.join(words) + '\n', mode=0o644)


def job_description():
    """The menu bar's LaunchAgent, as install.sh writes it: the launcher at every login, started again whenever it
    exits (KeepAlive), at most every 20 s, its output in menubar.log."""
    from . import menubar
    log = core.data_path(menubar.LOG)
    return {'Label': menubar.AGENT, 'ProgramArguments': [menubar.launcher()],
            'EnvironmentVariables': {'PATH': ':'.join(p.format(home=core.home()) for p in menubar.LAUNCHD_PATH)},
            'RunAtLoad': True, 'KeepAlive': True, 'ProcessType': 'Interactive', 'ThrottleInterval': 20,
            'StandardOutPath': log, 'StandardErrorPath': log}


def _write_job(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = '%s.%d.tmp' % (path, os.getpid())
    with open(tmp, 'wb') as f:
        plistlib.dump(job_description(), f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def _until(done):
    """Look every LOOK_EVERY seconds, for STOP_WAIT seconds at most, until done() is true. Returns done()."""
    for _ in range(int(STOP_WAIT / LOOK_EVERY)):
        if done():
            return True
        pause(LOOK_EVERY)
    return done()


def turn_off():
    """`switchboard menubar off`: the choice first (so the page's watch never starts it again meanwhile), then its job
    out of launchd and out of LaunchAgents, then the item stopped if it still runs (one started by hand). Returns
    (exit code, line)."""
    from . import menubar
    set_off(True)
    job = menubar.our_job()
    if job:
        menubar.launchctl('bootout', '%s/%s' % (menubar.domain(), menubar.AGENT))
        try:
            os.remove(job)
        except OSError:
            pass
    stopped = _until(lambda: menubar.running_pid() is None)
    if not stopped:
        pid = menubar.running_pid()
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
            stopped = _until(lambda: menubar.running_pid() is None)
    return (0, OFF) if stopped else (1, STILL_RUNNING)


def turn_on(update=None):
    """`switchboard menubar on`: the choice taken away, then the item's job written as the installer writes it and
    started. When an update took the item's files out, `switchboard update --menu-app` adds them back. Returns
    (exit code, line), or (exit code, None) after an update, which says what it did itself."""
    from . import menubar
    plist = menubar.agent_plist(menubar.AGENT)
    if os.path.exists(plist) and not menubar.our_job():     # a menu bar run for another folder: never rewrite its job
        return 1, OTHER_JOB
    set_off(False)
    hop = menubar.launcher()
    if not (os.path.isfile(hop) and os.access(hop, os.X_OK)):
        if update is None:
            return 1, menubar.NOT_HERE
        return update(['--menu-app']), None
    if not os.path.exists(menubar.agent_plist(menubar.PAGE_AGENT)):     # installed with --no-agent: no jobs at all
        return 0, NO_JOB
    _write_job(plist)
    target = '%s/%s' % (menubar.domain(), menubar.AGENT)
    if menubar.launchctl('print', target) == 0:
        if menubar.running_pid() is None:
            menubar.launchctl('kickstart', target)
    elif menubar.launchctl('bootstrap', menubar.domain(), plist) != 0 or menubar.launchctl('print', target) != 0:
        return 1, NOT_STARTED
    return (0, ON) if _until(lambda: menubar.running_pid() is not None) else (1, NOT_YET)


def command(word, update=None):
    """`switchboard menubar on|off`: one line, checked before it is said."""
    code, line = turn_off() if word == 'off' else turn_on(update)
    if line:
        print(line)
    return code


def doctor_line(short=None):
    """doctor's one line about the item: (good, words, what to do)."""
    from . import menubar
    if is_off():
        return None, 'Menu bar item: off, by your choice', 'Turn it on with: switchboard menubar on'
    pid = menubar.running_pid()
    if pid is not None:
        return True, 'Menu bar item: on%s' % (' (pid %d)' % pid if pid else ''), ''
    log = core.data_path(menubar.LOG)
    return None, 'Menu bar item: installed, not running', 'Start it with: switchboard menubar on. Its log: %s' % (
        short(log) if short else log)


# ---------- where it goes the first time ----------

def position_key(name):
    return POSITION_KEY % name


def place(get, put, name=None):
    """Ask macOS for the place toward Control Centre and the clock, the first time only: get(key) and put(key, value)
    read and write the app's own preferences (NSUserDefaults), before the item is made. Nothing when a place is
    saved already, when the setting "menubar_position" is false, or when the preferences can't be read or written
    (the item is made as before). Returns the position written, or None."""
    try:
        if core.settings().get('menubar_position') is False:
            return None
        if name is None:
            from . import menubar
            name = menubar.AGENT
        key = position_key(name)
        if get(key) is not None:
            return None
        put(key, NEXT_TO_CLOCK)
        return NEXT_TO_CLOCK
    except Exception:  # noqa: BLE001 - placing is a wish: the item is made wherever macOS puts it
        return None


# ---------- hidden by a full menu bar ----------

def _overlap(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def hidden(facts):
    """Is the item hidden by a full menu bar? facts (from the AppKit part): {'removed': the person took the item off
    the menu bar, 'visible': its window is shown, 'frame': (x, y, w, h) of its window, 'screens': [{'frame': (x, y, w,
    h), 'notch': (left x, right x) or None, 'menu_bar': the screen shows its menu bar}]}, in screen points. True: under
    the notch of a screen whose menu bar is shown. None (not known) when it was taken off on purpose, is not shown, is on
    no screen, or is on a screen whose menu bar is not shown: none of those says the menu bar is full."""
    if facts is None or facts.get('removed'):
        return None
    frame = facts.get('frame')
    if not facts.get('visible') or not frame:
        return None
    on = [s for s in facts.get('screens') or [] if _overlap(frame, s['frame'])]
    if not on or any(s.get('menu_bar') is False for s in on):
        return None
    x, w = frame[0], frame[2]
    return any(s.get('notch') and x < s['notch'][1] and x + w > s['notch'][0] for s in on)


def login_id():
    """This login: the Mac's boot time and the login's security session (SECURITYSESSIONID, which macOS gives each
    login's processes), so once per login holds across restarts of the menu bar."""
    boot = None
    try:
        out = subprocess.run(['/usr/sbin/sysctl', '-n', 'kern.boottime'], capture_output=True, text=True,
                             timeout=5).stdout
        boot = int(out.split('sec =', 1)[1].split(',', 1)[0])        # "{ sec = 1764200000, usec = 1234 } Thu ..."
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        try:
            boot = int((time.time() - time.clock_gettime(time.CLOCK_MONOTONIC)) // 60 * 60)
        except (AttributeError, OSError):
            boot = 0
    return '%d/%s' % (boot, os.environ.get('SECURITYSESSIONID') or '-')


def _notes_on():
    from . import notify_hooks
    return notify_hooks.notes_on()


class HiddenNotice(object):
    """The one notice when the item is hidden. look(hidden, mono) once per reading: hidden True, False or None (not
    known, which counts as neither); mono is the monotonic clock, which stops while the Mac sleeps. Past HIDDEN_FOR
    seconds hidden it sends the notice, once per login: this process remembers it, and notify.send remembers it by
    the login (a menu bar started again in the same login says nothing). Never while notifications are off; turned
    on while the item is still hidden, it is said then."""

    def __init__(self, send=None, notes_on=None, login=None):
        self.send = send or (lambda note: notify.send(note))
        self.notes_on = notes_on or _notes_on
        self.login = login or login_id
        self.since = None
        self.said = False

    def note(self):
        return notify.Note('menubar-hidden', 'Switchboard', HIDDEN, 'login %s' % self.login(), None)

    def look(self, hidden, mono):
        """True when the notice was sent now."""
        if not hidden:
            self.since = None
            return False
        if self.since is None:
            self.since = mono
        if self.said or mono - self.since <= HIDDEN_FOR or not self.notes_on():
            return False
        self.said = True
        self.send(self.note())
        return True
