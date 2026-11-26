"""Switchboard: every Claude account on one Mac, and links that open as the account that owns them.

  {short}                          the same command as switchboard, shorter (where the installer could add it)
  switchboard                 your accounts and their limits; press a number to move Claude Code there
                              (a login Claude Code is on that is not here yet is kept first)
  switchboard 2               move Claude Code to account 2 (or by its email: switchboard you@example.com)
  switchboard switch [NAME] [--json]
                              move Claude Code to an account (its number, email or name); alone, to the next one;
                              --json prints what happened as JSON
  switchboard status [--json]
                              your accounts and their limits, without the question; --json prints them as JSON
  switchboard add [--as SHORT] [--at N]
                              add accounts, one sign-in at a time (signing in with /login in Claude Code
                              is enough: each login is kept by itself); --as gives the first one kept a short
                              name, --at puts it at place N in the list
  switchboard add PROVIDER    add another provider (codex) to your order; its own app keeps its sign-in, and
                              Switchboard never reads or keeps it (switchboard add claude is switchboard add)
  switchboard add --token [-] [--for NAME | --label WORD] [--as SHORT]
                              keep a one-year token (from claude setup-token) or an API key as an account: asked
                              for without showing it, or read from standard input with -; for one of your accounts
                              or under a name of its own; auto-switch keeps it out until you say otherwise
  switchboard signin          sign in again to every account that needs it, one after another, as the page's
                              Sign in to all does (approve each in the tab that opens); the page must be running
  switchboard remove NAME [--yes]
                              forget an account and its saved login here (Claude Code and the browser are not touched)
  switchboard move NAME N     put an account at place N in the list (1 is first); the others keep their order
  switchboard swap NAME NAME  two accounts change places in the list
  switchboard leftovers [--json]
                              the logins Switchboard keeps aside (recovery copies, none of them a login in use) and
                              whether anything still needs each; --json prints them as JSON
  switchboard leftovers clear ID...|--all [--yes]
                              remove leftover logins that nothing needs (it asks first; --yes answers)
  switchboard leftovers clear ID... --only-copy [--yes]
                              also remove one that is the only copy of a login that was in use (that login is
                              then gone from this Mac for good)
  switchboard alias NAME [SHORT]
                              give an account a short name to type (without SHORT, its short name goes)
  switchboard run [NAME] [--bare] [--no-history|--history] [--strict] -- [claude arguments]
                              Claude Code as that account, in this terminal only, with your chats, memory,
                              instructions and hooks: the login Claude Code uses everywhere else stays as it is;
                              --bare starts it with none of your Claude Code folder
                              --no-history: all of that but your chat history; the chat goes when it ends
                              (it starts nothing, exit 6, on the account Claude Code is on); --history: the chat
                              is kept for this run when the run_history setting is set to apart
                              --strict: never on your own Claude Code folder (exit 6 when NAME is the account
                              Claude Code is on); without NAME: the account set for this folder
  switchboard folder [NAME|off]
                              this folder and every folder in it run as NAME with switchboard run; alone, what
                              this folder runs as; off takes out this folder's own
  switchboard folders [--check|--prune] [--json]
                              every folder set to run as an account; --check lists those whose folder or account
                              is gone, --prune takes them out
  switchboard resume CHAT [--as NAME] [--fork] -- [claude arguments]
                              open a chat again as the account it started under (or as NAME), in this terminal;
                              --fork continues it in a copy. `switchboard chats` lists every chat with its id
  switchboard auto [on|off]   whether auto-switch is on; on or off changes it, and it stays as you set it
  switchboard auto check [--dry-run] [--json] [--five-hour N] [--week N] [--order WORD]
                              one pass of auto-switch now, then exit, also with auto-switch off, for a scheduled
                              job on a Mac where the page is not running: 0 moved, 1 failed or could not look,
                              2 wrong words, 3 held back, 4 nothing to do, 5 the page's loop runs (so it moved
                              nothing); --dry-run decides and says why, and changes nothing; --five-hour, --week
                              and --order are for this check only; --json prints it as JSON
  switchboard auto why [--json] [--five-hour N] [--week N] [--order WORD]
                              what auto-switch would do now, and why (the same as auto check --dry-run, with its
                              codes: 0 it would move, 1 it could not look, 3 it would be held back, 4 it stays)
  switchboard auto last NAME  auto-switch goes to that account only when no other account has room
  switchboard auto out NAME   keep that account out of auto-switch: it never moves Claude Code there, or away from it
  switchboard auto in NAME    auto-switch uses that account as usual
  switchboard auto line NAME [--five-hour N] [--week N]
                              that account switches at lines of its own, from 50 to 99.9; alone, its lines;
                              switchboard auto line NAME off puts it back on the shared ones
  switchboard auto rank NAME N|off
                              auto-switch lands on rank 1 first, then 2, and on an account with no rank after
                              every ranked one; N is 1 to 99; off takes its rank away
  switchboard switch --best   move Claude Code now to the account auto-switch would pick, and say why
  switchboard log [-n N] [-f] [--json]
                              what happened, newest at the bottom: switches and why, keeps, auto-switch, limits,
                              sign-ins, problems; -f follows it, --json prints each line as JSON
  switchboard setup [--yes]   the first run: keeps the account Claude Code is on
  switchboard page            open the page
  switchboard menubar [--applications]
                              the menu bar item: two small bars for the account in use, every account in its menu
                              (it starts at login, or when you open Switchboard in Applications); --applications
                              puts the app back in Applications
  switchboard use NAME [--json]
                              move Claude Code to an account (the same as switch NAME)
  switchboard current [--json]
                              the whole email of the account Claude Code is on, in one line; exit 1 when it is
                              signed out or on a login Switchboard does not keep; --json prints it as JSON
  switchboard update          install the newest version, the way it was installed, with the same choices
  switchboard update --link-app    the same, and add the link app (it needs Apple's command line tools)
  switchboard update --menu-app    the same, and add the menu bar item (--no-menu-app takes it out)
  switchboard update --port N      the same, with the page on port N
  switchboard update --check       whether a newer version is out (it changes nothing); --json prints it as JSON
  switchboard open LINK       open a claude.ai artifact where its owner is signed in
  switchboard owner LINK [--json]
                              which account owns a claude.ai artifact, and how that is known; --json prints it as JSON
  switchboard pages           every artifact this Mac knows, and its account
  switchboard check           prove which browser profile is signed in as which account
  switchboard links [on|off]  whether links you click anywhere open as their owner (Switchboard Links is the default
                              browser); on asks macOS, off gives the seat back to your usual browser
  switchboard usage [--days N] [--json]
                              Work, last 7 days, as History on the page shows it
  switchboard chats [--all] [--days N] [--account NAME] [--json]
                              what is going on in each account: the chats on it now, where each started, how
                              often it moved, what it holds that belongs to another account (--all: the last
                              7 days; --account: one account)
  switchboard providers [--ask] [--json]
                              every provider on this Mac (Claude, Codex): its version, its limits, whether it has
                              room, and what Switchboard can do with it; --ask asks Codex for its numbers now
  switchboard continue [--to PROVIDER] [--chat NAME | --latest] [--in terminal|app] [--show] [--again]
                              carry the chat in this folder on in the next provider with room, in your order: a note
                              of it is made here, secrets taken out as well as Switchboard can, and that provider's
                              app starts on it in this folder (switchboard resume reopens a chat in Claude instead)
  switchboard back [--chat NAME] [--show]
                              come back: a note of what was done in the other provider goes to the chat you left
  switchboard hand PROVIDER [--chat NAME] [--] JOB
                              in a terminal, hand one read-only job to another provider in this folder; its answer
                              is printed
  switchboard allow PROVIDER  in a terminal, say yes once for this folder: its chats and jobs may go to PROVIDER
  switchboard stay PROVIDER|off
                              this folder's chats stay on PROVIDER (switchboard stay claude): continue and hand never
                              send them elsewhere; off lifts it
  switchboard order [PROVIDER ...]
                              the order continue picks the next provider in (Claude first unless you change it); your
                              Claude accounts keep their own order; alone, prints it
  switchboard awake --once [--json]
                              is Claude Code working, and what the power is (holds nothing)
  switchboard config [--json] [--changed] [--as-commands]
                              every setting, grouped as on the page, with its value, its default and what it does;
                              --changed: only those you changed; --as-commands: those as the commands that set them
  switchboard config get KEY [--json] | set KEY VALUE | unset KEY | path
                              one setting's value; set it (checked and applied as the page does); its default back;
                              the files settings are kept in
  switchboard statusline on|off|show [--replace]
                              Switchboard's line in Claude Code: on and off change its settings file,
                              off puts back exactly what was there; --replace stands in for your own line
  switchboard notify [on|off] notifications, with "done" after a long turn and "needs you" from Claude Code:
                              on and off put three hooks in its settings, or take exactly them out
  switchboard hooks [on|off]  the session hooks: they tell an open Claude Code session what a switch means for it
                              (which account owns an artifact, how to open it); on and off change its settings
                              file, and off takes exactly them out
  switchboard models [on|off] Sonnet for helpers: Claude Code's helpers that name no model run on Sonnet
                              instead of the chat's model (one setting in its settings file; off until you turn it on)
  switchboard keys list|add NAME|remove NAME
                              API keys for when every account is out: names and last four only; add asks for
                              the key without showing it and puts it in your Keychain; remove asks first (--yes
                              answers)
  switchboard claude [args]   Claude Code on those keys, for this one terminal (the gateway must be on)
  switchboard export FILE|- [--account NAME]... [--with-keys] [--anyway] [--plain]
                              your accounts' saved logins in one file protected with a passphrase, to carry to
                              another Mac (- writes to standard output); --account: only that account (again for
                              each); --with-keys: your API keys too; --anyway: also into a folder a cloud service
                              copies off the Mac; --plain: with -, the logins unprotected, as JSON. It asks for
                              the passphrase; a script gives it with --passphrase-file PATH (a file only you can
                              read) or --passphrase-fd N
  switchboard export --check FILE|- [--passphrase-file PATH | --passphrase-fd N]
                              whether a file is whole, and what it holds
  switchboard import FILE|- [--replace] [--yes] [--passphrase-file PATH | --passphrase-fd N]
                              keep the accounts of such a file: it shows what it would do first, and never puts an
                              older login over a newer one (--replace takes a newer one from the file, when
                              Anthropic says it is that account's)
  switchboard readings give   this Mac's limits for each account, as a small JSON document
  switchboard readings take [-|FILE] [--for 10m]
                              show them on another Mac for a while:
                              switchboard readings give | ssh other-mac switchboard readings take -
  switchboard doctor          check that everything is in place
  switchboard doctor --fix    repair what can be repaired now, and say what each repair did
  switchboard demo            the page with made-up accounts, to try it first
  switchboard serve           run the page (the background job does this)
  switchboard version         which version this is
  --debug                     after any command or before it (or SWITCHBOARD_DEBUG=1 at a terminal): what it did,
                              on standard error
  short words: ls and list are status, rm is remove, upgrade is update, whoami is current
"""
import http.client
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

from . import artifacts, browser, connect, core, engine, history, keep, linkapp, term
from . import newer
from .accounts import api
from . import names
from .core import SwitchError, load, port

__doc__ = __doc__.replace('{short}', core.SHORT_NAME)     # the short name is defined once, in core

AGENT = 'io.github.amyjainberkeley.switchboard'
EMAIL_RE = re.compile(r'^[^@\s"]+@[^@\s"]+\.[A-Za-z]{2,}$')
POLL = 1.0                          # seconds between looks at which account Claude Code is signed in to
SETTLE = 2.0                        # after a new sign-in, time for Claude Code to finish writing it down
_sleep = time.sleep                 # tests put a stand-in here


# ---------- the running page ----------

_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _page_open(req, timeout):
    """Ask the page on this Mac directly, never through a proxy: http_proxy (or a system proxy that does not list
    127.0.0.1) would take the call elsewhere, and a running page would look stopped."""
    return _DIRECT.open(req, timeout=timeout)


class NotThePage(OSError):
    """Something answered on the page's port, but not as Switchboard's page: another program (the port-in-use case),
    or a page cut off as it stopped. To every caller it is a page that is not running."""


def ask_page(path, body, timeout=120, here=True):
    """POST to the running page; its answer (a JSON object). Raises OSError when the page is not running: nothing
    answers on its port, or what answers is not the page (NotThePage: not HTTP, cut off, not JSON). First it checks
    that the page serves this command's data folder (same_folder): when it does not, nothing is sent. While the page
    waits for its port, which another program holds, the OSError is PageAway and its one sentence is said (page_away);
    `here` is whether the caller then makes the change itself, as every caller does but Sign in to all and check."""
    try:
        same_folder(min(timeout, FOLDER_WAIT))
    except OSError as e:
        raise page_away(e, here)
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (port(), path), data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json', 'X-Switchboard': '1'})
    try:
        with _page_open(req, timeout=timeout) as r:
            got = json.load(r)
    except urllib.error.HTTPError as e:
        try:
            said = json.load(e)
        except Exception:  # noqa: BLE001 - a refusal whose words can't be read is still a refusal
            said = {}
        finally:
            e.close()
        said = said if isinstance(said, dict) else {}
        raise engine.Refused(said.get('error') or 'the page said no', said.get('fix'))
    except (http.client.HTTPException, ValueError) as e:
        raise page_away(NotThePage('port %d answered, but not as the page: %r' % (port(), e)), here)
    except OSError as e:
        raise page_away(e, here)
    if not isinstance(got, dict):
        raise NotThePage('port %d answered, but not as the page' % port())
    return got


class PageAway(OSError):
    """The page can't be reached because it waits for its port, which another program holds (watch.port_mark). To
    every caller it is a page that is not running: a change is made here instead."""


PORT_HELD = 'Port %d is in use by another program, so this command can\'t reach the page'
PORT_WAITED = 'Nothing answers on port %d yet: the page waits there for the port to be free'


def away_words(n, refused, here):
    """The sentence for a page that waits for port n: PORT_WAITED when nothing answered there (refused), PORT_HELD
    when something else did (or hung); that this command makes the change itself when it goes on without the page
    (`here`: every caller but Sign in to all, which runs only on the page, and check, which changes nothing); then the
    one thing to do, in the page's own words for a taken port (watch.free_port_words)."""
    from . import watch
    if refused:
        said = PORT_WAITED % n + (', so this command makes the change itself' if here else '')
    else:
        said = PORT_HELD % n + (' and makes the change itself' if here else '')
    return '%s. %s' % (said, watch.free_port_words(n))


def page_away(e, here=True):
    """What ask_page raises when the page can't be reached: PageAway, its one sentence (away_words) said on stderr,
    while the page waits for this port (the page's own mark, watch.port_mark, and the page not stopped on purpose).
    Otherwise `e` as it was."""
    from . import watch
    try:
        mark = watch.port_mark()
        if not mark or mark['port'] != port() or watch.stopped() is not None:
            return e
    except Exception:  # noqa: BLE001 - the plain error, as before
        return e
    refused = isinstance(e, ConnectionRefusedError) or isinstance(getattr(e, 'reason', None), ConnectionRefusedError)
    words = away_words(port(), refused, here)
    sys.stderr.write(words + '\n')
    return PageAway(words)


FOLDER_WAIT = 30            # seconds the look at the page's data folder may take before a change: the page answers its
                            # last state at once (or says it is busy), so this is only reached by a page that hangs
OTHER_FOLDER = ('The page on port %d serves another data folder than this command uses. Nothing was changed. Run it '
                "with the page's data folder, or give it its own port with SWITCHBOARD_PORT.")
OLDER_PAGE = ('The page on port %d does not say which data folder it serves (it is from an older Switchboard). '
              'Nothing was changed. %s')
DEMO_PAGE = ('The page on port %d is the demo, with made-up accounts. Nothing was changed. Stop the demo (Ctrl-C where '
             'it runs), then run this again.')


def same_folder(timeout=FOLDER_WAIT):
    """Before a change through the running page (ask_page): the page on this command's port serves this command's
    data folder, by the mark in its state answer (core.folder_mark). Raises engine.Refused, in one sentence, when it
    serves another folder, is the demo, or does not say (a page from before this check: refused, since a command given
    a folder of its own but no port would otherwise change whatever page answers on the usual port). Raises OSError
    as ask_page does when nothing answers there or what answers is not the page. Commands that only read never ask."""
    url = 'http://127.0.0.1:%d/api/state' % port()
    try:
        with _page_open(url, timeout=timeout) as r:
            got = json.load(r)
    except urllib.error.HTTPError as e:
        try:
            got = json.load(e) if e.code == 503 else None       # busy: its answer still carries the mark
        except Exception:  # noqa: BLE001 - an answer that can't be read is not the page's
            got = None
        finally:
            e.close()
        if not (isinstance(got, dict) and got.get('busy') is True):
            raise NotThePage('port %d answered, but not as the page' % port())
    except urllib.error.URLError as e:
        if isinstance(getattr(e, 'reason', None), ConnectionRefusedError):
            return                  # nothing listens there: the request itself finds the same, as before this check
        raise
    except (http.client.HTTPException, ValueError) as e:
        raise NotThePage('port %d answered, but not as the page: %r' % (port(), e))
    if not isinstance(got, dict) or not (isinstance(got.get('accounts'), list) or got.get('busy') is True):
        raise NotThePage('port %d answered, but not as the page' % port())
    if got.get('demo') is True:
        raise engine.Refused(DEMO_PAGE % port())
    mark = got.get('folder')
    if not isinstance(mark, str) or not mark:
        raise engine.Refused(OLDER_PAGE % (port(), restart_page_words()))
    if mark != core.folder_mark():
        raise engine.Refused(OTHER_FOLDER % port())


def restart_page_words():
    """The one thing to do for a page that runs older code than the program on disk, in the words doctor uses for a
    page to restart: its background job started again when this install has one, else the page started again by
    hand."""
    plist = os.path.join(core.home(), 'Library', 'LaunchAgents', AGENT + '.plist')
    if os.path.exists(plist):
        return 'Restart the page: launchctl kickstart -k gui/%d/%s' % (os.getuid(), AGENT)
    return 'Stop that page and start it again: switchboard serve'


def page_state(timeout=5):
    """The running page's state (GET /api/state), or None when the page is not running: nothing answers on its port,
    or what answers is not Switchboard's own page (another program, an answer cut off, the demo)."""
    try:
        with _page_open('http://127.0.0.1:%d/api/state' % port(), timeout=timeout) as r:
            got = json.load(r) if r.status == 200 else None
    except Exception:  # noqa: BLE001 - refused, timed out, cut off (http.client.HTTPException), not JSON: no page
        return None
    return got if isinstance(got, dict) and isinstance(got.get('accounts'), list) and got.get('demo') is not True \
        else None


def page_status(timeout=3):
    """'running': Switchboard's own page answers on its port with its state; 'busy': it is running and said it is too
    busy to answer just now (its 503 with busy and its version), or took longer than `timeout` to answer; 'down':
    nothing answers there, or what answers is not Switchboard's page."""
    try:
        with _page_open('http://127.0.0.1:%d/api/state' % port(), timeout=timeout) as r:
            got = json.load(r) if r.status == 200 else None
    except urllib.error.HTTPError as e:
        try:
            said = json.load(e) if e.code == 503 else None
        except Exception:  # noqa: BLE001 - an answer that can't be read is not the page's busy answer
            said = None
        finally:
            e.close()
        return 'busy' if isinstance(said, dict) and said.get('busy') is True and 'version' in said else 'down'
    except (TimeoutError, socket.timeout):
        return 'busy'                           # it took the request, and did not answer in time
    except urllib.error.URLError as e:
        return 'busy' if isinstance(getattr(e, 'reason', None), (TimeoutError, socket.timeout)) else 'down'
    except Exception:  # noqa: BLE001 - refused, cut off, not HTTP, not JSON: not the page
        return 'down'
    ok = isinstance(got, dict) and isinstance(got.get('accounts'), list) and got.get('demo') is not True
    return 'running' if ok else 'down'


def page_running():
    """Switchboard's own page is running: it answers on its port with its state, or says it is busy (a busy page is
    running, never "not running")."""
    return page_status(timeout=3) != 'down'


def open_page(wait=True):
    """Open the page in the browser with macOS's `open`. When macOS does not let this terminal start it (a sandbox can
    do that), or it fails, the address is said instead, to open by hand. `wait`: wait for `open` and read how it went
    (the demo only starts it, and serves on). True when it was opened, or started."""
    url = 'http://localhost:%d' % port()
    try:
        if not wait:
            subprocess.Popen(['/usr/bin/open', url])
            return True
        if subprocess.call(['/usr/bin/open', url]) == 0:
            return True
    except OSError:
        pass
    print('Open %s in your browser.' % url)
    return False


# ---------- what the terminal shows: the page's own state ----------

def identity():
    """(email, organization) of the account Claude Code's config names now, or None."""
    return engine.identity()


def local_view():
    """The page's state worked out here, the way the page works it out (server.apply_rows, then server.render), from
    a fresh look at the engine. For when the page is not running. It only looks: nothing is saved."""
    from . import server
    got = engine.listing()
    st = load()
    st['active'] = None                         # so no switch is noted: this is a look, not a change
    server.apply_rows(st, got['rows'])
    st['disagree'] = bool(got.get('disagree'))
    server.apply_live_numbers(st, time.time())
    # Only what the accounts need: the page's own facts also ask the browser and the link app.
    facts = {'profiles': {}, 'now': time.time(), 'auto': engine.auto_settings(),
             'engine': {'installed': True, 'claude': None}, 'links': {'on': False}, 'guard': server.guard_line(),
             'browser': {'name': '', 'present': False, 'tabs_blocked': False},
             'setup_hidden': True, 'demo': False, 'connecting': []}
    try:
        return server.render(st, facts)
    except KeyError:                            # the page reads a fact this list lacks: use the page's own
        return server.render(st, dict(server.facts(), connecting=[]))


def current_view(fresh=False):
    """The page's state: the running page's own, else worked out here. `fresh` skips the page (right after a first
    run, before the page has looked again). The demo's is its made-up one."""
    if core.DEMO:
        from . import server
        return server.view()
    got = None if fresh else page_state()
    if got is None:
        with transient('Reading the accounts…'):
            got = local_view()
    return got


def terminal():
    """True when a person is at this terminal: both ends are one."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def colour():
    return term.colour_ok(sys.stdout, os.environ)


def width():
    """The terminal's width, or None when the output goes elsewhere (then nothing is narrowed)."""
    try:
        if sys.stdout.isatty():
            return shutil.get_terminal_size((80, 24)).columns
    except (AttributeError, ValueError):
        pass
    cols = os.environ.get('COLUMNS', '')
    return int(cols) if cols.isdigit() and int(cols) > 0 else None


@contextmanager
def transient(text):
    """A quiet line that shows while something runs and is gone after (only on a terminal)."""
    shown = False
    try:
        if sys.stdout.isatty() and os.environ.get('TERM') != 'dumb':
            sys.stdout.write('\r' + term.paint([(text, term.DIM)], colour()))
            sys.stdout.flush()
            shown = True
    except (AttributeError, ValueError):
        pass
    try:
        yield
    finally:
        if shown:
            sys.stdout.write('\r\x1b[2K')
            sys.stdout.flush()


def closing(view, state):
    """(a last line for what is missing, or None; whether there is anything to switch to). Another program that
    switches Claude Code's login too is said first: it changes nothing about what can be switched to by hand."""
    line, can = _closing(view, state)
    return (view['guard'] if view.get('guard') else line), can


def _closing(view, state):
    accounts = state['accounts']
    saved = [a for a in accounts if not a['unmanaged']]
    on = state['head']['email']
    if not accounts:                            # none at all: Claude Code is not signed in either
        return term.run_this('Install Claude Code first (https://claude.com/claude-code), then run' if not
                             connect.claude_bin() else 'Sign in to Claude Code first, then run', 'switchboard setup',
                             colour()), False
    if not saved:
        return term.run_this('Add it with', 'switchboard setup', colour()), False
    if on and not any(a['email'] == on for a in accounts):
        return term.run_this('%s is not in Switchboard yet. Add it with' % on, 'switchboard add', colour()), True
    if len(saved) == 1 and saved[0]['active']:
        return term.another_account(job_installed()), False
    return None, True


def show(view, ask=False):
    """Print the view; in a terminal, then ask which account to use. Returns the exit code."""
    live = identity() if not core.DEMO else None
    state = term.read(view, live=live[0] if live else None)
    from . import bearers
    state['head']['email'] = bearers.on_words(view) or state['head']['email']     # on a kept token: that, by name
    print('\n'.join(term.view(state, width(), colour()) + bearers.term_lines(view, colour())))
    line, can = closing(view, state)
    if line:
        print()
        print(line)
    late = watch_line(view)
    if late:
        print()
        print(late)
    for x in term.providers_lines(view.get('providers'), view.get('now') or time.time(), width(), colour()):
        print(x)                                # the other assistants, from the page's board (none: no group)
    slots = [a['slot'] for a in state['accounts'] if a['slot'] and not a['unmanaged']]
    if not (ask and can and slots):
        return 0
    print()
    picked = pick(slots)
    if picked is None:
        return 0
    return act_on(next(a for a in state['accounts'] if a['slot'] == picked), view)


def read_key(fd):
    """One key from the terminal, without waiting for Enter or showing it."""
    import termios
    import tty
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        key = os.read(fd, 1)
        if key == b'\x1b':                      # the rest of an arrow key's sequence, if any
            import select
            while select.select([fd], [], [], 0.02)[0]:
                os.read(fd, 8)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return key.decode('utf-8', 'replace')


def read_one_key():
    return read_key(sys.stdin.fileno())


def pick(slots):
    """'Switch to 1–5, or Enter to stay › ': a number picks that account; Enter, q or Escape leave everything as it
    is. Past slot 9 a number is typed and Enter pressed. Returns the slot, or None."""
    sys.stdout.write(term.prompt(slots, colour()))
    sys.stdout.flush()
    if max(slots) > 9:
        try:
            said = sys.stdin.readline().strip()
        except KeyboardInterrupt:
            said = ''
            sys.stdout.write('\n')
        if said.isdigit() and int(said) in slots:
            return int(said)
        return None
    while True:
        try:
            k = read_one_key()
        except KeyboardInterrupt:
            k = '\x03'
        if k in ('', '\r', '\n', 'q', 'Q', '\x1b', '\x03', '\x04'):
            sys.stdout.write('\n')
            return None
        if k.isdigit() and int(k) in slots:
            sys.stdout.write(k + '\n')
            return int(k)


def act_on(a, view):
    """Use the account picked: switch to it, or start its sign-in when its login needs a fresh one."""
    if a['needs_login'] and not a['active']:
        return sign_in(a['email'])
    if a['needs_login']:
        print('In Claude Code, run /login and sign in as %s.' % a['email'])
        return 0
    if not a['active']:
        switch(a['email'], view)
    print(term.now_on(a['email'], colour()))
    return 0


def switch(email, view):
    """Move Claude Code to the account: through the running page when it is up, else the engine itself."""
    if core.DEMO:
        from . import server
        server.act('/api/switch', {'email': email})
        return
    a = next((x for x in view.get('accounts') or [] if x.get('email') == email), {})
    if a.get('unmanaged') or not a.get('slot'):         # only before the first run: nothing is added yet
        raise SwitchError('%s is not in Switchboard yet. Add it with: switchboard setup' % email)
    try:
        said = ask_page('/api/switch', {'email': email}).get('note')
        if said:                                        # the account's links or Claude in Chrome; the desktop line once
            print(said)
    except OSError:
        since = time.time()
        engine.switch(a.get('id') or email, source='cli', reason='Picked in a terminal.')    # the page is down
        history.switched_here(email, next((x.get('email') for x in view.get('accounts') or [] if x.get('active')),
                                          None), since=since)
        artifacts._map_soon()                   # the link app and the session hooks read the account in use from it


def resolve(name, view):
    """The account someone typed: its email, its number, or (for `use`) its name."""
    want = name.strip().lower()
    for a in view.get('accounts') or []:
        if want in ((a.get('email') or '').lower(), (a.get('alias') or '').lower(), (a.get('name') or '').lower(),
                    str(a.get('slot'))):
            return a
    fits = names.fitting(want, view.get('accounts'))           # one email, two organizations: say how to name one
    if len(fits) > 1:
        raise SwitchError(names.ambiguous(want, [(a.get('slot'), names.org_of(a['email'])) for a in fits]))
    if fits:
        return fits[0]
    if '@' in want:
        raise SwitchError('%s is not in Switchboard. Add it with: switchboard add' % name)
    raise SwitchError('No account called "%s". Run `switchboard` to see them.' % name)


def home():
    """`switchboard`: the accounts, and in a terminal the question. A first run (nothing added yet) comes first; a
    login Claude Code is on that is not here yet is kept first."""
    ask = terminal()
    if ask and not core.DEMO and not saved_identities():
        if identity():
            code = first_run(Tty(sys.stdin, sys.stdout), last_line=False)
            if code:
                return code
            print()
            return show(current_view(fresh=True), ask=True)
    kept = keep_now()
    return show(current_view(fresh=kept), ask=ask)


def status():
    kept = keep_now()
    code = show(current_view(fresh=kept))
    if terminal() and not core.DEMO:            # in a folder set to run as an account, one line says so
        from . import foldercmd
        line = foldercmd.status_line()
        if line:
            print(line)
    said = newer.line()                 # a newer version, in one line, only while the look by itself is on
    if said:
        print()
        print(said)
    return code


def status_json():
    """`switchboard status --json`: the accounts and their limits as one JSON document (report.py). It only looks: a
    login Claude Code is on that Switchboard does not hold is shown, never kept (no keep_now), nothing is written, and
    nothing but the document reaches standard output (no transient line). Accounts that can't be read at all raise
    SwitchError, as `switchboard usage` does: a sentence on stderr, exit 1, nothing on stdout."""
    from . import report
    if core.DEMO:
        from . import server
        view, live = server.view(), None
    else:
        view = page_state() or local_view()
        me = identity()
        live = me[0] if me else None
    from . import foldercmd
    here = None if core.DEMO else foldercmd.this_folder()
    print(json.dumps(report.document(view, core.VERSION, live=live, this_folder=here), indent=1, ensure_ascii=False))
    return 0


# ---------- keeping the login Claude Code is on, and auto-switch on by itself (keep.py), from the terminal ----------

def keep_now():
    """Bare `switchboard` and `switchboard status` do at once what the page does by itself: the login Claude Code is
    on, when Switchboard does not hold it yet, is kept (it changes nothing else). Never one removed here (only add
    keeps that again). One line says so, above the view. Returns True when kept."""
    if core.DEMO:
        return False
    me = identity()
    if not me or me in saved_identities() or keep.is_let_go(me):
        return False
    try:
        save_current(by='auto')
    except Exception as e:  # noqa: BLE001 - whatever stopped it, the view still shows
        print('Could not keep %s: %s' % (me[0], e if isinstance(e, SwitchError) else
                                         'the page did not answer as it should.'))
        print()
        return False
    print(term.kept(me[0]))
    auto_here()
    print()
    return True


def switchable_here():
    """How many accounts automatic switching can move Claude Code to, as the engine keeps them (not needing a fresh
    sign-in), less any the running page last saw needing one."""
    try:
        rows = engine.rows()
    except SwitchError:
        return 0
    out = set(e for e in (a.get('email') for a in (page_state() or {}).get('accounts') or [] if a.get('needs_login'))
              if isinstance(e, str))
    return len([r for r in engine.held(rows) if r.get('status') not in keep.NOT_SWITCHABLE and r['email'] not in out])


def _turn_on_here():
    """Auto-switch on, as the page's switch turns it on, but its loop is left to the page: it runs only there."""
    with outside_the_page():
        engine.set_auto(enabled=True)


def auto_here(say=True):
    """After the terminal kept an account: auto-switch on by itself with two accounts to switch
    between (keep.auto_by_itself). Says so in one line (unless `say` is False). Returns True when it turned on."""
    try:
        on = keep.auto_by_itself(switchable_here(), turn_on=_turn_on_here)
    except Exception as e:  # noqa: BLE001 - a convenience: it never stops the command it follows
        core.log('auto-switch was not turned on: %r' % e)
        return False
    if on and say:
        print(term.AUTO_ON)
    return on


def use(name):
    view = current_view()
    try:
        a = resolve(name, view)
    except SwitchError:
        from . import bearers
        t = bearers.find(name)              # a one-year token or an API key kept as an account, by its name
        if t is None:
            raise
        return bearers.switch_to(t)
    if not a.get('active'):
        switch(a['email'], view)
    print(term.now_on(a['email'], colour()))
    return 0


def go_to(target):
    """`switchboard 2`, `switchboard you@example.com`."""
    view = current_view()
    want = target.strip().lower()
    a = next((x for x in view.get('accounts') or [] if want in ((x.get('email') or '').lower(), str(x.get('slot')))),
             None)
    fits = names.fitting(want, view.get('accounts')) if a is None else []
    if len(fits) > 1:                                           # one email, two organizations: say how to name one
        raise SwitchError(names.ambiguous(want, [(x.get('slot'), names.org_of(x['email'])) for x in fits]))
    a = a or (fits[0] if fits else None)
    if a is None:
        if '@' in want:
            raise SwitchError('%s is not in Switchboard. Add it with: switchboard add' % target)
        raise SwitchError('No account %s. Run `switchboard` to see them.' % target)
    row = next(x for x in term.read(view)['accounts'] if x['email'] == a['email'])
    if row['needs_login'] and not row['active'] and not terminal():
        raise SwitchError('%s needs a fresh sign-in: in Claude Code, run /login and sign in as it, then run '
                          'switchboard add.' % a['email'])
    return act_on(row, view)


# ---------- asking, in the terminal itself ----------

class Tty(object):
    """Where a question is asked: the terminal itself. `switchboard setup` runs from the installer, whose input is
    the installer's own text when it came through a pipe (curl ... | sh), so answers are read from /dev/tty."""

    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    def ask(self, text):
        """Show `text`, read one line. Raises EOFError when the terminal closes."""
        out = sys.stdout if sys.stdout.isatty() else self.writer
        out.write(text)
        out.flush()
        line = self.reader.readline()
        if not line:
            out.write('\n')
            raise EOFError
        return line.strip().lower()

    def yes(self, text, default):
        """[Y/n] or [y/N]: Enter takes the default; anything else is asked again."""
        while True:
            said = self.ask(text)
            if said in ('y', 'yes') or (not said and default):
                return True
            if said in ('n', 'no') or not said:
                return False


def open_tty():
    """The terminal this runs in, or None when there is none (a script, or Claude Code running it)."""
    try:
        reader = open('/dev/tty')
    except OSError:
        return None
    try:
        writer = open('/dev/tty', 'w')
    except OSError:
        reader.close()
        return None
    return Tty(reader, writer)


def question_colour(tty):
    return term.colour_ok(sys.stdout if sys.stdout.isatty() else tty.writer, os.environ)


# ---------- adding accounts ----------

def save_current(by='you'):
    """Hand the engine the login Claude Code is on now: added, or brought up to date when it is already there. Through
    the running page when it is up (it looks again at once), else the engine itself. A new one goes in the history as
    kept `by` you (add, setup) or by itself (bare `switchboard`)."""
    try:
        ask_page('/api/engine', dict({'add': True}, **({'by': 'auto'} if by == 'auto' else {})))
    except OSError:
        before = saved_identities()
        engine.add_current()
        history.kept_by_add(before, by)


def saved_identities():
    """(email, organization) of every account the engine keeps."""
    return engine.saved_identities()


def wait_for_login(before):
    """Wait until Claude Code is signed in to an account other than `before` (None: any). Looks again each time
    Claude Code's own file changes. Returns (email, organization)."""
    path, last = core.claude_json(), None
    with transient('Waiting… Ctrl-C to stop'):
        while True:
            try:
                st = os.stat(path)
                seen = (st.st_mtime_ns, st.st_size)
            except OSError:
                seen = None
            if seen != last:
                last = seen
                now = identity()
                if now and now != before:
                    return now
            _sleep(POLL)


def save_new():
    """Save the login that just arrived, after a moment for Claude Code to finish writing it; tries three times."""
    _sleep(SETTLE)
    for tries in range(3):
        try:
            return save_current()
        except engine.Refused:
            if tries == 2:
                raise
            _sleep(SETTLE)


def add(args):
    """`switchboard add`: one sign-in at a time. In Claude Code, /login to the next account; Switchboard notices it
    and saves it, then asks whether there is another."""
    if args:                                    # --as SHORT, --at N: places.py, which runs this with none
        from . import places
        return places.add_named(args, sys.modules[__name__])
    if core.DEMO:
        raise SwitchError('The demo has made-up accounts, so there is nothing to add.')
    tty = open_tty()
    me = identity()
    if not me and not connect.claude_bin():
        raise SwitchError('Claude Code is not installed on this Mac. Install it first: https://claude.com/claude-code')
    if me:
        new = me not in saved_identities()
        save_current()                          # its newest login is kept before Claude Code moves to another
        keep.forget(me)                         # kept on purpose: a removed account comes back with add
        if new or tty is None:
            print(term.saved(me[0]))
        auto_here()
    if tty is None:
        if not me:
            raise SwitchError('Claude Code is not signed in. Run /login in Claude Code, then switchboard add again.')
        print('To add another, run /login in Claude Code, then switchboard add again.')
        return 0
    paint = question_colour(tty)
    try:
        while True:
            print('In Claude Code, run /login and sign in to the next account.')
            me = wait_for_login(me)
            save_new()
            keep.forget(me)
            print(term.saved(me[0]))
            auto_here()                         # the second account: auto-switch on by itself, said once
            if not tty.yes(term.add_another(paint), default=False):
                break
    except (KeyboardInterrupt, EOFError):
        print()
        return 0
    print(term.all_accounts(len(saved_identities()), colour()))
    return 0


def sign_in(email):
    """A fresh sign-in for an account whose login stopped working: /login in Claude Code as that account."""
    me = identity()
    if me and me in saved_identities():
        save_current()                          # the account in use keeps its newest login
    print('In Claude Code, run /login and sign in as %s.' % email)
    try:
        now = wait_for_login(me)
    except KeyboardInterrupt:
        print()
        return 0
    if now[0] != email.lower():
        print('Claude Code is on %s now, not %s. Nothing was saved.' % (now[0], email))
        return 1
    save_new()
    print(term.saved(now[0]))
    return 0


FOLLOW_EVERY = 2.0                  # seconds between two looks at Sign in to all while `switchboard signin` follows it


def signin_words(q):
    """Where Sign in to all is, in the page's words (signinRunning in index.html)."""
    who, done = q.get('current'), '%d of %d done.' % (q.get('done') or 0, q.get('of') or 0)
    if not who:
        return 'Signing in again… ' + done
    if q.get('waiting'):
        return 'Keeping the new sign-in of %s. %s' % (who, done)
    return 'Signing in to %s: approve it in the tab that opened. %s' % (who, done)


def signin_command(args):
    """`switchboard signin`: the page's Sign in to all, through the running page (POST /api/signins): every account
    that needs it, one after another, each opened in its own browser profile for your yes. It says where it is until
    it ends, then the page's closing line; Ctrl-C stops it, as the page's Stop does. Without the page it starts
    nothing: the queue lives in the page."""
    if args:
        print(command_help('signin'))
        return 2
    try:
        got = ask_page('/api/signins', {'do': 'start'}, here=False)
    except PageAway:
        print('Sign in to all runs on the page.')         # why the page can't be reached was said just above
        return 1
    except OSError:
        print('The page is not running, and Sign in to all runs there. Start it with `switchboard serve`, or run the '
              'installer again.')
        return 1
    if got.get('line'):
        print(got['line'])
        return 0
    q, said = got.get('signins') or {}, None
    try:
        while q.get('state') == 'running':
            line = signin_words(q)
            if line != said:
                print(line)
                said = line
            _sleep(FOLLOW_EVERY)
            st = page_state()
            if st is None:
                print('The page stopped answering. Open it to see where Sign in to all is.')
                return 1
            q = st.get('signins') or {}
    except KeyboardInterrupt:
        print()
        try:
            got = ask_page('/api/signins', {'do': 'stop'}, here=False)
        except OSError:
            return 1
        print('Stopped. What was signed in stays signed in.')
        return 1
    print(q.get('line') or 'Sign in to all has ended.')
    return 1 if q.get('skipped') else 0


# ---------- the first run ----------

class _NoLoop(object):
    """Stands in for the loop's host while the terminal turns auto-switch on or off outside the page: the loop that
    reads the accounts and switches runs only inside the page, so a terminal never starts or stops it. The setting
    itself is still written."""

    def ensure(self, now=None):
        pass


@contextmanager
def outside_the_page():
    real = engine.HOST
    engine.HOST = _NoLoop()
    try:
        yield
    finally:
        engine.HOST = real


def job_installed():
    """Switchboard's background job is set up (it starts the page at login, and again if it stops)."""
    return os.path.exists(os.path.join(core.home(), 'Library', 'LaunchAgents', AGENT + '.plist'))


def first_step():
    """With no account yet: Claude Code to install, or to sign in to."""
    if not connect.claude_bin():
        return 'Install Claude Code first (https://claude.com/claude-code), then run switchboard setup again.'
    return 'Sign in to Claude Code first, then run switchboard setup again.'


def first_run(tty, yes=False, last_line=True):
    """The first minute: keep the account Claude Code is on (nothing asked: it changes nothing). With two accounts
    auto-switch goes on, and notifications and the status line go on (keep.py). At a terminal, macOS is asked once to
    make the link app the default browser, so links open as their owner. Without a terminal nothing is asked, and one
    line says what is left. Returns 0, or 1 when something failed."""
    if core.DEMO:
        raise SwitchError('The demo has made-up accounts, so there is nothing to set up.')
    one_line = tty is None and not yes
    me = identity()
    if me:
        try:
            save_current()
        except SwitchError:
            if not one_line:
                print(term.found_login(me[0]))
            raise
        keep.forget(me)                         # the first run keeps it on purpose, as add does
        if not one_line:
            print(term.found_login(me[0], kept=True))
    n = len(saved_identities())
    end = ended(n)
    if one_line:
        if end['lined'] == 'theirs':
            print(term.STATUSLINE_THEIRS)
        print(term.done(n, colour(), auto=end['auto'], notify=end['notify'], statusline=end['statusline'])
              if n else first_step())
        return 0
    # Links open as their owner: at a terminal, with an account kept, macOS is asked once to make the link app the
    # default browser (keep.links_by_itself); its question is said here, before the last line.
    links = bool(n) and tty is not None and keep.links_by_itself()
    if me:                                      # each other account: sign in with /login, and it is kept
        if end['turned']:
            print(term.AUTO_ON)
        said = term.on_words(notify=end['notified'], statusline=end['lined'] == 'on').strip()
        if said:
            print(said)
        if end['lined'] == 'theirs':
            print(term.STATUSLINE_THEIRS)
        if links:
            print(term.LINKS_FIRST_RUN)
        if last_line:
            print(term.sign_in_others(job_installed()))
        return 0
    if end['lined'] == 'theirs' and n:
        print(term.STATUSLINE_THEIRS)
    if links:
        print(term.LINKS_FIRST_RUN)
    if last_line:
        print(term.done(n, colour(), auto=end['auto'], notify=end['notify'], statusline=end['statusline'])
              if n else first_step())
    elif n:
        print(term.counted(n, end['auto'], end['notify'], end['statusline']))
    return 0


def ended(n):
    """The end of the first run, with `n` accounts kept: auto-switch on by itself with two accounts to switch between,
    and notifications and the status line on by themselves, once (keep.py). Returns {'auto': auto-switch on now, 'turned': turned on now, 'notify': the Notifications switch on now,
    'notified': turned on now, 'statusline': Switchboard's status line on now, 'lined': what keep.statusline_by_itself
    did ('on', 'theirs' or None)}."""
    end = {'turned': False, 'notify': False, 'notified': False, 'statusline': False, 'lined': None}
    from . import server
    server.statusline_every_minute()            # a status line from before it ran every minute gets that, once
    if n:
        end['turned'] = auto_here(say=False)
        end['notified'] = keep.notify_by_itself()
        end['notify'] = notifications_on()
        end['lined'] = keep.statusline_by_itself()
        end['statusline'] = statusline_on()
        end['hooked'] = keep.session_hooks_by_itself()      # the page's history says so; the first run's words don't
    end['auto'] = bool(engine.auto_settings().get('enabled'))
    return end


def statusline_on():
    """Switchboard's status line is in Claude Code's settings (server.statusline_status), as the page's switch shows."""
    from . import server
    try:
        return server.statusline_status().get('on') is True
    except Exception as e:  # noqa: BLE001 - only the first run's words depend on it
        core.log('reading the status line switch: %r' % e)
        return False


def notifications_on():
    """The Notifications switch as the page shows it (server.mac_facts): Switchboard's own notes on, and Claude
    Code's "done" and "needs you" hooks in and able to run."""
    from . import server
    try:
        return bool(server.mac_facts()['notify'])
    except Exception as e:  # noqa: BLE001 - only the first run's words depend on it
        core.log('reading the Notifications switch: %r' % e)
        return False


def setup(args):
    """`switchboard setup [--yes]`: the first run, as the installer ends. (--no-bringover is taken and changes
    nothing.)"""
    if any(a not in ('--yes', '--no-bringover') for a in args):
        print('switchboard setup [--yes]')
        return 2
    return first_run(open_tty(), yes='--yes' in args)


# ---------- auto-switch, from the terminal ----------

NOT_RUNNING_HERE = 'It runs in the page, which is not running. Start it with: switchboard serve'
NOT_READ_HERE = 'The accounts are read in the page, which is not running. Start it with: switchboard serve'


AUTO_FORMS = ('switchboard auto [on|off]',
              'switchboard auto check [--dry-run] [--json] [--five-hour N] [--week N] [--order WORD]',
              'switchboard auto why [--json] [--five-hour N] [--week N] [--order WORD]',
              'switchboard auto last NAME', 'switchboard auto out NAME',
              'switchboard auto in NAME', 'switchboard auto line NAME [--five-hour N] [--week N]',
              'switchboard auto line NAME off', 'switchboard auto rank NAME N|off')


def auto_command(args):
    """`switchboard auto [on|off]`. Alone: one line, whether auto-switch is on, at which lines, in which order, and
    while it is off with an account kept, that the accounts are still read; then one line for each account with a rule
    (term.rule_lines). On or off: through the running page (POST /api/auto, as the menu bar sends it) when it answers,
    else here; either way it is yours from then on (it never turns on by itself again). Then the new state, in the
    same one line. Without the page or its job, a second line says where it runs. `last`, `out` or `in` NAME: that
    account's rule (auto_rule). `line NAME ...` and `rank NAME ...`: its own lines and its rank (autorules.py); the
    line for each account then says them too."""
    if args[:1] in (['check'], ['why']):            # one pass now, or what it would do and why (autocheck.py)
        from . import autocheck
        return autocheck.main(args)
    if len(args) == 2 and args[0] in engine.RULES:
        return auto_rule(args[0], args[1])
    if args[:1] in (['line'], ['rank']):                # an account's own lines, its rank (autorules.py)
        from . import autorules
        return (autorules.auto_line if args[0] == 'line' else autorules.auto_rank)(args[1:], ask_page,
                                                                                   outside_the_page, history)
    if args not in ([], ['on'], ['off']):
        print('\n'.join(AUTO_FORMS))
        return 2
    if core.DEMO:
        raise SwitchError('The demo has made-up accounts; turn auto-switch on or off on its page.')
    if args:
        on = args[0] == 'on'
        try:
            ask_page('/api/auto', {'enabled': on})      # the page marks it as yours
        except OSError:
            with outside_the_page():                    # its loop runs only in the page, which starts it
                keep.by_you(lambda: engine.set_auto(enabled=on))    # marked as yours first, as the page does
            history.auto_turned(on)
            artifacts._map_soon()                       # the session hooks read whether it is on from the map
    from . import server
    a = engine.auto_settings()
    a['models_seen'] = server.models_seen(load(), a)            # the model weeks the page last saw, as it names them
    nowhere = (a.get('enabled') or a.get('reading_wanted')) and not job_installed() and not page_running()
    print(term.auto_line(a, yours=core.settings().get('auto_set_by_you') is True, reading=not nowhere))
    if a.get('rules') or a.get('own_lines') or a.get('ranks'):
        from . import autorules
        try:
            saved = engine.accounts()
        except SwitchError as e:            # the rules are said when the accounts can be read again
            core.log('reading the accounts for their rules: %r' % e)
            saved = []
        for line in autorules.account_lines(saved, a):     # its rule, its own lines, its rank
            print(line)
    if nowhere:
        print(NOT_RUNNING_HERE if a.get('enabled') else NOT_READ_HERE)
    return 0


def auto_rule(rule, name):
    """`switchboard auto last|out|in NAME`: one account's rule in auto-switch, through the running page (POST /api/auto)
    when it answers, else here. Then one sentence that says what happens now (term.rule_said)."""
    if core.DEMO:
        raise SwitchError("The demo has made-up accounts; set an account's rule in auto-switch on its page.")
    try:
        a = engine.find(name)
    except SwitchError:
        from . import bearers
        if bearers.find(name) is None:
            raise
        said = bearers.set_rule(name, rule)     # a one-year token or an API key: a key stays kept out
        history.note(said, h='setting', by='you')
        print(said)
        return 0
    try:
        ask_page('/api/auto', {'rule': rule, 'account': a.id})
    except OSError:
        with outside_the_page():
            engine.set_auto(rule=(a.id, rule))
        history.note(term.rule_said(names.of_account(a, engine.accounts()), rule), h='setting', by='you')
    print(term.rule_said(names.of_account(a, engine.accounts()), rule))
    return 0


# ---------- updating ----------

def install_choices():
    """The installer's choices, as this install shows them: what is there stays, what is not is not added. It never
    opens the page and never runs the first run again. The menu bar item and the link app are left out only when that
    was chosen at install (--no-menu-app, --no-link-app in <data>/app/choices): an update tries again when they could
    not be added the first time, and an install from before that file gets them, as a new one does. An install made by Homebrew's cask
    (--homebrew) stays one: its commands are Homebrew's, so none are added beside them."""
    flags = ['--no-open', '--no-setup']
    chosen = core.read_text(core.data_path(os.path.join('app', 'choices'))).split()
    if '--homebrew' in chosen:
        flags.append('--homebrew')
    if not job_installed():
        flags.append('--no-agent')
    if linkapp.installed():
        flags.append('--link-app')
    elif '--no-link-app' in chosen:                     # left out on purpose at install: an update leaves it out too
        flags.append('--no-link-app')
    if '--no-menu-app' in chosen:
        flags.append('--no-menu-app')
    if '--own-python' in chosen or python_record().get('python_source') == 'own':
        flags.append('--own-python')
    p = core.settings().get('port')
    if isinstance(p, int) and not isinstance(p, bool):
        flags += ['--port', str(p)]
    return flags


def fetch(url):
    """The installer's text from its address (curl, as the one-line install uses)."""
    try:
        r = subprocess.run(['/usr/bin/curl', '-fsSL', '--retry', '2', '--connect-timeout', '15', url],
                           capture_output=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        r = None
    if r is None or r.returncode != 0 or not r.stdout.startswith(b'#!'):
        raise SwitchError('Could not download the installer from %s. Check the connection and try again.' % url)
    return r.stdout


UPDATE_USAGE = 'switchboard update [--link-app] [--menu-app | --no-menu-app] [--port N]\n' + newer.CHECK_USAGE


def update_wants(args):
    """What `switchboard update` is asked to change, besides installing again with the same choices:
    {'link': True to add the link app, 'menu': True to add the menu bar item, False to take it out, None to keep it,
    'port': the page's new port or None}. None when the words are not ones it knows. Raises SwitchError for a port
    it can't use."""
    want = {'link': False, 'menu': None, 'port': None}
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--link-app' and not want['link']:
            want['link'] = True
        elif a in ('--menu-app', '--no-menu-app') and want['menu'] is None:
            want['menu'] = a == '--menu-app'
        elif a == '--port' and want['port'] is None and i + 1 < len(args):
            i += 1
            n = args[i]
            if not (n.isdigit() and 1024 <= int(n) <= 65535):
                raise SwitchError('--port needs a number between 1024 and 65535, like --port 7801.')
            want['port'] = int(n)
        else:
            return None
        i += 1
    return want


def update_flags(want):
    """The installer's options for an update: this install's choices, and the change asked for (update_wants)."""
    flags = install_choices()
    if want['link'] and '--link-app' not in flags:
        flags = [f for f in flags if f != '--no-link-app'] + ['--link-app']
    if want['menu'] is not None:
        flags = [f for f in flags if f != '--no-menu-app'] + ([] if want['menu'] else ['--no-menu-app'])
    if want['port'] is not None:
        at = flags.index('--port') if '--port' in flags else None
        if at is not None:
            del flags[at:at + 2]
        flags += ['--port', str(want['port'])]
    return flags


def update(args):
    """`switchboard update`: run the installer again from where it came from (<data>/app/source: its address, or the
    clone it ran from), with the same choices, which a one-line install can't otherwise be given again. With
    `--link-app` it adds the link app; with `--menu-app` the menu bar item (`--no-menu-app` takes it out, and either
    is kept for later updates); with `--port N` the page moves to port N (the installer sets it, and the page's
    background job starts it there). An install made by Homebrew is updated by Homebrew (update_with_homebrew)."""
    if '--check' in args:                       # a look only: nothing is installed (newer.py)
        as_json, wrong = newer.check_words(args)
        return newer.check_command(as_json, wrong, brew_path)
    want = update_wants(args)
    if want is None:
        print(UPDATE_USAGE)
        return 2
    src = core.read_text(core.data_path(os.path.join('app', 'source'))).strip()
    if not src:
        raise SwitchError('Switchboard does not know where it was installed from. Run the installer again the way '
                          'you first did.')
    before = newer.installed() or (core.VERSION, core.build())     # read before the installer replaces the copy
    if src.startswith('homebrew:'):
        code, ran = update_with_homebrew(want)
        return updated(before, code, ran)
    flags = update_flags(want)
    if src.startswith('https://'):
        script = fetch(src)
        print('Updating from %s' % src)
        sys.stdout.flush()
        return updated(before, subprocess.run(['/bin/sh', '-s', '--'] + flags, input=script).returncode)
    installer = os.path.join(src, 'install.sh')
    if not (os.path.isabs(src) and os.path.isfile(installer)):
        raise SwitchError('Switchboard was installed from %s, which is not there any more. Run the installer again '
                          'from where it is now.' % src)
    print('Updating from %s' % src)
    sys.stdout.flush()
    return updated(before, subprocess.call(['/bin/sh', installer] + flags, cwd=src))


def left_unfinished():
    """The installer could not download Switchboard's own Python: it was asked for (--own-python among
    <data>/app/choices) and the installer recorded that the program runs on the Python found on this Mac. Its last
    line then says how to finish."""
    chosen = core.read_text(core.data_path(os.path.join('app', 'choices'))).split()
    return '--own-python' in chosen and python_record().get('python_source') == 'found'


def updated(before, code, ran_installer=True):
    """The last line of an update: the version it had and the one it has now, read from the installed copy. Not when
    this update ran the installer and it could not finish (left_unfinished): the installer's own last line says how
    to finish, and stays the last thing read."""
    if ran_installer and left_unfinished():
        return code
    print(newer.update_line(before, newer.installed(), code))
    return code


def brew_path():
    """Homebrew's brew: on the PATH, else where its installer puts it (Apple silicon, then Intel). None without it."""
    found = shutil.which('brew')
    if found:
        return found
    return next((p for p in ('/opt/homebrew/bin/brew', '/usr/local/bin/brew') if os.access(p, os.X_OK)), None)


def update_with_homebrew(want):
    """`switchboard update` for an install made by Homebrew's cask (app/source is "homebrew:" and Homebrew's copy):
    Homebrew updates it, so there is one way in and one version, the one Homebrew lists. `brew upgrade` runs the
    installer again, which keeps this install's choices. A change asked for here (--link-app, --menu-app,
    --no-menu-app, --port N) is made after that by the installer in Homebrew's copy, which the upgrade may have
    replaced: app/source is read again. Returns (exit code, whether this ran the installer itself)."""
    brew = brew_path()
    if not brew:
        raise SwitchError('Switchboard was installed with Homebrew, and brew is not on this PATH. Update it with: '
                          'brew upgrade switchboard')
    print('Updating with Homebrew: brew upgrade switchboard')
    sys.stdout.flush()
    rc = subprocess.call([brew, 'upgrade', '--cask', 'switchboard'])
    if rc != 0 or (not want['link'] and want['menu'] is None and want['port'] is None):
        return rc, False
    src = core.read_text(core.data_path(os.path.join('app', 'source'))).strip()
    copy = src[len('homebrew:'):] if src.startswith('homebrew:') else ''
    installer = os.path.join(copy, 'install.sh')
    if not (os.path.isabs(copy) and os.path.isfile(installer)):
        raise SwitchError("Homebrew's copy of Switchboard is not there any more. Run: brew reinstall switchboard")
    return subprocess.call(['/bin/sh', installer] + update_flags(want), cwd=copy), True


# ---------- the other commands ----------

def cant_reach(e):
    """The page is not running, and this terminal can't reach the browser itself: macOS did not let it start osascript
    or open (a sandbox can stop them). The line to say so; the error itself goes in the log."""
    core.log('the browser could not be reached from this terminal: %r' % e)
    return SwitchError('Could not reach %s from here, and the page is not running. Start it with: switchboard serve'
                       % browser.app())


def open_link(url):
    """`switchboard open LINK`: the running page opens it as its owner (it checks the tab and learns); when the page
    doesn't answer, the saved owner list opens it in the owner's profile (artifacts.open_link)."""
    try:
        r = artifacts.open_link(url)
    except OSError as e:
        raise cant_reach(e)
    print(r.get('said') or 'Opened.')


def links_command(args):
    """`switchboard links [on|off]`: whether links clicked anywhere open as their owner, that is, whether Switchboard
    Links is the default browser. It is always on unless you say otherwise (the first run asks macOS once, the page's
    banner offers it until it is the default), so `off` is the way out: macOS asks which browser gets the seat back,
    and nothing offers it again. `on` asks macOS to make Switchboard Links the default. Either goes through the running
    page (POST /api/links, as its banner sends it) when it answers, else here; macOS is asked only when the seat would
    change. Alone, or when nothing was asked: where it stands, and the one thing to do."""
    from . import server
    if args not in ([], ['on'], ['off']):
        print('switchboard links [on|off]')
        return 2
    if args:
        if core.DEMO:
            raise SwitchError('The demo has made-up accounts; nothing is changed.')
        on = args[0] == 'on'
        try:
            said = ask_page('/api/links', {'on': on})   # the page asks macOS, then watches for its answer
        except OSError:
            said = server.act('/api/links', {'on': on}) or {}   # no page: the same change, here
        if said.get('asked'):
            print(term.LINKS_ASKING[on])
            return 0
    st = linkapp.status(fresh=True)
    where = linkapp.seat(look=False) if st.get('installed') and not st.get('on') else {}
    holder = where.get('holder') or {}
    said = None
    if where and holder.get('kind') != 'browser':   # an app that may send clicked links to the page: the page's words
        said = (linkapp.banner(st, where, forwards=server.passes_links(holder)) or {}).get('text')
    print(term.links_line(st, core.settings().get(server.LINKS_WANTED), holder.get('name'), said))
    return 0


def owner_command(url):
    """`switchboard owner LINK`: which account owns a claude.ai artifact, how that is known, and the commands that open
    it and act on it as that account (artifacts.owner_of). Exit 0 when the owner is known, 1 when it is not."""
    r = artifacts.owner_of(url) or {}
    print(r.get('said') or 'Not known yet. Open it once with: switchboard open %s' % url)
    return 0 if r.get('email') else 1


def owner_json(url):
    """`switchboard owner LINK --json`: the same answer as one JSON document (report.owner). Exit 0 when the owner is
    known, 1 when it is not, as `switchboard owner`."""
    from . import report
    r = artifacts.owner_of(url) or {}
    print(json.dumps(report.owner(url, r), indent=1, ensure_ascii=False))
    return 0 if r.get('email') else 1


def print_pages():
    artifacts.refresh()
    st = load()
    owner_w = max([len(x.get('owner') or '?') for x in st['artifacts']] + [1])
    for x in st['artifacts']:
        print('%-*s  %-10s %s  %s' % (owner_w, x.get('owner') or '?', x.get('src') or '-', x['url'],
                                      (x.get('title') or x.get('name') or '')[:50]))


CHECK_WAIT = 300.0              # seconds `switchboard check` waits for the page's check (a page opened per account)
CHECK_POLL = 2.0


def check():
    """The running page checks, on a thread of its own (POST /api/check answers at once; its answer comes in the page's
    state, which this reads every few seconds); without the page, it is checked here the same way. Returns 0, or 1
    when it could not."""
    from . import server
    try:
        started = time.time()
        r = ask_page('/api/check', {}, timeout=10, here=False)
        if not isinstance(r.get('results'), list):      # (a page from before answered with its results at once)
            r = _wait_for_check(started)
        if r is None:
            print('The page is still checking. Its answer shows on the page, under Settings, Links.')
            return 1
        r = dict(r, page=True)
    except OSError:
        try:
            seeing = browser.can_see_tabs()
            r = {'tabs': seeing, 'results': [] if seeing is False else artifacts.check_profiles()}
        except OSError as e:
            raise cant_reach(e)
    if r.get('error'):
        print(r['error'])
        return 1
    if r.get('tabs') is False:
        print('macOS has not allowed Switchboard to look at %s tabs.' % browser.app())
        if r.get('page'):                       # the page's own permission: its button asks macOS for it
            print('Press Let Switchboard check links on the page (Settings, Links), then run this again.')
        else:                                   # this terminal's own, asked of macOS for the terminal
            print('Allow it in System Settings > Privacy & Security > Automation, then run this again.')
        return 1
    st, names = load(), browser.profiles()
    rows = r.get('results') or []
    if not rows:
        print('Nothing to check yet: no account is here.')
        return 0
    email_w = max([len(row.get('email') or '') for row in rows] + [1])
    for row in rows:
        a = st['accounts'].get(row.get('email')) or {}
        words = server.CHECK_WORDS.get(row.get('result'), row.get('result') or '') % {'app': browser.app()}
        print('%-*s  %-24s %s' % (email_w, row.get('email') or '', (names.get(a.get('profile')) or '-')[:24], words))
    return 0


def _wait_for_check(started, wait=None):
    """The page's check that ended after `started` (its state's 'check'), or None when it did not end within `wait`
    seconds (CHECK_WAIT)."""
    until = time.monotonic() + (CHECK_WAIT if wait is None else wait)
    while True:
        c = (page_state() or {}).get('check')
        if isinstance(c, dict) and not c.get('running') and isinstance(c.get('at'), (int, float)) \
                and c['at'] >= started - 1:
            return c
        if time.monotonic() >= until:
            return None
        _sleep(CHECK_POLL)


def usage_command(args):
    from . import usage
    days, as_json = 7.0, '--json' in args
    if '--days' in args:
        i = args.index('--days')
        try:
            days = float(args[i + 1])
        except (IndexError, ValueError):
            days = 0
        if not 0 < days <= 366:
            raise SwitchError('--days needs a number of days, like --days 7.')
    until = time.time()
    summary = usage.summarize(core.projects(), until - days * 86400, until, cache_path=core.data_path('usage-cache.json'))
    page = usage.for_page(summary, days)
    if as_json:
        print(json.dumps(page, indent=1))
    else:
        usage.print_page(page, sys.stdout)


def awake_command(args):
    from . import awake
    return awake.main(args)


def statusline_command(args):
    from . import server, statusline
    what = args[0] if args else 'show'
    if what == 'show':
        now = server.statusline_status()
        print('Status line in Claude Code: %s' % ("unknown: Claude Code's settings file can't be read" if now['on'] is None
                                                  else 'on' if now['on'] else 'off, another one is set' if now['theirs']
                                                  else 'off'))
        print('Settings file: %s' % statusline.settings_path())
        latest = _latest_live(statusline.live_dir())
        print('It shows: %s' % statusline.build(latest or {}, statusline.read_state(), colour=False))
        return 0
    if what not in ('on', 'off'):
        print(__doc__.strip())
        return 2

    def change():                               # planned and made in one turn, after it is marked as yours
        plan = server.statusline_plan(what == 'on', replace='--replace' in args)
        if not plan.get('ok'):
            raise SwitchError(plan.get('message') or 'Switchboard left the settings file alone.')
        if plan.get('kept_theirs') and what == 'on':
            return plan, None
        return plan, server.statusline_turn(what == 'on', plan=plan)
    plan, r = keep.statusline_by_you(change)    # yours first: it never goes on by itself again
    if r is None:
        print('You already have a status line, so Switchboard left it as it is. To use Switchboard\'s instead, '
              'run `switchboard statusline on --replace`; `switchboard statusline off` puts yours back exactly.')
        return 1
    print(plan['message'])
    if plan.get('diff'):
        print(plan['diff'].rstrip('\n'))
    if r.get('backup'):
        print('The file as it was is saved at %s' % r['backup'])
    return 0


def notify_command(args):
    """`switchboard notify [on|off]`. Alone: whether notifications are on, whether Claude Code's "done" and "needs
    you" hooks are in its settings and can run, and a key that keeps Claude Code from running them. On or off: the
    exact change to the settings file, then what the page's Notifications switch does (server.act)."""
    from . import notify_hooks, server
    if args not in ([], ['on'], ['off']):
        print('switchboard notify [on|off]')
        return 2
    if args:
        on = args[0] == 'on'
        plan = notify_hooks.install_plan(command_line=notify_hooks.command()) if on else notify_hooks.uninstall_plan()
        r = server.act('/api/notify', {'on': on}) or {}     # refuses with one line, and then nothing was changed
        for said in (plan['message'] if plan.get('ok') and not r.get('left') else None,
                     plan['diff'].rstrip('\n') if plan.get('ok') and r.get('changed') else None,
                     ('Taken out of %s too.' % ', '.join(r['also'])) if r.get('also') else None,
                     ('The file as it was is saved at %s' % r['backup']) if r.get('backup') else None, r.get('left'),
                     notify_hooks.off_line(r['off_by']) if r.get('off_by') else None):
            if said:
                print(said)
        print('Notifications: %s' % ('on' if server.mac_facts()['notify'] else 'off'))
        return 0
    s, m = notify_hooks.status(), server.mac_facts()
    print('Notifications: %s' % ('on' if m['notify'] else 'off'))
    print('Done and needs you in Claude Code: %s' % (
        "unknown: Claude Code's settings file can't be read" if s['in'] is None else 'on' if s['works'] else
        'in, but the Python or the folder they run is gone. Run: switchboard notify on' if s['in'] else 'off'))
    if s['off_by']:
        print(notify_hooks.off_line(s['off_by']))
    off = notify_hooks.projects_off()
    if off:
        print('Off in %d project%s by their own disableAllHooks: %s' % (
            len(off), '' if len(off) == 1 else 's', ', '.join(off[:5]) + (' and %d more' % (len(off) - 5) if len(off) > 5 else '')))
    print('Settings file: %s' % notify_hooks.settings_path())
    return 0


def hooks_line(s=None):
    """The session hooks in one line, as `switchboard hooks` and doctor say them."""
    from . import session_hooks
    s = session_hooks.status() if s is None else s
    if s['in'] is None:
        return "Session hooks in Claude Code: unknown: Claude Code's settings file can't be read"
    if s['in'] and not s['works']:
        return 'Session hooks in Claude Code: in, but the Python or the folder they run is gone. Run: switchboard hooks on'
    if s['in'] and s.get('current') is False:
        return ("Session hooks in Claude Code: on, from an older version that doesn't hear claude.ai connectors. "
                'Run: switchboard hooks on')
    return 'Session hooks in Claude Code: %s' % ('on' if s['in'] else 'off')


def hooks_command(args):
    """`switchboard hooks [on|off]`: the session hooks (session_hooks.py), which tell an open Claude Code session what
    a switch means for it. Alone: whether they are in and can run. On or off: the exact change to Claude Code's
    settings file (off takes exactly them out, and puts the file back as it was when nothing else of Switchboard's is
    in it); either way it is the person's choice from then on: they never go on by themselves again."""
    from . import notify_hooks, session_hooks
    if args not in ([], ['on'], ['off']):
        print('switchboard hooks [on|off]')
        return 2
    if args:
        if core.DEMO:
            raise SwitchError('The demo has made-up accounts; nothing is changed.')
        on = args[0] == 'on'

        def change():
            r = session_hooks.turn(on)          # on: refuses with one line (PlanError), and then nothing was changed
            core.set_setting('session_hooks', on)   # off: quiet even where they could not be taken out
            return r
        try:
            r = keep.session_hooks_by_you(change)
        except session_hooks.PlanError as e:
            raise SwitchError(str(e))
        for said in (r.get('message') if r.get('changed') else None,
                     ('Taken out of %s too.' % ', '.join(r['also'])) if r.get('also') else None,
                     ('The file as it was is saved at %s' % r['backup']) if r.get('backup') else None, r.get('left'),
                     notify_hooks.off_line(r['off_by']) if r.get('off_by') else None):
            if said:
                print(said)
    s = session_hooks.status()
    print(hooks_line(s))
    if not args and s['off_by']:
        print(notify_hooks.off_line(s['off_by']))
    if s['in'] and core.settings().get('session_hooks') is False:
        print('They stay quiet: turned off in Switchboard.')
    print('Settings file: %s' % session_hooks.settings_path())
    return 0


def models_command(args):
    """`switchboard models [on|off]`: the page's "Sonnet for helpers" switch. Alone: whether it is on
    (term.models_line). On or off: the page's own change (server.act, POST /api/models): the one setting goes into
    Claude Code's settings file or comes out, the history notes it as yours, and when chats are open the page's note
    follows. Nothing turns it on by itself."""
    from . import server
    if args not in ([], ['on'], ['off']):
        print('switchboard models [on|off]')
        return 2
    note = None
    if args:
        if core.DEMO:
            raise SwitchError('The demo has made-up accounts; turn Sonnet for helpers on or off on its page.')
        note = (server.act('/api/models', {'enabled': args[0] == 'on'}) or {}).get('note')
    print(term.models_line(server.models_view()))
    if note in term.MODELS_NOTE:
        print(term.MODELS_NOTE[note])
    return 0


def statusline_works():
    """True when Switchboard's status line is on and the Python it runs exists, None when it is off, False when
    that Python is gone or Claude Code's settings file can't be read."""
    import shlex
    from . import statusline
    f = statusline._settings_file(statusline.settings_path())
    if f.get('error'):
        return False
    cur = f['obj'].get('statusLine')
    if not statusline.is_ours(cur):
        return None
    try:
        exe = shlex.split(cur['command'])[0]
    except (ValueError, IndexError):
        return False
    return os.path.isfile(exe) and os.access(exe, os.X_OK)


def statusline_theirs():
    """A status line of the person's own is in Claude Code's settings file (server.statusline_status): the page's
    switch and `switchboard statusline on --replace` stand in for it, and off puts it back."""
    from . import server
    return server.statusline_status().get('theirs') is True


def _latest_live(folder):
    """The newest numbers Claude Code handed the status line, shaped as it hands them, or None."""
    best, now = None, time.time()
    try:
        for e in os.scandir(folder):
            if e.name.endswith('.json') and e.is_file() and e.stat().st_mtime <= now + 5 \
                    and (best is None or e.stat().st_mtime > best[0]):
                best = (e.stat().st_mtime, e.path)
    except OSError:
        return None
    rec = core.read_json(best[1]) if best else {}
    return {'rate_limits': rec['rate_limits']} if isinstance(rec.get('rate_limits'), dict) else None


def _short_path(p):
    h, p = os.path.normpath(core.home()), os.path.normpath(p) if p else p
    return '~' + p[len(h):] if p and (p == h or p.startswith(h + os.sep)) else p


def python_record():
    """What the installer recorded about the Python Switchboard runs on (runtime/python.json), or {}:
    {'python', 'python_version', 'python_source' ('own', 'found'), 'page_python' (what the page's job runs: the menu bar
    app's own Python when it is installed, else the same Python)}."""
    return core.read_json(core.data_path(os.path.join('runtime', 'python.json')))


def keys_command(args):
    """switchboard keys list | add NAME | remove NAME [--yes]. A key is never an argument and never printed."""
    from . import lanes
    what = args[0] if args else 'list'
    if what == 'list' and len(args) <= 1:
        rows = lanes.lanes()
        if not rows:
            print('No keys yet. Add one with: switchboard keys add NAME')
        for i, x in enumerate(rows, 1):
            print('%d. %s  (%s, ends in %s)' % (i, x['name'], x.get('kind') or 'anthropic', x.get('last4') or '?'))
        return 0
    if what == 'add' and len(args) == 2:
        if sys.stdin.isatty():
            import getpass
            key = getpass.getpass('API key for %s (not shown): ' % args[1])
        else:                                   # piped in, e.g. from a password manager: one line, never echoed
            key = sys.stdin.readline()
        r = lanes.add(args[1], key.strip())
        key = None
        print('Added %s (ends in %s) to your Keychain.' % (r['name'], r['last4']))
        return 0
    if what == 'remove' and len(args) in (2, 3) and args[2:] in ([], ['--yes']):
        if '--yes' not in args:
            if not sys.stdin.isatty():
                raise SwitchError('Add --yes to remove a key without being asked.')
            if input('Remove the key %s from your Keychain? [y/N] ' % args[1]).strip().lower() not in ('y', 'yes'):
                print('Nothing removed.')
                return 1
        lanes.remove(args[1])
        print('Removed %s from your Keychain. The key itself stays valid with its provider.' % args[1])
        return 0
    print('switchboard keys list | add NAME | remove NAME [--yes]')
    return 2


def claude_on_keys(args):
    """Start Claude Code against the keys gateway, for this terminal only (gateway.claude_command)."""
    from . import gateway, lanes
    if not lanes.lanes():
        raise SwitchError('No keys yet. Add one first: switchboard keys add NAME')
    if not lanes.answers():
        if lanes.hello() == 'other':
            raise SwitchError("Another program answers on 127.0.0.1:%d, not Switchboard's keys gateway, so Claude Code "
                              "was not started." % gateway.port())
        raise SwitchError('The keys gateway is off. Turn it on in Switchboard (Settings, then Keys), then run this again.')
    exe = connect.claude_bin()
    if not exe:
        raise SwitchError('Claude Code is not installed on this Mac.')
    argv, env = gateway.claude_command(args, os.environ)
    sys.stderr.write('Claude Code on your keys, through the gateway on this Mac. Plain `claude` stays on your accounts.\n')
    sys.stderr.flush()
    os.execve(exe, [exe] + argv[1:], env)


def tidy(everything=False):
    """For the uninstaller: a cut-off sign-in's leftovers, and everything Switchboard changed in Claude Code put
    back (the status line, the notification hooks, the session hooks, the Models setting, house rules, the code map,
    claude-mem's settings). With everything: the keys too (their Keychain items). Prints one line per change, and one for each
    thing it could not put back; a step that can't be done never stops the ones after it. A file counts as left only
    when Switchboard's own record says it changed that file (or the record can't be read): one it never changed that
    can't be read, isn't JSON, or is a folder or a link holds nothing of Switchboard's. Returns 1 when anything is
    left: the uninstaller then keeps the program and its records, so this can run again."""
    from . import claudecode, lanes, notify_hooks, server, session_hooks, statusline
    left = []
    here = statusline.settings_path()                   # noted before the steps below drop their records
    copies, seen = [], set()
    for p in [here] + sorted(statusline._records()) + sorted(notify_hooks._records()) + \
            sorted(session_hooks._records()) + [os.path.realpath(here)]:
        b = statusline.backup_path(p)                   # one name per file: this process's own first
        real = os.path.join(os.path.realpath(os.path.dirname(b)), os.path.basename(b))
        if real not in seen:
            seen.add(real)
            copies.append(b)

    def kept(name, why, they=False):
        left.append(name)
        print('%s: left as %s (%s)' % (name, 'they are' if they else 'it is', why))

    try:
        # cleared, never taken up: nothing after this would keep its login. One whose login the Keychain will not give
        # up now is left with its folder, and named: the folder is what finds that login again at the next run.
        if connect.sweep(take_up=False):
            kept('A sign-in that never finished', "Couldn't take its login out of the Keychain yet: it may be locked, "
                 "or macOS may be waiting for your answer")
    except Exception as e:  # noqa: BLE001 - every step runs, and what one could not do is named
        kept('A sign-in that never finished', claudecode.plain_error(e))
    try:
        put_back = False
        if server.statusline_status().get('on'):
            server.statusline_turn(False)
            put_back = True
        else:
            f = statusline._settings_file(statusline.settings_path())
            if f.get('error') and statusline.record_names(statusline.record_path(), f['target']):
                raise SwitchError(f['error'])           # it went in there, and the file can't be read now
        if statusline.off_elsewhere(statusline.settings_path()) or put_back:   # every file its record names
            print('Status line: put back as it was.')
            try:
                if core.settings().get('statusline_by_itself'):     # an install again puts it back at its first run
                    core.set_setting('statusline_by_itself', None)
            except Exception as e:  # noqa: BLE001 - only the next install's first run misses it; nothing is left
                core.log('statusline_by_itself was not forgotten: %r' % e)
    except Exception as e:  # noqa: BLE001
        kept('Status line', claudecode.plain_error(e))
    try:
        r = notify_hooks.turn(False)
        if r.get('changed'):
            print('Notification hooks: taken out.')
            try:
                if core.settings().get('notify_by_itself'):     # an install again puts them back at its first run
                    core.set_setting('notify_by_itself', None)
            except Exception as e:  # noqa: BLE001 - only the next install's first run misses it; nothing is left
                core.log('notify_by_itself was not forgotten: %r' % e)
        if r.get('left'):
            kept('Notification hooks', r['left'], they=True)
    except Exception as e:  # noqa: BLE001
        kept('Notification hooks', claudecode.plain_error(e), they=True)
    try:
        r = session_hooks.turn(False)
        if r.get('changed'):
            print('Session hooks: taken out%s.' % (' (also of %s)' % ', '.join(_short_path(p) for p in r['also'])
                                                   if r.get('also') else ''))
            try:
                if core.settings().get('session_hooks_by_itself'):  # an install again puts them back at its first run
                    core.set_setting('session_hooks_by_itself', None)
            except Exception as e:  # noqa: BLE001 - only the next install's first run misses it; nothing is left
                core.log('session_hooks_by_itself was not forgotten: %r' % e)
        if r.get('left'):
            kept('Session hooks', r['left'], they=True)
    except Exception as e:  # noqa: BLE001
        kept('Session hooks', claudecode.plain_error(e), they=True)
    try:
        if server.models_turn(False).get('changed'):
            print('Models setting: taken out.')
    except Exception as e:  # noqa: BLE001
        if server.models_recorded():                # else the file holds nothing of Switchboard's: nothing to say
            kept('Models setting', claudecode.plain_error(e))
    for said in claudecode.undo_all(left):
        print(said)
    if everything:                              # every account, with its saved login in your Keychain
        try:
            gone = engine.held(engine.rows())
        except engine.Refused as e:
            gone = []
            if e.detail != 'todo':              # an engine not built in this copy keeps no account
                kept('Accounts', str(e), they=True)
        except Exception as e:  # noqa: BLE001
            gone = []
            kept('Accounts', claudecode.plain_error(e), they=True)
        for r in gone:
            try:
                engine.remove(r.get('id') or r['email'], everything=True)
                print('Account %s: removed, with its saved login.' % r['email'])
            except Exception as e:  # noqa: BLE001 - an account left behind must be named, whatever stopped it
                left.append('Account %s' % r['email'])
                print('Account %s: still kept (%s). Remove it with: switchboard remove %s --yes'
                      % (r['email'], e if isinstance(e, SwitchError) else e.__class__.__name__, r['email']))
        if not any(x.startswith('Account') for x in left):   # each one went: then every login of theirs still kept
            try:                                            # (earlier ones, recovery copies) leaves the Keychain too
                for said in engine.remove_all():
                    print(said)
            except engine.Refused as e:
                if e.detail != 'todo':
                    kept('Saved logins', str(e), they=True)
            except Exception as e:  # noqa: BLE001 - what is left must be named, whatever stopped it
                kept('Saved logins', claudecode.plain_error(e), they=True)
        try:
            keys = lanes.lanes()
        except Exception as e:  # noqa: BLE001
            keys = []
            kept('Keys', claudecode.plain_error(e))
        for x in keys:
            try:
                lanes.remove(x['name'])
                print('Key %s: removed from your Keychain.' % x['name'])
            except Exception as e:  # noqa: BLE001 - a key left behind must be named, whatever stopped it
                left.append('Key %s' % x['name'])
                print('Key %s: still in your Keychain (%s). Remove it with: switchboard keys remove %s --yes'
                      % (x['name'], e.__class__.__name__ if not isinstance(e, SwitchError) else e, x['name']))
    if not left:            # everything is back: the copy of Claude Code's settings from before the last change goes
        for b in copies:    # (the status line, the hooks and the Models setting keep one each file); kept otherwise
            try:
                if os.path.isfile(b) and not os.path.islink(b):
                    os.unlink(b)
                    print('Removed %s, the copy Switchboard kept from before its last change.' % _short_path(b))
            except OSError as e:
                kept('The copy %s' % _short_path(b), claudecode.plain_error(e))
    return 1 if left else 0


def doctor():
    """Plain lines: what is installed, the Python it runs on, the account engine's own health check, the heartbeat of
    the loop that reads the accounts, whether automatic switching is on, another program that switches too, and what
    to do about anything that is wrong. `--` is something not set up (yet); `NO` needs a look."""
    ok = True

    def line(good, text, fix=''):
        nonlocal ok
        ok = ok and good is not False
        print(' %s  %s%s' % ('ok' if good else '--' if good is None else 'NO', text, '\n       ' + fix if fix and not good else ''))

    print('Switchboard %s, in %s' % (core.version_text(), _short_path(core.data_dir())))
    where = {'own': ", Switchboard's own Python", 'found': ', the Python found on this Mac'}.get(
        python_record().get('python_source'), '')
    line(sys.version_info >= (3, 9), 'Program on Python %d.%d (%s)%s' % (sys.version_info[:2] + (sys.executable, where)),
         'Switchboard needs Python 3.9 or newer.')
    here = os.path.dirname(os.path.abspath(__file__))
    line(os.path.isfile(os.path.join(here, 'web', 'index.html')), 'Program files in %s' % _short_path(os.path.dirname(here)),
         'Run the installer again.')
    on_path = [d for d in os.environ.get('PATH', '').split(os.pathsep) if d and
               os.path.realpath(os.path.join(d, 'switchboard')) == os.path.realpath(core.data_path('bin/switchboard'))]
    line(bool(on_path) or None, 'The switchboard command%s' % (' (%s)' % _short_path(on_path[0]) if on_path else ''),
         'To type it, put ~/.local/bin on your PATH with this line, then open a new terminal:  %s' % path_line())
    _doctor_short(line)

    kept = _doctor_engine(line)
    _doctor_auto(line, kept)
    _doctor_watch(line)
    _doctor_settings(line)
    _doctor_heal(line, kept)
    other = engine.foreign_switcher()
    line(False if other else True, 'No other program switches Claude Code\'s login' if not other else
         'Another program switches Claude Code\'s login too (%s)' % other, engine.GUARD % other if other else '')

    line(bool(connect.claude_bin()) or None, 'Claude Code', 'Install Claude Code: https://claude.com/claude-code')
    _doctor_providers(line)
    _doctor_trust(line)
    said = auth_said()
    if said:
        line(None, said)
    line(browser.present() or None, '%s, %d profiles' % (browser.app(), len(browser.profiles())),
         'Switchboard opens links in Chrome profiles. Install Google Chrome and open it once.')
    links = linkapp.status(fresh=True)
    older = linkapp.app_path() == linkapp.older_path()
    line(False if links['installed'] and older else links['installed'] or None,
         'Link app (%s%s)' % (_short_path(linkapp.app_path()), ', its name before 0.2.0' if older else ''),
         "Run the installer again (it needs Apple's command line tools): it becomes Switchboard Links.app." if older
         else "Add it with: switchboard update --link-app (it needs Apple's command line tools).")
    line(links['on'] or None, 'Links find their account (Switchboard Links is the default browser)',
         ('Turn it on with: switchboard links on (macOS asks).' if links['installed'] else 'It needs the link app, above.') +
         ' Until then, use `switchboard open LINK`.')
    _doctor_menubar(line)
    _doctor_new_parts(line)
    plist = os.path.join(core.home(), 'Library', 'LaunchAgents', AGENT + '.plist')
    job = None                          # launchd is only asked about a job this install added
    if os.path.exists(plist):
        job = subprocess.run(['launchctl', 'print', 'gui/%d/%s' % (os.getuid(), AGENT)], capture_output=True).returncode == 0
    line(job, 'Background job (starts the page at login)', 'Run the installer again to add it.')
    _doctor_shell(line, plist)
    page = page_status()
    line(page != 'down' or (None if not job else False), 'Page at http://localhost:%d%s' % (
        port(), ' (busy just now: it answers again in a moment)' if page == 'busy' else ''),
        'Start it with: switchboard serve')
    works = statusline_works()
    yours = works is None and statusline_theirs()       # then plain `statusline on` leaves yours and exits 1
    line(works, 'Status line in Claude Code' + (': your own' if yours else ''),
         "Optional. To use Switchboard's instead, turn it on from the page, or run: switchboard statusline on "
         '--replace' if yours else
         'Optional. Turn it on from the page, or run: switchboard statusline on' if works is None else
         "Claude Code's settings file can't be read, or the Python the line runs is gone. "
         'Check the file, then run `switchboard statusline on` again.')
    from . import notify_hooks
    hooks = notify_hooks.status()
    line(None if hooks['in'] is False else bool(hooks['works'] and not hooks['off_by']),
         'Done and needs you in Claude Code',
         'Optional. Turn Notifications on from the page, or run: switchboard notify on' if hooks['in'] is False else
         notify_hooks.off_line(hooks['off_by']) if hooks['works'] else
         "Claude Code's settings file can't be read." if hooks['in'] is None else
         'The Python or the folder they run is gone. Run: switchboard notify on')
    from . import session_hooks
    sh = session_hooks.status()
    quiet = core.settings().get('session_hooks') is False
    line(None if sh['in'] is False or (sh['in'] and quiet) else bool(sh['works'] and not sh['off_by']),
         hooks_line(sh) + (' (quiet: turned off in Switchboard)' if sh['in'] and quiet else ''),
         'Optional. They tell an open Claude Code session what a switch means for it. Run: switchboard hooks on'
         if sh['in'] is False else
         'Take them out with: switchboard hooks off' if quiet else
         notify_hooks.off_line(sh['off_by']) if sh['works'] else
         "Claude Code's settings file can't be read." if sh['in'] is None else
         'The Python or the folder they run is gone. Run: switchboard hooks on')
    if browser.present():
        _doctor_tabs(line, page)
    if ok and kept == 0:                # nothing set up yet is not "All good": the first screen's own next step
        print('\n' + not_set_up())
        return 0
    print('\nAll good.' if ok else '\nSomething above needs a look.')
    return 0 if ok else 1


def doctor_command(args):
    """`switchboard doctor` only looks: a damaged settings file or list of accounts is answered from its copy and
    nothing is written (core.no_restore, vault.no_restore). `switchboard doctor --fix` runs every repair the self-check
    can make (selfcheck.py) and says what each did, one plain line each."""
    if args == ['--fix']:
        return doctor_fix()
    if args:
        raise SwitchError('Run: switchboard doctor, or switchboard doctor --fix')
    from .accounts import vault
    with core.no_restore(), vault.no_restore():
        return doctor()


def doctor_fix():
    """`switchboard doctor --fix`: the page's ten-minute self-check, run now, each check in one line: ok, fixed, or NO
    with the one thing to do. 0 when nothing is left to look at."""
    if core.DEMO:
        raise SwitchError('The demo has made-up accounts, so there is nothing to repair.')
    from . import selfcheck
    print('Switchboard %s, in %s' % (core.version_text(), _short_path(core.data_dir())))
    code, lines = selfcheck.fix_lines()
    for said in lines:
        print(said)
    print('\nAll good.' if code == 0 else '\nSomething above needs a look.')
    return code


def _doctor_heal(line, kept=None):
    """What the self-check finds, looking only (selfcheck.doctor_lines): a list or settings file restored from a copy
    in the last day, an entry of Switchboard's missing from Claude Code's settings, a damaged file, a switch cut off;
    then the page busy for BUSY_FOR or more. When automatic switching last decided is _doctor_watch's line alone."""
    from . import selfcheck
    try:
        said = selfcheck.doctor_lines()
    except Exception as e:  # noqa: BLE001 - a line for doctor: it never stops doctor
        core.log('doctor: the self-check: %r' % e)
        said = []
    for good, text, fix in said:
        line(good, text, fix or '')
    restart = 'launchctl kickstart -k gui/%d/%s' % (os.getuid(), AGENT)
    busy = selfcheck.busy_words(page_state(timeout=5))
    if busy:
        line(False, busy, 'If it stays like this, restart the page: %s' % restart)


def not_set_up():
    """The first screen's last line when no account is kept (_closing): Claude Code to install, to sign in to, or the
    account it is on to add, with switchboard setup."""
    try:
        signed_in = bool(connect.claude_bin()) and bool(identity())
    except Exception:  # noqa: BLE001 - doctor only looks; not known reads as not signed in
        signed_in = False
    return _closing({}, {'accounts': [{'unmanaged': True}] if signed_in else [], 'head': {'email': None}})[0]


SHELL_ONLY = {'CLAUDE_CONFIG_DIR': 'the two can work on different logins',       # (what can then differ)
              'SWITCHBOARD_HOME': 'the two can keep different accounts and settings',
              'SWITCHBOARD_PORT': 'this command can be talking to another page'}
CLOUD = ('CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY', 'CLAUDE_CODE_USE_MANTLE',
         'CLAUDE_CODE_USE_ANTHROPIC_AWS', 'CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD')  # on a cloud provider, not a login
HELPER = 'apiKeyHelper'                                           # a key of Claude Code's settings, not a variable
# What puts Claude Code on something other than an account's login, in the order Claude Code itself picks one when
# several are set (code.claude.com/docs/en/authentication, "Authentication precedence", and its env-vars page for the
# providers' names; read 2026-11-19): a cloud provider, the bearer token, the API key, the key helper, the long-lived
# token, a named profile. The login a switch changes comes below all of these. Claude Code puts a few more things
# above a login that are neither a variable in the shell nor a key of the person's own settings (a signed-in gateway
# session, federation, an API key a Console sign-in saved, settings an organization manages): of those nothing is
# said here.
PICKED_FIRST = CLOUD + ('ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY', HELPER, 'CLAUDE_CODE_OAUTH_TOKEN',
                        'CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR', 'CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR',
                        'ANTHROPIC_PROFILE')
OFF = ('0', 'false', 'no', 'off')                                 # Claude Code's own words for a switch that is off


def _doctor_shell(line, plist):
    """What this shell sets for Claude Code or Switchboard that the page's background job (its plist) does not have:
    a command here and the page can then work on different logins. Reads only."""
    import plistlib
    try:
        with open(plist, 'rb') as f:
            job = plistlib.load(f).get('EnvironmentVariables') or {}
    except Exception:  # noqa: BLE001 - no job, or one that can't be read: nothing to compare with
        return
    job = job if isinstance(job, dict) else {}
    for name in SHELL_ONLY:
        if os.environ.get(name) and os.environ.get(name) != job.get(name):
            line(None, "%s is set in this shell but %s in the page's background job, so %s"
                 % (name, 'has another value' if job.get(name) else 'not', SHELL_ONLY[name]))


def _auths():
    """Everything set that puts Claude Code on an API key, a token, a cloud provider or a profile instead of a login:
    (the name, where it is set, the value that applies), in Claude Code's own order (PICKED_FIRST). A variable can be
    set in this shell and in the env of Claude Code's settings; the settings' value is the one that applies ("Claude
    Code writes each env entry into the process environment, replacing the value inherited from the shell", its
    env-vars page), so a provider turned off there is off, and of two keys the settings' one is the key. Reads only."""
    doc = core.read_json(os.path.join(core.claude_dir(), 'settings.json'))
    env = doc.get('env') if isinstance(doc.get('env'), dict) else {}
    out = []
    for name in PICKED_FIRST:
        if name == HELPER:
            if doc.get(HELPER):
                out.append((name, "Claude Code's settings", ''))
            continue

        def on(value, flag=name in CLOUD):
            text = '' if value is None else str(value).strip()
            return bool(text) and not (flag and text.lower() in OFF)
        shell, kept = os.environ.get(name), env.get(name)
        applies = shell if kept is None else kept                  # what the settings say is what applies
        if not on(applies):
            continue
        places = [where for where, value in (('this shell', shell), ("Claude Code's settings", kept)) if on(value)]
        out.append((name, ' and '.join(places), str(applies).strip()))
    return out


def auth_override():
    """(the name, where it is set) of the first of _auths, or None."""
    found = _auths()
    return found[0][:2] if found else None


def key_answer(value):
    """What the person answered when Claude Code asked whether to use this ANTHROPIC_API_KEY in chats: 'approved',
    'declined', or None (not asked yet, or not known). Claude Code asks once and keeps the answer in its .claude.json
    (customApiKeyResponses: the key's last 20 characters; Claude Code 2.1.309, 2026-11). Without a terminal (-p) it
    uses the key whatever the answer (code.claude.com/docs/en/authentication, "Authentication precedence")."""
    said = core.read_json(core.claude_json()).get('customApiKeyResponses')
    said = said if isinstance(said, dict) else {}
    tail = value[-20:]
    found = [word for answer, word in (('rejected', 'declined'), ('approved', 'approved'))
             if tail and isinstance(said.get(answer), list) and tail in said[answer]]
    return found[0] if len(found) == 1 else None            # (on both lists: not known which one counts)


def auth_line(name, where, answer='approved'):
    """The one line about it. An ANTHROPIC_API_KEY is in use in chats only once the person approved it there."""
    if name == 'ANTHROPIC_API_KEY' and answer == 'declined':
        return ("Claude Code has ANTHROPIC_API_KEY (set in %s), which you declined for chats, so chats do not use it. "
                "Runs started with -p do, so a switch changes nothing for those." % where)
    if name == 'ANTHROPIC_API_KEY' and answer is None:
        return ("Claude Code has ANTHROPIC_API_KEY (set in %s): runs started with -p use it, and chats use it only if "
                "you approved it when Claude Code asked. Where it is used, a switch changes nothing." % where)
    return ("Claude Code uses %s (set in %s) instead of an account's login, so a switch changes nothing for it."
            % (name, where))


def auth_said(after_switch=False):
    """The line for doctor, or (after_switch) for the end of a switch, about the first of _auths that Claude Code
    uses. An API key is that for chats only once the person approved it. A key that is declined, or whose answer is
    not known, with something set further down: chats use one of the two and runs started with -p use the key, so the
    line names both and says only that. With nothing further down the line is about the key, and after a switch
    there is none when the key was declined: the switch did move the chats."""
    key = None
    for name, where, value in _auths():
        if name != 'ANTHROPIC_API_KEY':
            if key is None:
                return auth_line(name, where)
            return ("Claude Code has %s (set in %s) and %s (set in %s): it uses one of them instead of an account's "
                    "login, so a switch changes nothing." % (key[0], key[1], name, where))
        answer = key_answer(value)
        if answer == 'approved':
            return auth_line(name, where)
        key = (name, where, answer)
    if key is None or (after_switch and key[2] == 'declined'):
        return None
    return auth_line(*key)


def path_line():
    """The installer's own line that puts ~/.local/bin, where it links the command, last on the PATH of this shell."""
    shell = os.path.basename(os.environ.get('SHELL') or '/bin/zsh')
    if shell == 'fish':
        return 'fish_add_path --append ~/.local/bin'
    return 'echo \'export PATH="$PATH:$HOME/.local/bin"\' >> %s' % ('~/.bash_profile' if shell == 'bash' else '~/.zshrc')


TAB_FIX = {'denied': 'Allow it in System Settings > Privacy & Security > Automation.',
           'would_ask': "On the page: Settings, Links, Let Switchboard check links (macOS asks once).",
           'not_running': 'Open the browser, then look again.', 'slow': 'The browser was slow; it is looked at again.',
           'unknown': 'The page says it under Settings, Links.'}


def _doctor_short(line):
    """The short command (core.SHORT_NAME): ours (a link to this install's command, or to Homebrew's switchboard beside
    it), another program's, or not there. Neither of the last two is a problem: the command is switchboard."""
    name = core.SHORT_NAME
    found = shutil.which(name)
    if not found:
        line(None, 'No short command %s' % name, 'switchboard update adds it, when nothing else on this Mac has the name.')
        return
    ours = {os.path.realpath(core.data_path('bin/switchboard'))}
    full = shutil.which('switchboard')
    if full:
        ours.add(os.path.realpath(full))
    if os.path.realpath(found) in ours:
        line(True, 'The short command %s (%s)' % (name, _short_path(os.path.dirname(found))))
    else:
        line(True, '%s already means something else on this Mac (%s), so the command is switchboard' % (name, _short_path(found)))


def _doctor_tabs(line, page):
    """May the page read the browser's tabs: the running page's own answer from macOS (it alone asks, and only from
    its button). A terminal never asks the browser itself here: macOS would ask about this terminal, unasked."""
    tabs = ((page_state() or {}).get('browser') or {}).get('tabs') if page == 'running' else None
    app = browser.app()
    if not isinstance(tabs, dict) or not tabs.get('state'):
        line(None, 'Allowed to read %s tab titles: not known while the page is %s' % (
            app, 'busy' if page == 'busy' else 'not running'), 'Start the page, then look again.')
        return
    state = tabs['state']
    line(True if state == 'ok' else False if state in ('denied', 'would_ask') else None,
         'Allowed to read %s tab titles%s' % (app, '' if state == 'ok' else ': ' + str(tabs.get('text') or state)),
         TAB_FIX.get(state, TAB_FIX['unknown']))


def _doctor_engine(line):
    """The account engine (part of the program): how many accounts it keeps, and its own health check (api.health):
    the Keychain, Claude Code's conventions, a cut-off switch, the store, accounts a terminal is using."""
    rec = python_record()
    if rec.get('page_python') and rec.get('page_python') != rec.get('python'):
        line(True, 'The page runs on %s' % _short_path(rec['page_python']))
    try:
        rows = engine.rows()
        n = len(engine.held(rows))
        line(True, 'Accounts: %d kept' % n)
        h = engine.health()
    except engine.Refused as e:
        if e.detail == 'todo':                  # a copy whose engine is not built (a development skeleton)
            line(False, 'Account engine: not ready in this copy', 'Install Switchboard again from a released copy.')
        else:
            line(False, 'The account engine answers', '%s Run the installer again.' % e)
        return
    except SwitchError as e:
        line(False, 'The account engine answers', '%s Run the installer again.' % e)
        return
    for good, text in engine.health_items(h):
        line(good, text, 'Open the page to see what it needs.' if good is False else '')
    from . import leftovers
    said = leftovers.doctor_line()              # logins kept aside; it never raises
    if said:
        line(None, said)
    _doctor_signins(line, rows)
    return n


def _doctor_settings(line):
    """Each key in the settings file that nothing reads (a typing mistake, or a key from an older version), with the
    nearest real key: something to look at, never a problem."""
    from . import config
    try:
        stray = config.not_read_lines()
    except Exception as e:  # noqa: BLE001 - a line for doctor: it never stops doctor
        core.log('doctor: reading the settings file: %r' % e)
        return
    for said in stray:
        line(None, said)


def _doctor_trust(line):
    """What secure connections trust (trust.doctor_line): NO when a file named in SSL_CERT_FILE or
    NODE_EXTRA_CA_CERTS did not load."""
    from . import trust
    try:
        good, text, fix = trust.doctor_line()
    except Exception as e:  # noqa: BLE001 - a line for doctor: it never stops doctor
        core.log('doctor: what secure connections trust: %r' % e)
        return
    line(good, text, fix)


def _doctor_signins(line, rows, now=None):
    """When sign-ins end, in the page's words (term.signin_line, the same for the account in use): each account whose
    sign-in ends within the week is ok, with why once; each whose sign-in has ended needs a look, with how to sign in
    again (Sign in to all). A login refused before its date, or a date not known or far off, says nothing here."""
    now = time.time() if now is None else now
    why = False
    for r in engine.held(rows):
        need = engine.needs_sign_in(r)
        said = term.signin_line({'signin_ends_at': r.get('refresh_expires_at'), 'needs_login': need,
                                 'active': r.get('active')}, now)
        if not said:
            continue
        if need:
            line(False, '%s: %s' % (r.get('email'), said),
                 'Press Sign in to all on the page, or run switchboard signin.')
            continue
        line(True, '%s: %s' % (r.get('email'), said))
        if not why:
            print('       ' + term.SIGNIN_WHY)
            why = True


def _doctor_auto(line, kept=None):
    """Reading the accounts, then automatic switching. One loop does both, inside the page: it reads while an account
    is kept (`kept`: how many, None when the engine did not say), auto-switch on or off, and switches only while
    auto-switch is on (engine.loop_wanted). This only looks at its heartbeat (api.auto_status)."""
    try:
        on = bool(engine.auto_settings().get('enabled'))
    except SwitchError:
        on = False
    fresh = False
    if not on and not kept:
        line(None, 'Reading the accounts: %s' % ('no account kept yet' if kept == 0 else
                                                 'not known while the account engine does not answer'))
    else:
        pid = engine.HOST.recorded_pid()            # the page runs it; this command only looks
        try:
            s = api.auto_status() or {}
        except (api.AccountsError, NotImplementedError):
            s = {}
        stuck = s.get('stuck_for_s') if isinstance(s.get('stuck_for_s'), (int, float)) else None
        stage = s.get('stage') if isinstance(s.get('stage'), str) else None
        fresh = bool(pid) and stuck is not None and stuck <= engine.AutoHost.STALE
        running = page_running()
        where = ', %s' % engine.stage_words(stage) if stage else ''
        line(fresh or (False if running else None),
             'Reading the accounts: %s' % (
                 'last heartbeat %d s ago%s (pid %d)' % (stuck, where, pid) if fresh else
                 'running but no heartbeat for %d min%s' % (stuck // 60, ' (%s)' % engine.stage_words(stage) if stage
                                                            else '') if pid and stuck is not None else
                 'running, no heartbeat yet' if pid else 'not running'),
             'It runs inside the page, which restarts it when it is stuck. Open the page to see why.' if running else
             'It runs inside the page: start it with `switchboard serve`.')
    if not on:
        line(None, 'Automatic switching: off', 'Turn it on: switchboard auto on')
        return
    line(True if fresh else None, 'Automatic switching: on')     # it switches only while the reading above runs


def _doctor_watch(line):
    """Only when something is off: auto-switch on and running but deciding nothing for minutes past the pass it planned
    (watch.decided_late), a page that restarted more than three times in ten minutes, and a page its own watchdog
    restarted within the hour. Reads files only."""
    from . import watch
    try:
        on = bool(engine.auto_settings().get('enabled'))
        late = watch.decided_late(api.auto_status() or {}, on)
    except (SwitchError, api.AccountsError, NotImplementedError):
        late = None
    if late is not None:                # a fresh page ends that loop and starts another (engine.AutoHost._end_stale)
        todo = restart_page_words()
        line(False, watch.decided_words(late).rstrip('.'), 'If it stays like this, ' + todo[0].lower() + todo[1:])
    said = watch.restart_words()
    if said:
        line(False, said.rstrip('.'), 'Why is in its log: %s' % _short_path(core.data_path('agent.log')))
    fired = watch.watchdog_fired()
    if fired:
        line(False if fired >= watch.FIRES_PER_HOUR else None,
             "The page's own watchdog restarted it %d time%s in the last hour" % (fired, '' if fired == 1 else 's'),
             'It restarts a page that hangs. Why is in its log: %s' % _short_path(core.data_path('agent.log')))


def watch_line(view):
    """Under the terminal's list, only when something is off, in the page's words (server.watch_words): when
    auto-switch last decided, and a page that keeps restarting. None otherwise."""
    auto = view.get('auto') if isinstance(view, dict) and isinstance(view.get('auto'), dict) else {}
    said = []
    if auto.get('decided') and auto.get('enabled') and auto.get('on') and not auto.get('problem'):
        said.append(auto['decided'])
    if auto.get('restarts'):
        said.append(auto['restarts'])
    return ' '.join(said) or None


def _doctor_menubar(line):
    """The menu bar: what it runs on, its job (launchd is asked only about a job this install added), whether it runs,
    and its app. Reads only: it starts nothing and makes nothing."""
    from . import menubar, menubar_wrap
    hop = menubar.launcher()
    if not (os.path.isfile(hop) and os.access(hop, os.X_OK)):
        line(None, 'Menu bar: not installed', 'Add it with: switchboard update --menu-app')
        return
    libs = menubar.libraries()
    line(bool(libs), 'Menu bar on Python %s, PyObjC %s (%s)' % (libs['python'], libs['pyobjc'], _short_path(hop))
         if libs else 'Menu bar (%s)' % _short_path(hop),
         "What it runs on doesn't load on the menu bar's Python. Run: switchboard update")
    plist = menubar.agent_plist(menubar.AGENT)
    job = None
    if os.path.exists(plist):
        job = subprocess.run(['launchctl', 'print', 'gui/%d/%s' % (os.getuid(), menubar.AGENT)],
                             capture_output=True).returncode == 0
    line(job, 'Menu bar job (starts it at login)', 'Run the installer again to add it.')
    pid = menubar.running_pid()
    line(True if pid is not None else None, 'Menu bar running%s' % (' (pid %d)' % pid if pid else ''),
         'Start it with: switchboard menubar. Its log: %s' % _short_path(core.data_path('menubar.log')))
    app = menubar_wrap.present()
    if libs and libs['framework']:
        line(None, 'Menu bar app (%s)' % _short_path(app or menubar_wrap.path()), menubar.FRAMEWORK)
    elif app and menubar_wrap.in_applications(app):
        line(True, 'Menu bar app (%s)' % _short_path(app))
    elif app:
        line(None, 'Menu bar app (%s), not in Applications' % _short_path(app),
             'Put it there with: switchboard menubar --applications')
    else:
        line(None, 'Menu bar app: not made yet', 'Put it in Applications with: switchboard menubar --applications')


def _doctor_providers(line):
    """One line per provider, from the board (providers/view.py, built here from files only: nothing starts and no
    app is asked): an added one with its version and its state; one on this Mac and not added, with the line that
    adds it; one not here. An added one that is signed out, gone from this Mac, or that Switchboard could not read
    needs a look. A board that cannot be built is one line, and doctor goes on."""
    from .providers import words as provider_words
    try:
        from . import providers
        from .providers import view as provider_view
        board = provider_view.view()
        now = board.get('now') or time.time()
        shown = dict((p.get('id'), p) for p in provider_view._listed(board, 'providers'))
        offered = set(a.get('id') for a in provider_view._listed(board, 'add'))
        for pid in providers.ids():
            cls = providers.adapter_class(pid)
            name = getattr(cls, 'name', '') or pid
            p = shown.get(pid)
            if p is None:
                if pid in offered:
                    line(None, provider_words.DOCTOR_NOT_ADDED.format(name=name),
                         provider_words.DOCTOR_ADD.format(pid=pid))
                else:
                    line(None, provider_words.DOCTOR_NOT_HERE.format(name=name))
            elif p.get('error'):
                line(False, provider_words.DOCTOR_NOT_READ.format(name=name, error=p['error']),
                     provider_words.DOCTOR_REINSTALL)
            else:
                said = provider_words.DOCTOR_LINE.format(name=name, version=' %s' % p['version'] if p.get('version')
                                                         else '', words=provider_view.summary(p, now))
                if p.get('account_list') and p.get('state') in ('signed_out', 'not_installed'):
                    line(None, said)            # the Claude Code line above already says what to do
                elif p.get('state') == 'signed_out':
                    line(False, said, provider_words.DOCTOR_SIGN_IN.format(
                        name=name, how=getattr(cls, 'sign_in', '') or provider_words.SIGN_IN_PLAIN))
                elif p.get('state') == 'not_installed':
                    line(False, said, provider_words.DOCTOR_INSTALL.format(vendor=getattr(cls, 'vendor', '') or name,
                                                                           name=name))
                else:
                    line(True, said)
    except Exception:  # noqa: BLE001 - the board is never the reason doctor stops
        line(None, provider_words.DOCTOR_NOT_KNOWN)


def _doctor_new_parts(line):
    """Memory search, the Claude Code switches, the keys and the gateway. Reads only; a key is never read."""
    from . import claudecode, gateway, lanes, memorypage
    from .packs import memory
    try:
        s = memory.search_status()
        line(True if s['on'] else None, 'Memory search: %s' % ('%d chats indexed%s' % (
            s['chats'], '' if s['fts5'] else ', plain search (this Python has no FTS5)') if s['on'] else 'not built yet'),
             'It builds when you open the Memory page.')
    except Exception as e:  # noqa: BLE001
        if 'no such table' in str(e):           # the first build has not finished (or never ran)
            line(None, 'Memory search: not built yet', 'It builds when you open the Memory page.')
        else:
            line(False, 'Memory search', "The index can't be read (%s). Delete %s and open the Memory page." % (e, memory.index_path()))
    health = memorypage.health_line()
    if health:
        line(None, health, "Trim that MEMORY.md: what is over the limit is not read at the start of a chat.")
    h, c, m = claudecode.house_view(), claudecode.codemap_view(), claudecode.claudemem_view()
    line(None if h['state'] in ('off', None) else h['state'] == 'on', 'House rules: %s' % (h['state'] or 'unreadable'),
         'Off. Turn it on from the page (Claude Code).' if h['state'] == 'off' else
         'The block was changed by hand; Switchboard leaves it to you.')
    line(c['on'] or None, 'Code map: %s%s' % ('on' if c['on'] else 'off',
                                            ', found %s' % ' and '.join(c['found']) if c['found'] else ', no map found'),
         'Optional. Turn it on from the page once a map is found.')
    line(m['on'] or None, 'Watching chats with claude-mem: %s' % (
        'off' if not m['on'] else 'on, weekly cap %g%%%s%s' % (m['cap'], '' if m['installed'] else ', claude-mem not installed yet',
                                                               ', paused by the cap' if m.get('paused') else '')),
         'Optional. Turn it on from the page.')
    for name, s in (('claude-mem', m.get('setup')), ('codegraph', c.get('setup'))):     # installed by Switchboard
        if s and s.get('by_switchboard'):
            h = s.get('health') or {}
            line(False if s.get('stopped') else (h.get('ok') or None) if h else True,
                 '%s %s, installed by Switchboard%s' % (name, s.get('version'), ': stopped part-way' if s.get('stopped')
                                                        else ': ' + h['line'] if h.get('line') else ''),
                 'Repair it from the page (Settings, In Claude Code, %s).' % ('claude-mem' if name == 'claude-mem' else 'Code map'))
    rows = lanes.lanes()
    if not rows:
        line(None, 'Keys: none', 'Optional, for when every account is out: switchboard keys add NAME')
    else:
        store = lanes.store()
        for x in rows:
            line(store.present(x['name']), 'Key %s (ends in %s) in your Keychain' % (x['name'], x['last4']),
                 'Its Keychain item is gone: switchboard keys remove %s, then add it again.' % x['name'])
        wanted = bool(core.settings().get('gateway_on'))
        up = lanes.answers()
        line(up if wanted else None, 'Keys gateway: %s' % ('answering on 127.0.0.1:%d' % gateway.port() if up else 'off' if not wanted
                                                         else 'not answering on 127.0.0.1:%d' % gateway.port()),
             'It runs inside the page: open the page.' if wanted else 'Turn it on from the page (Settings, then Keys).')


SWITCH_USAGE = 'switchboard switch [NAME|--best] [--json]'


def switch_command(args):
    """`switchboard switch [NAME]`: to that account, or alone to the next one in the list after the one in use
    (passing over one that needs a fresh sign-in, and one kept out of auto-switch: that one only by its name; and one
    at or past a line, judged by its own lines where it has them, while another is not). `--best`: where auto-switch
    would land now (autorules.switch_best)."""
    if args == ['--best']:                              # where auto-switch would land now (autorules.py)
        from . import autorules
        return _said_after(autorules.switch_best(current_view, switch))
    if len(args) > 1 or args[:1] and args[0].startswith('-'):     # a flag it does not take is no account's name
        print(SWITCH_USAGE)
        return 2
    if args:
        return _said_after(use(args[0]))
    view = current_view()
    rows = [a for a in term.read(view)['accounts'] if not a['unmanaged'] and a['slot']]
    if len(rows) < 2:
        raise SwitchError('There is no other account to switch to. Add one with: switchboard add')
    at = next((i for i, a in enumerate(rows) if a['active']), -1)
    usable = [a for a in rows[at + 1:] + rows[:max(at, 0)]
              if not a['needs_login'] and not a['active'] and a['rule'] != 'out']
    taken = set(a.get('email') for a in view.get('accounts') or [] if a.get('elsewhere'))   # shown, never decided on
    for a in [x for x in usable if x['email'] in taken or not any(lim['over'] for lim in x['limits'])] + usable:
        return _said_after(use(a['email']))        # the next one under its lines, else the next one in the list
    others = [a for a in rows if not a['active']]
    if all(a['rule'] == 'out' for a in others):
        raise SwitchError('Every other account is kept out of auto-switch; name one to move there: '
                          'switchboard switch NAME')
    if any(a['rule'] == 'out' for a in others):
        raise SwitchError('Every other account needs a fresh sign-in or is kept out of auto-switch; name a kept-out '
                          'one to move there: switchboard switch NAME')
    raise SwitchError('Every other account needs a fresh sign-in: in Claude Code, run /login as one of them.')


def _said_after(code):
    """After a switch's result: the line saying when Claude Code is not on a login at all, so the switch did nothing."""
    said = auth_said(after_switch=True)
    if said:
        print(said)
    return code


def remove_command(args):
    """`switchboard remove NAME [--yes]`: forget an account and its saved login here (engine.remove). It is never
    kept again by itself (keep.let_go); `switchboard add` keeps it again. Claude Code and the browser are not touched."""
    names = [a for a in args if a != '--yes']
    if len(names) != 1:
        print('switchboard remove NAME [--yes]')
        return 2
    if core.DEMO:
        raise SwitchError('The demo has made-up accounts; nothing is removed.')
    try:
        a = engine.find(names[0])
    except SwitchError:
        from . import bearers
        t = bearers.find(names[0])          # a one-year token or an API key kept as an account
        if t is None:
            raise
        return bearers.remove(t, yes='--yes' in args)
    active = (identity() or (None,))[0] == a.email
    if '--yes' not in args:
        if not terminal():
            raise SwitchError('Add --yes to remove an account without being asked.')
        from . import more
        if input(more.remove_line(a.email, active) + ' [y/N] ').strip().lower() not in ('y', 'yes'):
            print('Nothing removed.')
            return 1
    engine.remove(a.id)
    keep.let_go((a.email, a.org_id or ''))
    history.note('Removed %s from Switchboard' % a.email, 'warn')
    print('Removed %s.%s' % (a.email, ' Claude Code stays on it until you switch.' if active else ''))
    return 0


def alias_command(args):
    """`switchboard alias NAME [SHORT]`: a short name to type for an account; without SHORT, its short name goes."""
    if len(args) not in (1, 2):
        print('switchboard alias NAME [SHORT]')
        return 2
    a = engine.find(args[0])
    engine.set_alias(a.id, args[1] if len(args) == 2 else None)
    print('%s is %s now.' % (a.email, args[1]) if len(args) == 2 else '%s has no short name now.' % a.email)
    return 0


def run_command(args):
    """`switchboard run [NAME] [--bare] [--no-history|--history] [--strict] -- [claude arguments]`: Claude Code as
    that account in this terminal only (api.run_as: the live login is not touched), with the person's Claude Code
    folder shared unless --bare; without NAME, as the account set for this folder (foldercmd.py). Exits with Claude
    Code's own exit code; anything Switchboard says goes to stderr, so a `-p` answer stays alone."""
    from . import foldercmd
    return foldercmd.run(args, command_help('run'))


def resume_command(args):
    """`switchboard resume CHAT [--as NAME] [--fork] [-- claude arguments]` (resume.py)."""
    from . import resume
    if args and not connect.claude_bin():
        raise SwitchError('Claude Code is not installed on this Mac. Install it first: https://claude.com/claude-code')
    return resume.main(args)


HELP = ('-h', '--help')
UNLISTED = HELP + ('help', '--version', '-v', 'tidy')   # commands it knows that have no lines of their own in the help
# Short words for commands: the first word only, put back as the command's own word at the top of main(), so each is
# that command in every way (its words, its help, its exit codes). The help names them on one line.
SHORT_WORDS = {'ls': 'status', 'list': 'status', 'rm': 'remove', 'upgrade': 'update', 'whoami': 'current'}


def nearest_command(cmd):
    """The command a typing mistake most likely meant (`stauts`: status), from the help's own commands and the short
    words, or None when no command is close."""
    import difflib
    import inspect
    if not cmd or cmd.startswith('-'):
        return None
    words = ['help']
    for line in inspect.cleandoc(__doc__).splitlines():
        first = re.split(r'\s{2,}', line, maxsplit=1)[0].split() if line.startswith('switchboard ') else []
        if len(first) > 1 and first[1].isalpha() and first[1] not in words:
            words.append(first[1])
    found = difflib.get_close_matches(cmd.lower(), words + list(SHORT_WORDS), n=1, cutoff=0.75)
    return found[0] if found else None


def unknown(cmd):
    """A command it does not know, with --help or without: it says so, with the nearest real command when one is
    close, then the help."""
    near = nearest_command(cmd)
    print('Unknown command: %s.%s' % (cmd, ' Did you mean %s?' % near if near else ''))
    print(__doc__.strip())
    return 2


def command_help(cmd):
    """CMD's own lines of the help above (an account's number or email: `switchboard 2`'s), or None when it has none.
    The help is read as Python 3.13 and newer keep a docstring (inspect.cleandoc), so it is the same on every Python."""
    import inspect
    key = '2' if cmd.isdigit() or EMAIL_RE.match(cmd) else cmd
    out, take = [], False
    for line in inspect.cleandoc(__doc__).splitlines():
        if line.startswith('switchboard'):              # a command's first line; the lines under it go with it
            words = re.split(r'\s{2,}', line, maxsplit=1)[0].split()
            take = len(words) > 1 and words[1] == key
        elif not line.startswith(' '):
            take = False
        if take:
            out.append(line)
    return '\n'.join(out) or None


def asks_help(cmd, rest):
    """`switchboard CMD ... --help` (or -h): CMD's own help, and nothing runs. Of `switchboard claude`'s arguments
    only the first is Switchboard's, and of `switchboard run`'s (and `resume`'s and `hand`'s) only those before `--`:
    `switchboard hand codex -- "what does --help say"` hands that job on."""
    if cmd == 'claude':
        rest = rest[:1]
    if cmd in ('run', 'resume', 'hand') and '--' in rest:
        rest = rest[:rest.index('--')]
    return any(a in HELP for a in rest)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    from . import debug
    args, traced = debug.take(args)                           # --debug, or SWITCHBOARD_DEBUG=1 (debug.py)
    if traced:
        with debug.tracing(sys.modules[__name__], args):
            return main(args)
    args[:1] = [SHORT_WORDS.get(a, a) for a in args[:1]]       # ls, rm, whoami, ...: the command's own word
    cmd = args[0] if args else ''
    if not args:
        return home()
    if asks_help(cmd, args[1:]):
        if not command_help(cmd) and cmd not in UNLISTED:
            return unknown(cmd)
        print(command_help(cmd) or __doc__.strip())
        return 0
    if cmd in ('-h', '--help', 'help'):
        print(__doc__.strip())
    elif cmd in ('version', '--version', '-v'):
        print('switchboard %s' % core.version_text())         # with the commit it was made from, when known
    elif cmd == 'serve':
        from . import server
        server.serve()
    elif cmd == 'demo':
        os.environ.setdefault('SWITCHBOARD_PORT', '7798')
        core.DEMO = True
        from . import server
        # The tab opens once the demo holds its port: never on a page that did not start.
        server.serve(ready=lambda: open_page(wait=False))
    elif cmd == 'page':
        if page_running():
            return 0 if open_page() else 1
        else:
            print('The page is not running. Start it with `switchboard serve`, or run the installer again.')
            return 1
    elif cmd == 'status' and '--json' in args:
        return status_json()
    elif cmd == 'status':
        return status()
    elif cmd == 'menubar':
        from . import menubar
        return menubar.main(args[1:])
    elif cmd == 'setup':
        return setup(args[1:])
    elif cmd == 'add' and '--token' in args[1:]:    # a one-year token or an API key kept as an account
        from . import bearers
        return bearers.add_command(args[1:])
    elif cmd == 'add' and args[1:2] == ['claude']:
        return add(args[2:])
    elif cmd == 'add' and args[1:2] and args[1].startswith('-'):    # its flags: switchboard add --as work
        return add(args[1:])
    elif cmd == 'add' and len(args) > 1:            # another provider: switchboard add codex
        from .providers import cli as providers_cli
        return providers_cli.main(args)
    elif cmd == 'add':
        return add(args[1:])
    elif cmd == 'signin':
        return signin_command(args[1:])
    elif cmd == 'update':
        return update(args[1:])
    elif cmd == 'use' and len(args) > 1:
        if '--json' in args:                    # the same switch, then one JSON document (scripted.py)
            from . import scripted
            return scripted.switch_json(scripted.use_words(use), scripted.without_json(args[1:]))
        return use(args[1])
    elif cmd == 'open' and len(args) > 1:
        open_link(args[1])
    elif cmd == 'owner' and len(args) == 3 and args[1:].count('--json') == 1:
        return owner_json(next(a for a in args[1:] if a != '--json'))
    elif cmd == 'owner' and len(args) == 2:
        return owner_command(args[1])
    elif cmd == 'owner' and '--json' in args:       # one document, also for words it does not take (scripted.py)
        from . import scripted
        return scripted.wrong_words('switchboard owner LINK [--json]')
    elif cmd == 'switch':
        if '--json' in args:                    # the same switch, then one JSON document (scripted.py)
            from . import scripted
            return scripted.switch_json(switch_command, scripted.without_json(args[1:]))
        return switch_command(args[1:])
    elif cmd == 'remove' and len(args) > 1:
        return remove_command(args[1:])
    elif cmd in ('move', 'swap'):
        from . import places
        return (places.move_command if cmd == 'move' else places.swap_command)(args[1:], sys.modules[__name__])
    elif cmd == 'leftovers':
        from . import leftovers
        if '--json' in args and args[1:] != ['--json']:     # one document, also for words it does not take
            from . import scripted
            return scripted.wrong_words(leftovers.FORMS.replace('\n', '; '))
        return leftovers.main(args[1:], sys.modules[__name__])
    elif cmd == 'alias' and len(args) > 1:
        return alias_command(args[1:])
    elif cmd == 'run':
        return run_command(args[1:])
    elif cmd == 'folder':
        from . import foldercmd
        return foldercmd.folder(args[1:])
    elif cmd == 'folders':
        from . import foldercmd
        return foldercmd.folders_command(args[1:])
    elif cmd == 'resume':
        return resume_command(args[1:])
    elif cmd == 'current':
        from . import scripted
        return scripted.current(args[1:])
    elif cmd == 'pages':
        print_pages()
    elif cmd == 'check':
        return check()
    elif cmd == 'links':
        return links_command(args[1:])
    elif cmd in ('export', 'import') and len(args) > 1:
        from . import backup
        return backup.main(args)
    elif cmd == 'readings' and len(args) > 1:
        from . import readings
        return readings.main(args[1:])
    elif cmd == 'doctor':
        return doctor_command(args[1:])
    elif cmd == 'usage':
        usage_command(args[1:])
    elif cmd == 'chats':
        from . import chats
        wrong = chats._options(args[1:]) if '--json' in args else {}
        if not isinstance(wrong, dict):                 # one document, also for words it does not take
            from . import scripted
            return scripted.wrong_words('%s %s' % (wrong, chats.USAGE))
        return chats.main(args[1:])
    elif cmd == 'awake' and '--once' in args:
        return awake_command(args[1:])
    elif cmd == 'config':
        from . import config
        return config.main(args[1:], ask=ask_page)
    elif cmd == 'statusline':
        return statusline_command(args[1:])
    elif cmd == 'notify':
        return notify_command(args[1:])
    elif cmd == 'hooks':
        return hooks_command(args[1:])
    elif cmd == 'models':
        return models_command(args[1:])
    elif cmd == 'tidy':                         # used by the uninstaller: see tidy()
        return tidy('--all' in args)
    elif cmd == 'keys':
        return keys_command(args[1:])
    elif cmd == 'claude':
        return claude_on_keys(args[1:])
    elif cmd == 'auto':
        return auto_command(args[1:])
    elif cmd == 'log':
        return history.main(args[1:])
    elif cmd in ('providers', 'continue', 'back', 'hand', 'allow', 'stay', 'order'):
        from .providers import cli as providers_cli
        return providers_cli.main(args)
    elif len(args) == 1 and (cmd.isdigit() or EMAIL_RE.match(cmd)):
        return go_to(cmd)                       # switchboard 2, switchboard you@example.com
    elif command_help(cmd):                     # a command it knows, without the words it needs: its own lines
        print(command_help(cmd))
        return 2
    else:
        return unknown(cmd)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except SwitchError as e:
        sys.exit(str(e))
    except KeyboardInterrupt:
        try:
            if sys.stdout.isatty():
                sys.stdout.write('\n')
        except (AttributeError, ValueError):
            pass
        sys.exit(0)
    except BrokenPipeError:                     # `switchboard status | head`: the reader has what it wanted
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())     # so the exit's own flush is quiet too
        except (OSError, AttributeError, ValueError):
            pass
        sys.exit(128 + signal.SIGPIPE)          # what a shell reports for a command whose reader went away
    except Exception as e:  # noqa: BLE001 - one sentence here; the traceback, redacted, goes to the log
        import traceback
        core.log('switchboard %s: %s' % (' '.join(sys.argv[1:2]), traceback.format_exc()))
        where = '' if core.DEMO else '; the details are in ~/.switchboard/switchboard.log'    # (the demo writes none)
        sys.exit('Something went wrong: %s. `switchboard doctor` checks every part%s.' % (type(e).__name__, where))
