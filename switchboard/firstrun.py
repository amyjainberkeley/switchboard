"""The first run, in the terminal: `switchboard setup`, and `switchboard` alone before any account is kept.

    Switchboard setup.

    1 of 5  Your account
      Claude Code is signed in as maya@northwind.example. Kept.

    2 of 5  Your other accounts
      Each signs in through your browser, on Anthropic's own page. Nothing is signed out by this step.
      Add a Claude account?  Enter opens your browser, s skips.
      maya@ivy.example (Max 5x). Kept.
      ...

    3 of 5  See it move
      Claude Code is on maya@northwind.example. Switchboard moves it at your lines, and you can move it any time.
      Move Claude Code to maya@ivy.example now?  Enter moves it, s skips.
      Moved. Open chats follow within about 30 seconds. Back: switchboard switch maya@northwind.example

    4 of 5  Links that open in the right account
    5 of 5  Where to find it

Each step is numbered "N of M", where M counts only the steps this run shows: a step with nothing to do is left out
and not counted. Every line says what was checked just before it was printed: an account is "Kept" once the engine
has kept its login, links are "Done" once macOS has handed the default-browser seat to the link app, and step 4 names
only what is on on this Mac now.

Another account signs in the way the page's Add account does it (connect.py, through the running page: POST
/api/connect, then /api/connect-look): Claude Code's own sign-in runs in a throwaway folder and opens Anthropic's page
in a new browser profile, so the login in use is never touched, and the engine keeps the new login. Nothing here
presses, fills or reads anything on that page; the person approves it there. Ctrl-C while it waits cancels that one
sign-in with the page's own Cancel. When the page is not running, its background job is asked to start it, and
when it does not, one line names switchboard doctor.

The link step explains, before macOS asks, what the question it shows is for: macOS words that question and no
program can change it. Then it waits for the answer as long as the person takes (Ctrl-C moves on), and says what the
seat holds now.

Without a terminal nothing is asked; at most three lines say what was done and the command that does the rest.
`switchboard add` at a terminal, `switchboard links on` at a terminal, a sign-in again from the list, and
`switchboard help` use the parts below too.

Words a person reads are here, not in term.py, except the ones shared with other commands.
"""
import contextlib
import io
import os
import re
import subprocess
import sys
import textwrap
import time

from . import core, engine, linkapp, term

# ---------- the words ----------

TITLE = 'Switchboard setup.'
STEP_ACCOUNT = 'Your account'
STEP_OTHERS = 'Your other accounts'
STEP_MOVE = 'See it move'
STEP_ROUTING = 'Routing'
STEP_LINKS = 'Links that open in the right account'
STEP_WHERE = 'Where to find it'

SIGNED_IN_KEPT = 'Claude Code is signed in as %s. Kept.'
OTHERS_HOW = "Each signs in through your browser, on Anthropic's own page. Nothing is signed out by this step."
ASK_FIRST = 'Add a Claude account?  Enter opens your browser, s skips.'
ASK_ANOTHER = 'Add another?  Enter opens your browser, s skips.'
ASK_AGAIN = 'Try again?  Enter opens your browser, s skips.'
WAITING = 'Waiting for your browser. Ctrl-C cancels.'
WAITING_DEFAULT = ('Your browser opens the sign-in as it is: if it shows an account already here, sign out of '
                   'claude.ai there first.')
KEEPING = 'Your browser approved it. Keeping the login. Ctrl-C cancels.'
KEPT = '%s. Kept.'
KEPT_AGAIN = 'Signed in again as %s. Kept.'
SAME_DEFAULT = ('Your browser signed in as %s, which is already here. Sign out of claude.ai in that window, then '
                'press Enter to try again.')
SAME_PROFILE = ('Your browser signed in as %s, which is already here. Press Enter to try again: a new browser '
                'profile opens, and you sign in there as the other account.')
NOT_FINISHED = 'The sign-in did not finish: %s.'
NOT_STARTED = 'The sign-in did not start: %s'
OTHER_HERE = ('Your browser signed in as %s, which is already here, not as %s. Sign out of claude.ai in that window, '
              'then try again.')
OTHER_ADDED = 'Added %s, not %s: your browser was signed in as %s.'
CANCELLED = 'Cancelled. Nothing was added.'
CANCELLED_ON_PAGE = 'The sign-in was cancelled on the page. Nothing was added.'
LATE_KEEP = 'Your browser had already approved it, so its login is being kept. Ctrl-C leaves that to the page.'
GONE = 'The sign-in is no longer under way: the page may have restarted. Nothing was added.'
PAGE_STOPPED = 'The page stopped answering, so the sign-in could not be followed. Open the page to see where it is.'
PAGE_DOWN = ('Accounts are added through the page, which is not running and did not start. Run switchboard doctor to '
             'see why, then add them with switchboard add.')
PAGE_WAITING = 'Starting the page…'
NO_TERMINAL_ADD = 'Add each one at a terminal with: switchboard add'
CODEX_ASK = '%s is on this Mac too. Add it to your list?  Enter adds it, s skips.'

LINKS_WHY = ('A claude.ai link opens only for the account that made it. Switchboard can open each claude.ai link in '
             'the browser profile of its owner. Every other link opens in %s exactly as it does now. For this, '
             'macOS asks one question about your "default web browser": choose "Use Switchboard Links".')
LINKS_UNDO = 'Undo at any time: switchboard links off'
LINKS_ASK = "Turn this on?  Enter shows macOS's question, s skips."
LINKS_WAIT = 'macOS is asking. Choose "Use Switchboard Links". If you chose Keep, press Ctrl-C.'
LINKS_NO_ANSWER = 'No answer from macOS yet. Ctrl-C moves on.'
LINKS_DONE = 'Done. claude.ai links open as their owner. Everything else still opens in %s.'
LINKS_KEPT = 'No change. %s is still your default browser. Turn this on later with: switchboard links on'
LINKS_SKIPPED = 'Turn this on later with: switchboard links on'
LINKS_ON_ALREADY = 'Links open as their owner: on. Switchboard Links is the default browser.'

WHERE_MENU = ('Menu bar', 'the S, next to the clock')
WHERE_MENU_LOGIN = 'It starts when you log in.'
WHERE_CODE = 'Claude Code'
WHERE_STATUSLINE = 'a status line with the account and its limits'
WHERE_NOTIFY = 'a notification when a long turn is done or it needs you'
WHERE_PAGE = 'The page'
WHERE_PAGE_WORDS = 'switchboard page   (every account, chat and artifact, at 127.0.0.1:%d)'
WHERE_PAGE_DOWN = 'switchboard page   (at 127.0.0.1:%d; not running now: switchboard doctor says why)'
CHANGED = "Changed in Claude Code's settings: %s."
CHANGES = (('lined', 'status line', 'switchboard statusline off'),
           ('notified', 'notifications', 'switchboard notify off'),
           ('hooked', 'session hooks', 'switchboard hooks off'))
WHERE_AUTO = ('Auto-switch is on: at %s%% of the 5-hour limit or %s%% of the week, Claude Code moves to the next '
              'account.')

MOVE_ON = 'Claude Code is on %s. Switchboard moves it at your lines, and you can move it any time.'
MOVE_ASK = 'Move Claude Code to %s now?  Enter moves it, s skips.'
MOVED = 'Moved. Open chats follow within about 30 seconds. Back: switchboard switch %s'
NOT_MOVED = 'Not moved: %s. Claude Code stays on %s.'
STILL_ON = 'Not moved: Claude Code is still on %s.'
MOVE_SKIPPED = 'Nothing moved. Move it any time with: switchboard switch'
MOVE_NONE_ONE = ('There is no other account to move to yet. Add one any time with switchboard add, and Claude Code '
                 'moves at your lines.')
MOVE_NONE_FULL = 'No other account is under its lines now, so Claude Code stays on %s.'
ROUTING_FAILED = 'Routing could not be turned on: %s. Turn it on later with: switchboard router on'

# The short help: what a new person needs, on one screen of 80 columns. `switchboard help all` is every command.
HELP_LINES = (
    'Switchboard routes Claude Code between the Claude accounts you pay for,',
    'and puts Codex and your own API keys one command away.',
    '',
    '  switchboard          your accounts and limits; press a number to move there',
    '  switchboard add      add an account: it signs in through your browser',
    '  switchboard page     the page at 127.0.0.1:{port}: accounts, chats, artifacts',
    '  switchboard doctor   checks every part; --fix repairs what it can',
    '',
    '  switchboard help all   every command',
    'Stuck? switchboard log says what happened.',
)

NBSP = '\u00a0'
# Kept on one line when a paragraph is wrapped: a quoted name ("Use Switchboard Links") and a command to type.
WHOLE = re.compile(r'"[^"]*"|switchboard (?:links (?:on|off)|auto off|add(?: [^ ]+@[^ ]+[^ .])?|serve|setup|page|'
                   r'doctor|switch(?: [^ ]+@[^ ]+[^ .])?|statusline off|notify off|hooks off)\b')
LOOK_EVERY = 1.0            # seconds between two looks at a sign-in, the page or macOS's answer
PAGE_START_WAIT = 20        # seconds a first run waits for a page its background job is starting (the installer has
                            # just loaded that job when it runs `switchboard setup`)
PLAN_LOOKS = 3              # looks at the page's state for a newly kept account's plan, LOOK_EVERY apart


def short_help():
    """`switchboard help`: HELP_LINES with the page's port."""
    return '\n'.join(HELP_LINES).replace('{port}', str(core.port()))


# ---------- printing ----------

class Out(object):
    """Lines to the terminal: indented under a step, wrapped to its width, and one line that is rewritten in place
    while something waits (only when the output is a terminal; elsewhere it is printed once). `indent` is two spaces
    under a step of the first run, none for a command of its own (add, links on)."""

    def __init__(self, cli, indent='  '):
        self.cli, self.indent = cli, indent
        self.live = None            # the waiting line on screen now (a terminal only)
        self.said = None            # the waiting line printed last (elsewhere: each one once)

    def width(self):
        return max(40, min(self.cli.width() or 100, 100))

    def colour(self):
        return self.cli.colour()

    def line(self, text=''):
        self.clear()
        print(text)

    def para(self, text):
        self.clear()
        kept = WHOLE.sub(lambda m: m.group(0).replace(' ', NBSP), text)    # a button's name or a command: one line
        print(textwrap.fill(kept, self.width(), initial_indent=self.indent, subsequent_indent=self.indent,
                            break_long_words=False, break_on_hyphens=False).replace(NBSP, ' '))

    def step(self, n, m, title):
        self.line()
        self.line(term.paint([('%d of %d  ' % (n, m), None), (title, term.BOLD)], self.colour()))

    def row(self, label, words, label_w):
        lead = '  %s   ' % label.ljust(label_w)
        self.clear()
        print(textwrap.fill(words, self.width(), initial_indent=lead, subsequent_indent=' ' * len(lead),
                            break_long_words=False, break_on_hyphens=False))

    def waiting(self, text):
        """The one line that says what is waited for; rewritten in place."""
        if text == self.live or text == self.said:
            return
        try:
            tty = sys.stdout.isatty()
        except (AttributeError, ValueError):
            tty = False
        if tty:
            sys.stdout.write('\r\x1b[2K' + self.indent + term.paint([(text, term.DIM)], self.colour()))
            sys.stdout.flush()
            self.live = text
        else:
            print(self.indent + text)
            self.said = text

    def clear(self):
        self.said = None
        if self.live is not None:
            sys.stdout.write('\r\x1b[2K')
            sys.stdout.flush()
            self.live = None


def answer(tty, out, question):
    """'go' (Enter, y, yes), 'skip' (s, n, no, q, or Ctrl-C) or 'gone' (the terminal closed). Anything else asks again."""
    while True:
        out.clear()
        try:
            said = tty.ask(out.indent + question + '  ')
        except EOFError:
            return 'gone'
        except KeyboardInterrupt:
            print()
            return 'skip'
        if said in ('', 'y', 'yes'):
            return 'go'
        if said in ('s', 'n', 'no', 'q', 'skip'):
            return 'skip'


# ---------- the steps ----------

def links_offer():
    """The link app's state when the link step has something to do: installed, not the default browser, and not
    turned off on purpose (`switchboard links off`). Else None."""
    from . import server
    try:
        if core.settings().get(server.LINKS_WANTED) is False:
            return None
        st = linkapp.status(fresh=True)
    except Exception as e:  # noqa: BLE001 - the step is left out when the link app can't be read
        core.log('reading the link app for the first run: %r' % e)
        return None
    return st if st.get('installed') and not st.get('on') else None


def run(cli, tty, yes=False, closing=True):
    """The first run. `tty` is where questions are asked (None: no terminal); `yes` asks nothing and says every step
    (`switchboard setup --yes`); `closing` False leaves out the last line, for `switchboard` alone, which shows the
    accounts next. Returns 0. Raises SwitchError when the account Claude Code is on can't be kept."""
    if core.DEMO:
        raise core.SwitchError('The demo has made-up accounts, so there is nothing to set up.')
    if tty is None and not yes:
        return without_a_terminal(cli)
    out = Out(cli)
    asking = tty is not None and not yes
    me = cli.identity()
    offer = links_offer() if me and asking else None
    move = me and asking and may_move(cli)
    routing = router_parts() if me else None
    alone = providers_offered() if asking and not me else []     # Codex here, Claude Code not: offered, no dead end
    # The steps this run shows, planned before the first is printed, so "N of M" never counts one left out.
    steps = [STEP_ACCOUNT] + ([STEP_OTHERS] + ([STEP_MOVE] if move else []) + ([STEP_ROUTING] if routing else []) +
                              ([STEP_LINKS] if offer else []) + [STEP_WHERE] if me else
                              [alone[0][1]] if alone else [])
    out.line(term.paint([(TITLE, term.BOLD)], out.colour()))

    def step(name):
        out.step(steps.index(name) + 1, len(steps), name)
    step(STEP_ACCOUNT)
    if not step_account(cli, out, me):
        if alone:
            step(alone[0][1])
            offer_codex(cli, tty, out)
        return 0
    step(STEP_OTHERS)
    step_others(cli, tty if asking else None, out, me)
    if move:
        step(STEP_MOVE)
        move_step(cli, tty, out)
    if routing:
        step(STEP_ROUTING)
        routing_step(cli, tty, out)
    n = len(cli.saved_identities())
    end = cli.ended(n)                          # auto-switch, notifications and the status line: on by themselves
    if offer:
        step(STEP_LINKS)
        links_step(cli, tty, out, offer)
    step(STEP_WHERE)
    where(cli, out, end)
    if closing:
        out.line()
        out.line(last_line(cli, n, out.colour()))
    return 0


def step_account(cli, out, me):
    """Step 1: the account Claude Code is on, kept without a question. With none, the one thing to do. True when an
    account is kept."""
    if not me:
        out.para(cli.first_step())
        return False
    keep_current(cli, me)
    out.para(SIGNED_IN_KEPT % me[0])
    return True


def may_move(cli):
    """Whether "See it move" is planned. "N of M" is printed before step 2 adds anything, so the step is planned when
    a move can follow step 2: an account here to move to now, or the page up (or its job starting it) to add one."""
    try:
        if next_move(cli)[1] is not None:
            return True
    except Exception as e:  # noqa: BLE001 - planned on the page alone when the accounts can't be read
        core.log('reading the accounts for the first run: %r' % e)
    return cli.page_running() or cli.job_installed()


def next_move(cli):
    """(the account Claude Code is on, the account `switchboard switch` alone would move it to now, when that one is
    under its lines) from the engine's own accounts and readings; (on, None) when there is none."""
    view = cli.current_view(fresh=True)
    on = next((a.get('email') for a in view.get('accounts') or [] if a.get('active')), None)
    a, room = cli.next_account(view)
    return on, (a['email'] if a and room else None)


def move_step(cli, tty, out):
    """See it move: one real switch, through the same code as `switchboard switch` (cli.use: the running page when it
    answers, else the engine). "Moved." only once Claude Code's own login file names the account. Asked once; s
    changes nothing. When there is nothing to move to after step 2, one line says why."""
    me = cli.identity()
    on, to = next_move(cli)
    on = on or (me[0] if me else '')
    if not to:
        others = [a for a in cli.saved_identities() if not me or a[0] != me[0]]
        out.para(MOVE_NONE_FULL % on if others else MOVE_NONE_ONE)
        return False
    out.para(MOVE_ON % on)
    if answer(tty, out, MOVE_ASK % to) != 'go':
        out.para(MOVE_SKIPPED)
        return False
    said = io.StringIO()
    try:
        with contextlib.redirect_stdout(said):
            cli.use(to)
    except Exception as e:  # noqa: BLE001 - a refused or failed switch is said in one line; the first run goes on
        out.para(NOT_MOVED % (str(e).rstrip('.') or 'the switch did not finish', on))
        return False
    for line in said.getvalue().splitlines():   # the page's note about the account's links, if any
        if line.strip() and line.strip() != term.now_on(to):
            out.para(line)
    now = cli.identity()
    if not now or now[0] != to:
        out.para(STILL_ON % (now[0] if now else on))
        return False
    out.para(MOVED % on)
    return True


def router_parts():
    """(wire, hand_words) from this copy's `switchboard.router` package, which hands work between assistants; None when
    this copy has none (then the routing step does not exist and is not counted)."""
    try:
        from .router import hand_words, wire
    except ImportError:
        return None
    return wire, hand_words


def routing_step(cli, tty, out):
    """Routing: the router's own first-run lines (hand_words.FIRST_RUN), then what wire.turn_on() says it did, each
    line under the step. Nothing is asked. Whatever goes wrong on its side is said in one line, and the first run goes
    on. False when this copy has no router."""
    parts = router_parts()
    if parts is None:
        return False
    wire, hand_words = parts
    try:
        for line in list(hand_words.FIRST_RUN):
            out.para(str(line))
        for line in list(wire.turn_on() or []):
            out.para(str(line))
    except Exception as e:  # noqa: BLE001 - the other side's failure never stops the first run
        core.log('routing in the first run: %r' % e)
        out.para(ROUTING_FAILED % (str(e).rstrip('.') or type(e).__name__))
        return False
    return True


def step_others(cli, tty, out, me):
    """Step 2: each other account, through the browser, then Codex when it is here. Without a terminal (`--yes`): the
    command that does it."""
    out.para(OTHERS_HOW)
    if tty is None:
        out.para(NO_TERMINAL_ADD)
        return
    others(cli, tty, out, ASK_FIRST)
    offer_codex(cli, tty, out)


def keep_current(cli, me):
    """The account Claude Code is on: kept (added, or its newest login when it is already there), as add does."""
    from . import keep
    cli.save_current()
    keep.forget(me)                             # kept on purpose: a removed account comes back with setup


def without_a_terminal(cli):
    """No terminal (an agent's shell, Homebrew's log): nothing asked, at most three lines."""
    me = cli.identity()
    if me:
        keep_current(cli, me)
    n = len(cli.saved_identities())
    if not n:
        print(cli.first_step())
        return 0
    end = cli.ended(n)
    if me:
        print(SIGNED_IN_KEPT % me[0])
    on = term.on_words(auto=end['auto'], notify=end['notify'], statusline=end['statusline']).strip()
    theirs = term.STATUSLINE_THEIRS if end['lined'] == 'theirs' else ''
    if on or theirs:
        print(' '.join(x for x in (on, theirs) if x))
    print(term.run_this('Add your other accounts at a terminal with', 'switchboard setup', cli.colour()))
    return 0


def where(cli, out, end):
    """Step 4: only what is on on this Mac now. The page is always named."""
    from . import menubar
    rows = []
    try:
        menu = menubar.running_pid() is not None
        at_login = os.path.exists(menubar.agent_plist(menubar.AGENT))
    except Exception as e:  # noqa: BLE001 - not said when it can't be read
        core.log('reading the menu bar for the first run: %r' % e)
        menu = at_login = False
    if menu:
        rows.append((WHERE_MENU[0], WHERE_MENU[1] + ('. ' + WHERE_MENU_LOGIN if at_login else '.')))
    code = [w for w, on in ((WHERE_STATUSLINE, end.get('statusline')), (WHERE_NOTIFY, end.get('notify'))) if on]
    if code:
        rows.append((WHERE_CODE, ', and '.join(code)))
    running = cli.page_running()
    rows.append((WHERE_PAGE, (WHERE_PAGE_WORDS if running else WHERE_PAGE_DOWN) % core.port()))
    label_w = max(len(r[0]) for r in rows)
    for label, words in rows:
        out.row(label, words, label_w)
    if end.get('lined') == 'theirs':
        out.para(term.STATUSLINE_THEIRS)
    if end.get('auto'):
        auto = engine.auto_settings()
        five = auto.get('session_at') if isinstance(auto.get('session_at'), (int, float)) else 90
        week = auto.get('weekly_at') if isinstance(auto.get('weekly_at'), (int, float)) else 95
        out.para(WHERE_AUTO % (term.number(five), term.number(week)))
    changed = changes(end)
    if changed:
        out.para(changed)


def changes(end):
    """What this first run itself put into Claude Code's settings (ended(): only what went in now), each with the
    command that takes it out: "Changed in Claude Code's settings: status line (undo: switchboard statusline off),
    ...". None when nothing went in."""
    made = ['%s (undo: %s)' % (what, undo) for key, what, undo in CHANGES
            if (end.get(key) == 'on' if key == 'lined' else end.get(key) is True)]
    return CHANGED % ', '.join(made) if made else None


def last_line(cli, n, colour):
    """'2 Claude accounts and Codex. Type switchboard to see them.'"""
    other = [name for name in added_providers()]
    what = '%d Claude account%s' % (n, '' if n == 1 else 's')
    if other:
        what += ' and ' + ' and '.join(other)
    return term.paint([(what + '. Type ', None), ('switchboard', term.BOLD),
                       (' to see %s.' % ('it' if n == 1 and not other else 'them'), None)], colour)


# ---------- other providers (Codex) ----------

def board():
    from .providers import view as provider_view
    return provider_view.view()


def added_providers():
    """The names of the providers added besides Claude, in the person's order (built from files only)."""
    try:
        from .providers import view as provider_view
        b = board()
        return [p.get('name') or p.get('id') for p in provider_view._listed(b, 'providers')
                if p.get('id') != 'claude' and not p.get('account_list')]
    except Exception:  # noqa: BLE001 - only the last line's count depends on it
        return []


def providers_offered():
    """[(id, name)] of each provider on this Mac that is not added yet (Codex). Its adapter tells whether it is here
    from files only, and never opens its login file. [] when the board can't be built."""
    try:
        from .providers import view as provider_view
        return [(a['id'], a.get('name') or a['id']) for a in provider_view._listed(board(), 'add')
                if a.get('id') and a.get('id') != 'claude']
    except Exception as e:  # noqa: BLE001 - nothing offered when the board can't be built
        core.log('reading the providers for the first run: %r' % e)
        return []


def offer_codex(cli, tty, out):
    """Each provider on this Mac that is not added yet (Codex), offered once. Yes runs `switchboard add PROVIDER` and
    prints its lines."""
    for pid, name in providers_offered():
        if answer(tty, out, CODEX_ASK % name) != 'go':
            continue
        add_provider(pid, out)


def add_provider(pid, out):
    """`switchboard add PROVIDER`, its lines indented under the step and wrapped to the terminal."""
    import dataclasses
    from .providers import cli as providers_cli

    class Indented(object):
        def write(self, text):
            for line in text.splitlines(True):
                if line.strip():
                    out.para(line.rstrip('\n'))
                else:
                    sys.stdout.write(line)

        def flush(self):
            sys.stdout.flush()
    try:
        kit = dataclasses.replace(providers_cli.default_kit(), out=Indented())
    except ImportError:
        kit = None
    return providers_cli.main(['add', pid], kit)


# ---------- other accounts, through the page ----------

def page_up(cli, out):
    """The page answers. Right after an install its background job may still be starting it: when the job is
    installed, this asks launchd to start it, as the menu bar does (menubar.start_page), and waits on one line for up
    to PAGE_START_WAIT seconds. When it still does not answer: one line that names switchboard doctor. True once it
    answers."""
    if cli.page_running():
        return True
    if cli.job_installed():
        out.waiting(PAGE_WAITING)
        try:
            start_page()
        except Exception as e:  # noqa: BLE001 - the wait below finds out either way
            core.log('starting the page for the first run: %r' % e)
        waited = 0.0
        try:
            while waited < PAGE_START_WAIT:
                cli._sleep(LOOK_EVERY)
                waited += LOOK_EVERY
                if cli.page_running():
                    out.clear()
                    return True
        except KeyboardInterrupt:               # skips the wait; the first run goes on without adding
            out.clear()
            print()
        out.clear()
    out.para(PAGE_DOWN)
    return False


def start_page():
    """The page's own background job, started (or loaded again) through launchd, as the menu bar starts it."""
    from . import menubar
    return menubar.start_page()


def others(cli, tty, out, question):
    """Sign in to other accounts, one at a time, each through the browser. Returns the emails kept, or None when the
    page could not be reached."""
    if not page_up(cli, out):
        return None
    kept = []
    while question:
        said = answer(tty, out, question)
        if said != 'go':
            break
        r = sign_in(cli, out)
        if r['how'] in ('kept', 'again'):
            kept.append(r['email'])
            question = ASK_ANOTHER
        elif r['how'] == 'down':
            break
        elif r['how'] == 'cancelled':
            question = ASK_ANOTHER if kept else ASK_FIRST
        else:
            question = ASK_AGAIN
    return kept


def accounts_now(cli):
    """{email: the page's account} of the accounts Switchboard keeps, as the running page has them; None when the
    page does not answer."""
    st = cli.page_state()
    if st is None:
        return None
    return dict(((a.get('email') or '').lower(), a) for a in st.get('accounts') or []
                if a.get('email') and not a.get('unmanaged'))


def sign_in(cli, out, email=''):
    """One sign-in through the running page (its Add account, or an account's Sign in when `email` is given), followed
    to its end. Returns {'how': 'kept' (a new account), 'again' (one already here that needed it), 'same' (the browser
    came back as an account already here), 'failed', 'cancelled' or 'down' (the page can't be reached), 'email'}."""
    before = accounts_now(cli)
    if before is None:
        out.para(PAGE_STOPPED)
        return {'how': 'down', 'email': None}
    profile = '+new'
    if email:
        p = (before.get(email.lower()) or {}).get('profile')
        profile = p or '+new'
    try:
        got = cli.ask_page('/api/connect', {'email': email, 'profile': profile})
    except KeyboardInterrupt:                   # the page may have started it already: cancel it, as follow() does
        out.clear()
        print()
        cancel(cli, email.lower() or 'new')
        out.para(CANCELLED)
        return {'how': 'cancelled', 'email': None}
    except engine.Refused as e:
        out.para(NOT_STARTED % e)
        return {'how': 'failed', 'email': None}
    except OSError:
        out.para(PAGE_STOPPED)
        return {'how': 'down', 'email': None}
    key, started = got.get('key'), got.get('started')
    if not isinstance(key, str) or not key:
        out.para(NOT_STARTED % 'the page did not say which sign-in it started.')
        return {'how': 'failed', 'email': None}
    look = follow(cli, out, key, started)
    if look is None:
        return {'how': 'down', 'email': None}
    if look.get('state') == 'cancelled':
        out.para(CANCELLED)
        return {'how': 'cancelled', 'email': None}
    if look.get('state') != 'over':
        out.para(GONE)
        return {'how': 'failed', 'email': None}
    result = look.get('result') or {}
    real = (result.get('got') or '').lower()
    if not result.get('ok'):
        if result.get('why') == 'stopped':
            out.para(CANCELLED_ON_PAGE)
            return {'how': 'cancelled', 'email': None}
        if real and email and real != email.lower():        # asked for one account, the browser approved another
            out.para(OTHER_HERE % (real, email.lower()) if real in before else
                     OTHER_ADDED % (real, email.lower(), real))
            return {'how': 'same' if real in before else 'kept', 'email': real}
        out.para(NOT_FINISHED % (result.get('why') or 'the sign-in did not finish'))
        return {'how': 'failed', 'email': real or None}
    had = before.get(real)
    if had is not None and not had.get('needs_login') and not email:
        out.para((SAME_DEFAULT if look.get('default_browser') else SAME_PROFILE) % real)
        return {'how': 'same', 'email': real}
    if had is not None:
        out.para(KEPT_AGAIN % real)
        return {'how': 'again', 'email': real}
    out.para(KEPT % with_plan(cli, real))
    return {'how': 'kept', 'email': real}


def cancel(cli, key):
    """The page's Cancel for the sign-in under `key`; a page that does not answer, or a second Ctrl-C, leaves it to
    the page (it ends a sign-in by itself after its own wait)."""
    try:
        cli.ask_page('/api/cancel', {'key': key})
    except (OSError, KeyboardInterrupt, engine.Refused) as e:
        core.log('cancelling a sign-in from the terminal: %r' % e)


def with_plan(cli, email):
    """'maya@ivy.example (Max 5x)': its plan as the page says it once it has read it; else the email alone."""
    try:
        for i in range(PLAN_LOOKS):
            a = (accounts_now(cli) or {}).get(email)
            words = term.plan_words(a) if a else ''
            if words:
                return '%s (%s)' % (email, words)
            if i < PLAN_LOOKS - 1:
                cli._sleep(LOOK_EVERY)
    except KeyboardInterrupt:                   # the account is kept already; only its plan word is left out
        print()
    return email


def follow(cli, out, key, started):
    """Wait for one sign-in to end, saying what it waits for on one line. Ctrl-C cancels it with the page's Cancel;
    one the browser had just approved is then left to be kept, and followed to its end (a second Ctrl-C leaves it to
    the page). Returns the page's last look ({'state': 'over', 'result'}, or 'cancelled', or None for a page that
    stopped answering)."""
    said_default = False
    cancelled = False
    last = {}
    while True:
        try:
            try:
                look = cli.ask_page('/api/connect-look', {'key': key, 'started': started})
            except OSError:
                out.clear()
                out.para(PAGE_STOPPED)
                return None
            state = look.get('state')
            if state in ('over', None):
                out.clear()
                if state is None and cancelled:
                    return {'state': 'cancelled'}
                if state == 'over':
                    look['default_browser'] = last.get('default_browser', False)
                return look
            last = look
            if state == 'waiting' and look.get('default_browser') and not said_default:
                out.para(WAITING_DEFAULT)
                said_default = True
            out.waiting(KEEPING if state == 'keeping' else WAITING)
            cli._sleep(LOOK_EVERY)
        except KeyboardInterrupt:
            out.clear()
            if cancelled:
                return {'state': 'cancelled'}
            cancelled = True
            try:
                cli.ask_page('/api/cancel', {'key': key})
                look = cli.ask_page('/api/connect-look', {'key': key, 'started': started})
            except (OSError, KeyboardInterrupt):
                return {'state': 'cancelled'}
            if look.get('state') == 'over' and (look.get('result') or {}).get('ok'):
                return look                     # it was kept just before the Cancel reached the page
            if look.get('state') != 'keeping':
                return {'state': 'cancelled'}
            out.para(LATE_KEEP)


# ---------- links that open as their owner ----------

def browser_name(holder):
    """The browser that holds the seat now, by name; else 'your browser'."""
    holder = holder or {}
    return holder.get('name') if holder.get('kind') == 'browser' and holder.get('name') else 'your browser'


def plain_browser(before):
    """Where plain links go once the link app holds the seat: the browser it noted when macOS was asked
    (previous-browser.txt), by name; else the one that held the seat before."""
    bid = core.read_text(core.data_path('previous-browser.txt'))
    if bid and bid in linkapp.BROWSER_IDS:
        return linkapp.BROWSER_IDS[bid]
    return before


def explain_links(out, holder):
    out.para(LINKS_WHY % browser_name(holder))
    out.para(LINKS_UNDO)


def links_step(cli, tty, out, st):
    """Step 3, and `switchboard links on` at a terminal: the explanation, then macOS's question only after Enter,
    then what the seat holds."""
    holder = linkapp.seat(look=False).get('holder') if st else {}
    explain_links(out, holder)
    if answer(tty, out, LINKS_ASK) != 'go':
        out.para(LINKS_SKIPPED)
        return False
    return ask_macos(cli, out, browser_name(holder))


def make_default():
    """The link app's --make-default, started: macOS shows its question, and the process ends with the answer."""
    return subprocess.Popen([linkapp.bin_path(), '--make-default'], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)


def ask_macos(cli, out, before):
    """Ask macOS to make the link app the default browser (the link app's --make-default, which waits for the answer
    and exits 0 when the seat changed, 1 when it did not, 2 after its own wait), then wait as long as the person takes:
    Ctrl-C moves on. What is said is what the seat holds afterwards. Returns True when links open as their owner."""
    from . import server
    core.set_setting('links_by_itself', time.time())        # asked: the first run never asks by itself again
    try:
        proc = make_default()
    except OSError as e:
        core.log('the link app did not start: %r' % e)
        out.para(LINKS_KEPT % before)
        return False
    interrupted = False
    try:
        while proc.poll() is None:
            out.waiting(LINKS_WAIT)
            cli._sleep(LOOK_EVERY)
        if proc.returncode == 2:                    # its own 180 s wait ended with no answer: macOS may still ask
            while not linkapp.status(fresh=True).get('on'):
                out.waiting(LINKS_NO_ANSWER)
                cli._sleep(LOOK_EVERY)
        elif proc.returncode not in (0, 1):         # it did not run as it should (refused at launch, or crashed)
            core.log('the link app --make-default exited %r' % proc.returncode)
    except KeyboardInterrupt:
        interrupted = True
    out.clear()
    on = bool(linkapp.status(fresh=True).get('on'))
    if on or (proc.returncode == 1 and not interrupted):    # "Keep" is an answer too: nothing offers it again
        core.set_setting(server.LINKS_WANTED, on)           # by itself; a Ctrl-C or a crash is not an answer
    if on:
        out.para(LINKS_DONE % plain_browser(before))
    else:
        out.para(LINKS_KEPT % (before[0].upper() + before[1:]))
    return on


def links_on(cli, tty):
    """`switchboard links on` at a terminal: the same explanation as the first run before macOS asks. Returns the
    exit code."""
    out = Out(cli, indent='')
    st = linkapp.status(fresh=True)
    if st.get('on'):
        out.line(LINKS_ON_ALREADY)
        return 0
    if not st.get('installed'):
        out.line('Links open as their owner: off. ' + term.LINKS_NEEDS)
        return 1
    holder = linkapp.seat(look=False).get('holder')
    explain_links(out, holder)
    if answer(tty, out, LINKS_ASK) != 'go':
        out.para(LINKS_SKIPPED)
        return 0
    ask_macos(cli, out, browser_name(holder))
    return 0


# ---------- `switchboard add` at a terminal, and a sign-in again from the list ----------

def add(cli, tty):
    """`switchboard add` at a terminal, while the page runs: one browser sign-in at once, then "Add another?", then
    Codex when it is here and not added. Returns the exit code."""
    out = Out(cli, indent='')
    me = cli.identity()
    if me:
        new = me not in cli.saved_identities()
        keep_current(cli, me)
        if new:
            out.line(term.saved(me[0]))
        cli.auto_here()
    r = sign_in(cli, out)
    if r['how'] != 'down':
        kept = r['how'] in ('kept', 'again')
        others(cli, tty, out, ASK_ANOTHER if kept else ASK_FIRST if r['how'] == 'cancelled' else ASK_AGAIN)
    cli.auto_here()                             # the second account: auto-switch on by itself, said once
    offer_codex(cli, tty, out)
    out.line(term.all_accounts(len(cli.saved_identities()), cli.colour()))
    return 0


def add_one(cli, email):
    """`switchboard add EMAIL`: that account's sign-in in the browser, through the running page (in its own browser
    profile when it has one, else a new one), followed to its end. Returns the exit code."""
    if core.DEMO:
        raise core.SwitchError('The demo has made-up accounts, so there is nothing to add.')
    out = Out(cli, indent='')
    if not page_up(cli, out):
        return 1
    r = sign_in(cli, out, email)
    if r['how'] in ('kept', 'again'):
        cli.auto_here()
    return 0 if r['how'] in ('kept', 'again') and r['email'] == email.lower() else 1


def sign_in_again(cli, email):
    """An account whose login needs a fresh sign-in, picked in the list: its sign-in through the running page, in its
    own browser profile, as its row's Sign in starts it. Returns the exit code, or None when the page is not running
    (the caller says what else to do)."""
    if not cli.page_running():
        return None
    out = Out(cli, indent='')
    r = sign_in(cli, out, email)
    return 0 if r['how'] in ('again', 'kept') and r['email'] == email.lower() else 1

