"""The menu bar's wrapper app, so the menu bar's process has a bundle identifier.

`switchboard menubar` runs on the menu bar's Python: to macOS a bare interpreter with no bundle identifier. Menu bar
managers (Ice, Bartender) know every status item by its owner's identifier and skip an item whose owner has none:
it never shows in their lists and cannot be hidden, ordered or kept in view. So the menu bar runs from inside a
small app, made on demand, and execs into it before AppKit loads:

    <data>/menubar/Switchboard.app/
      Contents/
        Info.plist      CFBundleIdentifier io.github.amyjainberkeley.switchboard.menubar, CFBundleName Switchboard,
                        LSUIElement (no Dock icon)
        pyvenv.cfg      home = <the folder of the real interpreter>
        lib/
          python3.X -> <the menu bar's environment>/lib/python3.X    its packages, PyObjC among them
          libpython3.X.dylib -> <the base install>/lib/...           for an interpreter that loads it from ../lib
        MacOS/python    a byte copy of the real interpreter
        Resources/      sitecustomize.py (below), and Switchboard.icns: Switchboard's mark, the link app's icon

macOS knows a process by its executable's path, so the interpreter must really be under Contents/MacOS (a link
resolves to the original and keeps the missing identity); the copy is made once, about 20 MB. Python finds
pyvenv.cfg one folder up and takes Contents for a virtual environment: `home` points it at the standard library,
the lib links at the installed packages. lib is a real folder, not a link to the environment's: an interpreter that
is a small stub linked against @executable_path/../lib/libpython3.X.dylib finds that library in it too.

A framework build of Python (python.org's, Homebrew's) is left as it is: its bin/python3.X is a launcher that starts
Python.app from a path next to itself, so a copy finds nothing, and a copy of the real one inside Python.app is
signed as that app and is stopped by macOS on its first line (tried on python.org's 3.14). Such a Python is known to
draw no status item on macOS 26 at all; doctor says when the menu bar runs on one.

Every start makes it again, cheaply: only what changed is written, so a new environment or a new Python never leaves
a stale app behind, and launchd keeps the same pid through the exec.

Where it lives: the installer puts it in /Applications when that can be written to without a password, else in
~/Applications (place()), so Spotlight and Launchpad find it, and menu bar managers can keep its item in view (on
macOS 26 the menu bar reads an app outside /Applications as having no identity, and a manager that hides anything may
hide it too). A Switchboard.app there that is not this install's is never touched: the other folder is used, and
when neither will do, the data folder. `switchboard menubar --applications` does the same. Each start makes the app
again where this install's copy is (home()). The app names the data folder it was made for (Info.plist,
SwitchboardData), so a try with another SWITCHBOARD_HOME, or another account on this Mac, never rebuilds it.
SWITCHBOARD_MENUBAR_NO_BUNDLE=1 runs the menu bar without the app.

Opened from Finder, Spotlight or Launchpad, macOS starts the app's program, this copy of Python, with nothing to run
(no arguments, nothing on stdin), and with Info.plist's LSEnvironment, which macOS sets only for an app it opens:
PYTHONPATH points that Python at Contents/Resources/sitecustomize.py, which Python imports as it starts. Those few
lines hand the start to `switchboard menubar --opened` on the program and data folder the app was made for: the page
when the menu bar runs, else the menu bar. Started any other way (the menu bar's own start passes -I, which ignores
PYTHONPATH) they do nothing. Opened again while the menu bar runs, macOS hands the open to the running one, which
opens the page (menubar.py).

With the app in place, the page's background job runs on the app's Python too (install.sh writes bin/switchboard-page),
so what macOS asks on the page's behalf names Switchboard.
"""
import collections
import os
import plistlib
import shutil
import stat
import subprocess
import sys

from . import core

BUNDLE_ID = 'io.github.amyjainberkeley.switchboard.menubar'
BUNDLE_NAME = 'Switchboard'
DIRNAME = 'Switchboard.app'
APPLICATIONS = '/Applications'          # read at call time, so tests can point it elsewhere
OPT_OUT = 'SWITCHBOARD_MENUBAR_NO_BUNDLE'
DATA_KEY = 'SwitchboardData'            # Info.plist: the data folder the app was made for
OPENED = 'SWITCHBOARD_APP_OPENED'       # set by macOS (LSEnvironment) only when it opens the app
HOOK = 'sitecustomize.py'               # in Contents/Resources: what an opened app's Python runs as it starts
# Its icon: Switchboard's mark, as the link app has it (router/icon.swift draws it; `sh router/build.sh --icon`), so
# Spotlight, Launchpad, Finder and what macOS asks on the page's behalf show the S tile, not a blank app.
ICON = os.path.join(os.path.dirname(core.PACKAGE), 'router', 'SwitchboardLinks.icns')
ICON_NAME = 'Switchboard'               # Contents/Resources/Switchboard.icns
RUNPY = ('import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); '
         'runpy.run_module("switchboard", run_name="__main__", alter_sys=True)')

# The lines an opened app's Python runs as it starts (see above). make() fills in the program and data folders.
HOOK_LINES = """\
# Switchboard.app was opened (Finder, Spotlight, Launchpad): macOS started its Python with nothing to run, and with
# Info.plist's LSEnvironment, so Python imported this file as it started. It hands the start to
# `switchboard menubar --opened`. Started any other way, it does nothing. Made by switchboard/menubar_wrap.py.
import os
import sys

PROGRAM = %(program)r
DATA = %(data)r
RUNPY = %(runpy)r

if sys.argv == [''] and os.environ.pop(%(opened)r, None):
    for name in ('PYTHONPATH', 'PYTHONDONTWRITEBYTECODE'):
        os.environ.pop(name, None)
    os.environ['SWITCHBOARD_HOME'] = DATA
    try:
        os.execv(sys.executable, [sys.executable, '-I', '-B', '-c', RUNPY, PROGRAM, 'menubar', '--opened'])
    except OSError:
        pass
"""


class NotOurs(OSError):
    """Another app (not this install's) is where ours would go."""


def applications():
    """/Applications (SWITCHBOARD_APPLICATIONS stands in for it, for tests)."""
    return os.environ.get('SWITCHBOARD_APPLICATIONS') or APPLICATIONS


def user_applications():
    """~/Applications: for a person who can't write to /Applications without a password."""
    return os.path.join(core.home(), 'Applications')


def folders():
    """Where the app may go, in order."""
    return [applications(), user_applications()]


def data_home():
    return os.path.join(core.data_dir(), 'menubar')


def short(path):
    """A path under the home folder as ~/..., as the installer says it."""
    h, p = os.path.normpath(core.home()), os.path.normpath(path)
    return '~' + p[len(h):] if p == h or p.startswith(h + os.sep) else p


def info(app):
    """An app's Info.plist, or {}."""
    try:
        with open(os.path.join(app, 'Contents', 'Info.plist'), 'rb') as f:
            got = plistlib.load(f)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def ours(app):
    return info(app).get('CFBundleIdentifier') == BUNDLE_ID


def this_data():
    return os.path.realpath(core.data_dir())


def mine(app):
    """Ours, and made for this data folder. One made before the app named its folder counts for the usual one,
    ~/.switchboard, only: a try with another SWITCHBOARD_HOME never takes it over."""
    got = info(app)
    if got.get('CFBundleIdentifier') != BUNDLE_ID:
        return False
    made_for = got.get(DATA_KEY)
    if not isinstance(made_for, str):
        made_for = os.path.join(core.home(), '.switchboard')
    return os.path.realpath(made_for) == this_data()


def home():
    """Where each start makes the app: the Applications folder that holds this install's (while it can be written
    to), else the data folder."""
    for a in folders():
        if mine(os.path.join(a, DIRNAME)) and os.access(a, os.W_OK):
            return a
    return data_home()


def path():
    return os.path.join(home(), DIRNAME)


def present():
    """This install's app as it is now, wherever it is (Applications first), or None."""
    for where in folders() + [data_home()]:
        app = os.path.join(where, DIRNAME)
        if mine(app) and os.path.isfile(os.path.join(app, 'Contents', 'MacOS', 'python')):
            return app
    return None


def running_app(executable=None):
    """The app this interpreter (or `executable`) runs from, when it is ours, else None."""
    exe = executable or sys.executable
    return os.path.dirname(os.path.dirname(os.path.dirname(exe))) if inside_app(exe) else None


def in_applications(app):
    """Is this app in one of the Applications folders (not the data folder)?"""
    return os.path.realpath(os.path.dirname(app)) in [os.path.realpath(f) for f in folders()]


def inside_app(executable=None):
    """Does this interpreter (or `executable`) run from inside our app, as <name>.app/Contents/<folder>/<it>?"""
    app, last = os.path.split(os.path.dirname(os.path.dirname(executable or sys.executable)))
    return last == 'Contents' and app.endswith('.app') and ours(app)


def real_interpreter():
    """The interpreter to copy: the one behind this environment, through any link."""
    return os.path.realpath(getattr(sys, '_base_executable', None) or sys.executable)


def framework(build=None):
    """Is this Python a framework build (python.org's, Homebrew's)? Its app can't be made (see above)."""
    return bool(getattr(sys, '_framework', '') if build is None else build)


# What Contents holds is a list of parts, each (its path under Contents, its kind, what it is made from), made in the
# list's order. A folder's source is its mode; a links folder's is the names it keeps; data is bytes; a copy is the
# path of its original; a link is its target.
_Part = collections.namedtuple('_Part', 'where kind source')
_FOLDER, _LINKS, _DATA, _COPY, _LINK = 'folder', 'links', 'data', 'copy', 'link'
_PROGRAM = os.path.join('MacOS', 'python')


def _lib_links(env_lib, base_lib):
    """Contents/lib's links, name to target: everything in the environment's lib (its python3.X folders), and the base
    install's libpython for an interpreter that loads it from ../lib. A name both have is the environment's."""
    links = {}
    for folder, wanted in ((base_lib, lambda name: name.startswith('libpython')), (env_lib, lambda name: True)):
        if os.path.isdir(folder):
            links.update((name, os.path.join(folder, name)) for name in os.listdir(folder) if wanted(name))
    return links


def _layout(contents, src, env_lib):
    """The parts of the app for the interpreter `src` and the environment lib folder `env_lib`."""
    resources = os.path.join(contents, 'Resources')
    hook = HOOK_LINES % {'program': os.path.dirname(core.PACKAGE), 'data': this_data(), 'runpy': RUNPY,
                         'opened': OPENED}
    # The interpreter itself is the app's executable: menu bar managers know a started-again app by "its executable
    # is CFBundleExecutable" before they give it back its place, and a launcher script in between would hide that.
    # LSEnvironment is set only when macOS opens the app (Finder, Spotlight, Launchpad), never for the menu bar's own
    # start: it points Python at the hook, and keeps it from writing bytecode into the app.
    plist = {'CFBundleIdentifier': BUNDLE_ID, 'CFBundleName': BUNDLE_NAME, 'CFBundleDisplayName': BUNDLE_NAME,
             'CFBundleExecutable': 'python', 'CFBundlePackageType': 'APPL', 'CFBundleShortVersionString': core.VERSION,
             'LSUIElement': True, DATA_KEY: this_data(),
             'LSEnvironment': {'PYTHONPATH': resources, 'PYTHONDONTWRITEBYTECODE': '1', OPENED: '1'}}
    parts = [_Part('MacOS', _FOLDER, 0o755), _Part('Resources', _FOLDER, 0o755),
             # the hook comes before the Info.plist that names it
             _Part(os.path.join('Resources', HOOK), _DATA, hook.encode())]
    if os.path.isfile(ICON):                    # a copy of the program without router/ has none: no icon named
        plist['CFBundleIconFile'] = ICON_NAME
        parts.append(_Part(os.path.join('Resources', ICON_NAME + '.icns'), _COPY, ICON))
    # home is the folder of the base interpreter; Python finds the standard library from it, as in any venv.
    venv = 'home = %s\ninclude-system-site-packages = false\n' % os.path.dirname(src)
    links = _lib_links(env_lib, os.path.join(os.path.dirname(os.path.dirname(src)), 'lib'))
    parts += [_Part('Info.plist', _DATA, plistlib.dumps(plist)), _Part('pyvenv.cfg', _DATA, venv.encode()),
              _Part('lib', _LINKS, frozenset(links))]
    parts += [_Part(os.path.join('lib', name), _LINK, links[name]) for name in sorted(links)]
    parts.append(_Part(_PROGRAM, _COPY, src))
    return parts


def _keep_only_links(folder, names):
    """A real folder (a link in its place goes) whose links are these names only: one left by an older Python or
    environment goes. Files that are not links stay."""
    if os.path.islink(folder):
        os.unlink(folder)
    os.makedirs(folder, exist_ok=True)
    for name in os.listdir(folder):
        if name not in names and os.path.islink(os.path.join(folder, name)):
            os.unlink(os.path.join(folder, name))


def _has_bytes(dest, data):
    try:
        if os.stat(dest).st_size != len(data):
            return False
        with open(dest, 'rb') as f:
            return f.read() == data
    except OSError:
        return False


def _has_copy(dest, original):
    """A copy keeps its original's time, so the same size and time mean the same file, without reading 20 MB."""
    try:
        have, want = os.stat(dest), os.stat(original)
    except OSError:
        return False
    return (have.st_size, have.st_mtime_ns) == (want.st_size, want.st_mtime_ns)


def _has_link(dest, target):
    if os.path.islink(dest):
        return os.readlink(dest) == target
    if os.path.lexists(dest):
        raise OSError('%s is there and is not a link' % dest)
    return False


def _new_file(tmp):
    return os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb')


def _fill_bytes(tmp, data):
    with _new_file(tmp) as f:
        f.write(data)
    os.chmod(tmp, 0o644)


def _fill_copy(tmp, original):
    with _new_file(tmp) as out, open(original, 'rb') as inp:
        shutil.copyfileobj(inp, out, 1 << 20)
    st = os.stat(original)
    os.chmod(tmp, stat.S_IMODE(st.st_mode) | 0o700)
    os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))


def _fill_link(tmp, target):
    os.symlink(target, tmp)


# kind: (is dest already right?, write it at a temporary path)
_MAKERS = {_DATA: (_has_bytes, _fill_bytes), _COPY: (_has_copy, _fill_copy), _LINK: (_has_link, _fill_link)}


def _put(dest, source, already, fill):
    """Make dest anew only when `already(dest, source)` says it is not right: `fill(tmp, source)` makes it beside dest
    under a dot-name, and one rename puts it in place. The menu bar may be running from these files: one replaced by a
    rename keeps running on its old copy, where one written in place would change under it. Nothing is made when
    nothing changed, so a second start leaves the folder as it was. Returns whether dest changed."""
    if already(dest, source):
        return False
    tmp = os.path.join(os.path.dirname(dest), '.%s.%d' % (os.path.basename(dest), os.getpid()))
    if os.path.lexists(tmp):                    # left by a start that stopped half-way, under a reused pid
        os.unlink(tmp)
    try:
        fill(tmp, source)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


def _apply(contents, parts):
    """Bring the folder `contents` in line with `parts`."""
    for part in parts:
        dest = os.path.join(contents, part.where)
        if part.kind == _FOLDER:
            os.makedirs(dest, mode=part.source, exist_ok=True)
        elif part.kind == _LINKS:
            _keep_only_links(dest, part.source)
        else:
            _put(dest, part.source, *_MAKERS[part.kind])


def make(where=None, executable=None, prefix=None):
    """Make the app, or bring it up to date, in `where` (default: home()); returns its interpreter's path.
    `executable` is the real interpreter to copy and `prefix` the environment whose lib holds PyObjC (default:
    this one). Nothing that is already right is written again."""
    src = os.path.realpath(executable or real_interpreter())
    if executable is None and framework():
        raise OSError('a framework build of Python cannot be copied into an app')
    contents = os.path.join(where or home(), DIRNAME, 'Contents')
    _apply(contents, _layout(contents, src, os.path.join(prefix or sys.prefix, 'lib')))
    return os.path.join(contents, _PROGRAM)


def install_in(folder, executable=None, prefix=None):
    """Make the app in this Applications folder. Returns its path, or None (nothing written) when the folder can't be
    written to (~/Applications is made when it is not there). Raises NotOurs when an app that is not this install's is
    already there."""
    app = os.path.join(folder, DIRNAME)
    if os.path.lexists(app) and not mine(app):
        raise NotOurs(app)
    if folder == user_applications() and not os.path.isdir(folder):
        try:
            os.mkdir(folder, 0o700)
        except OSError:
            return None
    if not os.access(folder, os.W_OK):
        return None
    make(folder, executable=executable, prefix=prefix)
    return app


def install_in_applications(executable=None, prefix=None):
    """install_in(/Applications)."""
    return install_in(applications(), executable=executable, prefix=prefix)


def place(executable=None, prefix=None):
    """Put this install's app in Applications, for the installer and `switchboard menubar --applications`:
    /Applications when it can be written to without a password, else ~/Applications. An app there that is not this
    install's is never touched: the other folder is used. Then this install's copies elsewhere go (tidy). Returns (the
    app's path, or None when neither folder would do; the lines to say)."""
    said = []
    for folder in folders():
        try:
            app = install_in(folder, executable=executable, prefix=prefix)
        except NotOurs as e:
            said.append('%s is another app, so it was left alone.' % short(str(e)))
            continue
        except OSError as e:
            said.append("Couldn't put Switchboard in %s: %s" % (short(folder), e))
            continue
        if app:
            tidy(app)
            return app, said
    return None, said


def others(keep):
    """This install's copies of the app besides `keep`: in the other Applications folder and in the data folder."""
    out = []
    for where in folders() + [data_home()]:
        app = os.path.join(where, DIRNAME)
        if os.path.realpath(app) != os.path.realpath(keep) and os.path.isdir(app) and mine(app):
            out.append(app)
    return out


def in_use(app):
    """Does a menu bar of this data folder run from this app now (its lock held, its stamp naming a Python in it)?"""
    from . import menubar
    if menubar.running_pid() is None:
        return False
    python = core.read_json(menubar.stamp_path()).get('python')
    inside = os.path.realpath(app) + os.sep
    return isinstance(python, str) and (os.path.realpath(python) + os.sep).startswith(inside)


def tidy(keep):
    """Take out this install's other copies of the app, but one a menu bar runs from (it goes the next time).
    Returns the paths that went."""
    gone = []
    for app in others(keep):
        if in_use(app):
            continue
        shutil.rmtree(app, ignore_errors=True)
        if not os.path.exists(app):
            gone.append(app)
    return gone


def installer(executable=None, prefix=None):
    """For install.sh, on the menu bar's Python: place() the app, and print one line per thing to say ("say ..." to
    read, "note ..." routine) and "app PATH" for where it is. 0 when it is in Applications, else 1."""
    app, said = place(executable=executable, prefix=prefix)
    for line in said:
        print('say ' + line)
    if not app:
        print("say The menu bar's app stays in %s." % short(data_home()))
        return 1
    print('app ' + app)
    print('note Added Switchboard to %s.' % short(os.path.dirname(app)))
    return 0


def python_problem(python):
    """Start the app's interpreter once and have it load PyObjC: why it can't, or None when it can."""
    try:
        done = subprocess.run([python, '-I', '-B', '-c', 'import objc, AppKit'], capture_output=True, text=True,
                              timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return str(e)
    return _last_words(done) if done.returncode else None


def _last_words(done):
    """The line a failed run ended on (its errors, else its output), or its exit status when it said nothing."""
    said = (done.stderr or done.stdout).strip()
    return said.splitlines()[-1] if said else 'exit status %d' % done.returncode


# Whether to start again inside the app: go, or stay for a reason.
_Answer = collections.namedtuple('_Answer', 'go why')


def _restart_question(platform, environ):
    """The first reason to stay wins, and each is asked only once the ones before it have let the start go on. The
    last is the loop guard: the process started again inside the app stays there."""
    reasons = (('not macOS', lambda: (platform or sys.platform) != 'darwin'),
               ('%s is set' % OPT_OUT, lambda: bool(environ.get(OPT_OUT))),
               ('a framework build of Python', framework),
               ('already in the app', inside_app))
    for why, holds in reasons:
        if holds():
            return _Answer(False, why)
    return _Answer(True, None)


def relaunch_reason(platform=None, environ=None):
    """Why this process stays where it is; None when it should start again inside the app."""
    answer = _restart_question(platform, os.environ if environ is None else environ)
    return None if answer.go else answer.why


def command(python, args):
    """The menu bar again, from the app's interpreter: this program (the folder it is in), with the same words."""
    return [python, '-I', '-B', '-c', RUNPY, os.path.dirname(core.PACKAGE)] + list(args)


class _Stays(Exception):
    """The app can't be used this time; the text says why."""


def _working_python():
    """The app's interpreter, made or brought up to date, once it has started and loaded PyObjC."""
    try:
        python = make()
    except OSError as e:
        raise _Stays('could not make %s: %s' % (path(), e))
    problem = python_problem(python)
    if problem:
        raise _Stays("the app's interpreter does not start: %s" % problem)
    return python


def _become(python, args):
    try:
        os.execv(python, command(python, args))
    except OSError as e:
        raise _Stays('could not start %s: %s' % (python, e))


def relaunch_inside(args):
    """Replace this process with the same command run from inside the app. Returns, with the reason, only when it
    stays: the app could not be made, its interpreter fails to start, or exec refuses. The menu bar then still works;
    menu bar managers just can't see it."""
    why = relaunch_reason()
    if why:
        return why
    try:
        _become(_working_python(), args)
    except _Stays as stays:
        sys.stderr.write('The menu bar runs without its app, so menu bar managers will not see it (%s).\n' % stays)
        sys.stderr.flush()
        return str(stays)
    return None
