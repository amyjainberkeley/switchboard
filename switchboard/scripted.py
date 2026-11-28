"""The commands a script, a prompt or a status bar calls: `switchboard current` and `switchboard switch --json`.

Each answers in one line, or with --json in one JSON document (report.py, whose rules these follow): standard output
holds the answer and nothing else; anything else the command says goes to standard error.
"""
import json
import sys
import time
from contextlib import contextmanager, redirect_stdout

from . import core, engine, history, report
from .core import SwitchError

PAGE_REASON = 'Picked on the page.'     # the reason the page hands the engine for a switch (server.switch_to)


def _print(doc):
    print(json.dumps(doc, indent=1, ensure_ascii=False))


def _live():
    """(email, organization id) of the login Claude Code is on now, or None. Two accounts can share an email, so a
    move is told by both. On a one-year token kept as an account: (its name as the terminal says it, None, its id),
    whatever account its label names, since that is what Claude Code is on (bearers.py)."""
    from . import bearers
    tok = next((t for t in bearers.rows() if t.get('active')), None)
    if tok is not None:
        return (tok['name'], None, tok['id'])
    me = engine.identity()
    return tuple(me[:2]) if me else None


def _number(me):
    """The number a person types for the account Switchboard keeps for that login (email, organization id), or
    None. A one-year token has none (switch NAME by its name)."""
    if not me or len(me) > 2:
        return None
    try:
        saved = engine.accounts()
    except SwitchError as e:
        core.log('switch: reading the accounts: %r' % e)
        return None
    return next((i for i, a in enumerate(saved, 1) if (a.email or '').lower() == me[0] and (a.org_id or '') == me[1]),
                None)


@contextmanager
def _watch_switch():
    """The reason this process hands the engine for a switch, when the switch is made here and not by the page:
    {'reason': ...}, or {} when the engine was not asked here."""
    seen, real = {}, engine.switch

    def watched(target, source='page', kind='manual', reason=''):
        seen['reason'] = reason
        return real(target, source=source, kind=kind, reason=reason)
    engine.switch = watched
    try:
        yield seen
    finally:
        engine.switch = real


def switch_json(run, words):
    """`switchboard switch [NAME] --json` and `switchboard use NAME --json`: `run(words)` is the plain command, given
    its words without --json; what it prints goes to standard error. Then one document (report.switched). Exit code:
    the plain command's own (1 when it refused, 2 for words it does not take, whose usage line is the refusal)."""
    before, since = _live(), time.time()
    refused, code = None, 0
    said = _Said(sys.stderr)
    try:
        with redirect_stdout(said), _watch_switch() as seen:
            code = run(words)
    except SwitchError as e:
        refused, code = str(e), 1
    if code == 2:
        refused = said.last or 'switchboard switch [NAME|--best] [--json]'
    after = _live()
    reason, at = None, None
    moved = bool(after) and after != before and refused is None
    if moved:
        rec = history.recorded_switch(after[0], since) or {}
        reason = rec.get('reason') or seen.get('reason') or PAGE_REASON
        at = rec.get('t') if isinstance(rec.get('t'), (int, float)) else time.time()
    _print(report.switched(before[0] if before else None, after[0] if after else None, reason=reason, at=at,
                           refused=refused, moved=moved, numbers=(_number(before), _number(after)),
                           on_token=bool(after) and len(after) > 2))
    return code


class _Said(object):
    """Standard error, standing in for standard output: what the plain command prints goes there, and its last line
    is kept (a usage line, when the command was given words it does not take)."""

    def __init__(self, to):
        self.to, self.last, self._line = to, None, ''

    def write(self, text):
        self.to.write(text)
        self._line += text
        lines = [x for x in self._line.split('\n') if x.strip()]
        if lines:
            self.last = lines[-1].strip()
        self._line = self._line[-2000:]
        return len(text)

    def flush(self):
        self.to.flush()

    def isatty(self):
        return False


def use_words(use):
    """`switchboard use NAME` as switch_json runs it: exactly one NAME, else its usage line and 2."""
    def run(words):
        if len(words) != 1:
            print('switchboard use NAME [--json]')
            return 2
        return use(words[0])
    return run


def without_json(args):
    return [a for a in args if a != '--json']


def wrong_words(said, out=None):
    """A command line a --json command does not take: one document on standard output, {'schema', 'refused': the
    sentence (the command's usage line, or what was wrong and then its usage)}, and the sentence on standard error.
    Returns 2, as the plain command does for words it does not take."""
    (out or sys.stdout).write(json.dumps({'schema': report.SCHEMA, 'refused': said}, indent=1, ensure_ascii=False)
                              + '\n')
    sys.stderr.write(said + '\n')
    return 2


def current(args):
    """`switchboard current [--json]`: the whole email of the account Claude Code is on, in one line, exit 0. Signed
    out, or on a login Switchboard does not keep: a sentence on standard error and exit 1 (the email still printed
    when it is known). It reads Claude Code's own config and Switchboard's list of accounts, nothing else: no
    network, no page, and no Keychain unless a one-year token is kept. Then, as `status --json` does, it asks whether
    Claude Code is on one (bearers.rows): on a token kept for an account it names that account, on a token of its own
    that token's name (its label), with kind 'one_year'. On a login no account here is (a token no record owns), it
    names no account, as the page does: the engine's sentence on standard error and exit 1."""
    as_json = '--json' in args
    kind = label = other = None
    if without_json(args) or args.count('--json') > 1:
        if as_json:
            return wrong_words('switchboard current [--json]')
        print('switchboard current [--json]')
        return 2
    if core.DEMO:                               # the demo's made-up account in use, as its page shows it
        from . import server
        a = next((x for x in server.view().get('accounts') or [] if isinstance(x, dict) and x.get('active')), {})
        email, held = a.get('email') or None, bool(a.get('email')) and not a.get('unmanaged')
        number, short = a.get('slot'), a.get('alias')
    else:
        from . import bearers
        kept = bearers.rows()
        tok = next((t for t in kept if t.get('active')), None)
        if kept and tok is None:                # Claude Code's login item was read for those (as status --json does)
            other = _not_kept()
        me = engine.identity()
        email = me[0] if me else None
        held, number, short = False, None, None
        if me or tok:
            try:
                saved = engine.accounts()
            except SwitchError as e:
                core.log('current: reading the accounts: %r' % e)
                saved = []
            for i, a in enumerate(saved, 1):
                if (a.id == tok['for_id']) if tok and tok.get('for_id') else \
                        (me and (a.email or '').lower() == me[0] and (a.org_id or '') == me[1]):
                    email = (a.email or '').lower() if tok else email
                    held, number, short = True, getattr(a, 'order', None) or i, a.alias or None
                    break
        if tok is not None:
            kind = 'one_year'
            if not tok.get('for_id'):           # a token of its own: its own name, as status --json gives it
                email, held, number, short, label = None, True, None, tok.get('alias') or None, \
                    tok.get('label') or tok.get('name')
    if as_json:
        _print(report.current(email, held, number=number, short_name=short, kind=kind, label=label,
                              other=bool(other)))
    elif (email or label) and not other:
        print(email or label)
    if other:
        sys.stderr.write('%s.\n' % other.rstrip('.'))
        return 1
    if not email and not label:
        sys.stderr.write('Claude Code is not signed in. Run /login in Claude Code.\n')
        return 1
    if not held:
        sys.stderr.write('%s is not in Switchboard yet. Add it with: switchboard add\n' % email)
        return 1
    return 0


def _not_kept():
    """The engine's sentence when Claude Code is on a login no account here is and no kept token (api.live state
    'other' with no token of Switchboard's: the page then marks no account in use), else None. Asked only after
    bearers.rows read the live view for a kept token; that view is cached, so this is the same read."""
    from .accounts import api
    try:
        lv = api.live()
    except (api.AccountsError, NotImplementedError, AttributeError) as e:
        core.log('current: reading the live login: %r' % e)
        return None
    if getattr(lv, 'state', None) == 'other' and not getattr(lv, 'bearer', None):
        return lv.why or 'Claude Code is on a login Switchboard does not keep'
    return None

