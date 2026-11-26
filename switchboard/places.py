"""Where accounts are in the list, from the terminal: `switchboard move NAME N`, `switchboard swap NAME NAME`, and the
short name and place `switchboard add --as SHORT --at N` gives the first account it keeps.

The list is the engine's own order. A number is a place in it, so it changes when the list changes: a script names an
account by its email or its short name. A change goes through the running page when it answers (POST /api/move, the
call its row menu makes), else it is made here with the function the page runs (more.move). Either way the list is
then read back from the engine and printed: numbers and whole emails.

`cli` is the command's own module (switchboard/__main__.py), passed in: its ask_page, identity and add are the ones
the command uses, and tests stand in for them there.
"""
from . import core, engine, more, names
from .accounts import api
from .core import SwitchError

MOVE = 'switchboard move NAME N'
SWAP = 'switchboard swap NAME NAME'
ADD = ('switchboard add [--as SHORT] [--at N]\n'
       'switchboard add --token [-] [--for NAME | --label WORD] [--as SHORT]')     # the second: bearers.USAGE


# ---------- the rule ----------

def moved(ids, who, to):
    """ids with `who` at place `to` (1 first); the others keep their order around it."""
    rest = [x for x in ids if x != who]
    return rest[:to - 1] + [who] + rest[to - 1:]


def swapped(ids, a, b):
    """ids with a and b in each other's place; nothing else moves."""
    return [b if x == a else a if x == b else x for x in ids]


# ---------- what is printed ----------

def listing(accounts):
    """The list as `switchboard move` prints it: '  1  maya@northwind.example', numbers lined up; two accounts with
    one email each with its organization (names.of_account)."""
    w = len(str(len(accounts)))
    named = names.all_of(accounts)
    return ['  %*d  %s' % (w, n, named.get(a.id) or a.id) for n, a in enumerate(accounts, 1)]


def _name(a):
    """An account's name as the page's state keeps it: what the running page is sent, and what is printed."""
    return names.of_account(a, engine.accounts())


def _show(accounts):
    for line in listing(accounts):
        print(line)


def _place(word):
    """A place as typed (digits only), or None when it is not a number."""
    return int(word) if isinstance(word, str) and word.isdigit() else None


def _no_place(n, accounts, last=None):
    """No place n; `last`: the last place the account can have when it is not the list's length (a new account
    can go one past the end)."""
    if last is not None and last != len(accounts):
        print('There is no place %d: a new account can go at 1 to %d.' % (n, last))
    else:
        print('There is no place %d: Switchboard has %d account%s.' % (n, len(accounts),
                                                                       '' if len(accounts) == 1 else 's'))
    _show(accounts)
    return 1


def _where(accounts, aid):
    return next(i for i, a in enumerate(accounts, 1) if a.id == aid)


def _not_in_demo():
    if core.DEMO:
        raise SwitchError("The demo has made-up accounts; change the order on its page.")


# ---------- making the change ----------

def put(cli, a, to):
    """Account `a` (api.Account) to place `to`: through the running page when it answers, else here."""
    try:
        cli.ask_page('/api/move', {'email': _name(a), 'to': to})
    except OSError:
        more.move(a.id, to)


# ---------- switchboard move, switchboard swap ----------

def move_command(args, cli):
    """`switchboard move NAME N`: the account to place N (1 first); the others keep their order. Exit 0 when it is
    there (also when it already was), 1 when there is no place N (the list is printed), 2 for wrong words."""
    if len(args) != 2 or _place(args[1]) is None:
        print(MOVE)
        return 2
    _not_in_demo()
    from . import bearers
    a, to = bearers.sign_in(args[0], 'move'), _place(args[1])
    before = engine.accounts()
    if not 1 <= to <= len(before):
        return _no_place(to, before)
    was = _where(before, a.id)
    if was == to:
        print('%s is already at %d.' % (_name(a), to))
    else:
        put(cli, a, to)
        print('Moved %s from %d to %d.' % (_name(a), was, to))
    _show(engine.accounts())
    return 0


def swap_command(args, cli):
    """`switchboard swap NAME NAME`: two accounts change places. Through the page: two moves, as its row menu would
    make them; here: one change of the order. Twice puts them back."""
    if len(args) != 2:
        print(SWAP)
        return 2
    _not_in_demo()
    from . import bearers
    a, b = bearers.sign_in(args[0], 'swap'), bearers.sign_in(args[1], 'swap')
    if a.id == b.id:
        raise SwitchError('%s is named twice; nothing was changed.' % _name(a))
    before = engine.accounts()
    i, j = _where(before, a.id), _where(before, b.id)
    na, nb = _name(a), _name(b)
    try:
        cli.ask_page('/api/move', {'email': na, 'to': j})
        cli.ask_page('/api/move', {'email': nb, 'to': i})
    except OSError:
        engine.reorder(swapped([x.id for x in before], a.id, b.id))
    print('Swapped %s (%d to %d) and %s (%d to %d).' % (na, i, j, nb, j, i))
    _show(engine.accounts())
    return 0


# ---------- switchboard add --as SHORT --at N ----------

def add_wants(args):
    """(short name or None, place or None) from add's own flags, or None when the words are not those."""
    want, rest = {}, list(args)
    while rest:
        flag = rest.pop(0)
        if flag not in ('--as', '--at') or flag in want or not rest:
            return None
        want[flag] = rest.pop(0)
    if '--at' in want and _place(want['--at']) is None:
        return None
    return want.get('--as'), _place(want['--at']) if '--at' in want else None


def _same(a, me):
    return me is not None and ((a.email or '').lower(), a.org_id or '') == me


def _close(tty):
    for f in (getattr(tty, 'reader', None), getattr(tty, 'writer', None)):
        try:
            f.close()
        except (AttributeError, OSError):
            pass


def add_named(args, cli):
    """`switchboard add --as SHORT [--at N]`: the account this run keeps gets the short name and the place.

    Which account: at a terminal, the first one this run keeps that Switchboard did not hold before (add appends a new
    account to the list, so it is the first new one there); several kept, the first, and it is said. With no terminal
    the run keeps only the account Claude Code is on, so that one, new or not: a script signs in with /login and then
    names it, and the page may have kept it already. A name that is taken or can't be one, and a place that does not
    exist, are refused before add starts, so before any sign-in."""
    wants = add_wants(args)
    if wants is None:
        print(ADD)
        return 2
    short, at = wants
    if core.DEMO:
        raise SwitchError('The demo has made-up accounts, so there is nothing to add.')
    before = engine.accounts()
    me = cli.identity()
    tty = cli.open_tty()
    alone = tty is None
    _close(tty)
    mine = next((a for a in before if _same(a, me)), None) if alone else None
    try:
        short = api.check_alias(short, mine.id if mine else None) if short is not None else None
    except api.AccountsError as e:
        raise engine.refused(e)
    last = len(before) + (0 if mine else 1)
    if at is not None and not 1 <= at <= last:
        return _no_place(at, before, last)
    code = cli.add([])
    after = engine.accounts()
    held = set(a.id for a in before)
    new = [a for a in after if a.id not in held]
    target = next((a for a in after if _same(a, me)), None) if alone else (new[0] if new else None)
    if target is None:
        print('No new account was kept, so --as and --at changed nothing.')
        return code
    if short is not None and (target.alias or '') != short:
        engine.set_alias(target.id, short)
    if at is not None:
        now = engine.accounts()
        at = min(at, len(now))
        if _where(now, target.id) != at:
            put(cli, target, at)
    who = _name(target) + (', the first account kept,' if len(new) > 1 and not alone else '')
    if short is not None and at is not None:
        print('%s is %s now, at %d in the list.' % (who, short, at))
    elif short is not None:
        print('%s is %s now.' % (who, short))
    else:
        print('%s is at %d in the list now.' % (who, at))
    return code
