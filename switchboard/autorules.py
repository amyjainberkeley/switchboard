"""Auto-switch's finer rules for one account at a time, from the terminal and in words: its own switch lines
(`switchboard auto line`), its rank (`switchboard auto rank`), and the best switch made by hand (`switchboard switch
--best`). What they decide is in accounts/policy.py; this module only reads what a person typed and says what
changed, in the voice of the commands beside it (__main__.py, term.py).
"""
from . import core, engine, names, term
from .core import SwitchError

LINE_FORM = 'switchboard auto line NAME [--five-hour N] [--week N]'
LINE_OFF_FORM = 'switchboard auto line NAME off'
RANK_FORM = 'switchboard auto rank NAME N|off'
FLAGS = {'--five-hour': 'session_at', '--week': 'weekly_at'}
WINDOW_WORDS = (('session_at', '5-hour'), ('weekly_at', 'weekly'))


# ---------- words ----------

def lines_words(own):
    """'85% 5-hour · 90% weekly': an account's own lines, in the order the shared ones are said ('' for none)."""
    return term.SEP.join('%s%% %s' % (term.number(own[k]), w) for k, w in WINDOW_WORDS if k in own)


KEPT_OUT_TOO = ' It is kept out of auto-switch, so this does nothing until switchboard auto in %s.'


def line_said(label, own, rule=None):
    """What a change of an account's own lines does now, in one sentence. `own`: its own lines after the change;
    `rule`: its rule in auto-switch ('out': auto-switch never moves to it or from it, so the line does nothing yet)."""
    if not own:
        return '%s is back on the shared lines.' % label
    said = '%s now has its own line%s: %s.' % (label, 's' if len(own) > 1 else '', lines_words(own))
    return said + (KEPT_OUT_TOO % _typed(label) if rule == 'out' else '')


def rank_said(label, rank, rule=None):
    """What a change of an account's rank does now, in one sentence; for an account kept out or used last, what is
    true for it (policy: Use last and Keep out hold whatever the ranks)."""
    if rank is None:
        return '%s has no rank now: auto-switch uses it after every ranked account.' % label
    if rule == 'out':
        return '%s is rank %d now.%s' % (label, rank, KEPT_OUT_TOO % _typed(label))
    if rule == 'last':
        return '%s is rank %d now; it is used last, so it still comes after every account that is not.' % (label, rank)
    return '%s is rank %d now: auto-switch lands there before any account with a later rank.' % (label, rank)


def _typed(label):
    """An account's name as one word for a shell (a name with its organization has spaces)."""
    return label if ' ' not in label else "'%s'" % label.replace("'", "'\\''")


def rule_of(auto, account_id):
    """'last', 'out' or None: an account's rule in auto-switch, from the auto settings."""
    rules = auto.get('rules') if isinstance(auto.get('rules'), dict) else {}
    return rules.get(account_id)


def lines_now(label, auto, account_id):
    """`switchboard auto line NAME` alone: 'maya@ivy.example switches at 85% 5-hour (its own) · 95% weekly.'"""
    own = own_lines_of(auto, account_id)
    shared = {'session_at': auto.get('session_at') if term._num(auto.get('session_at')) else 90,
              'weekly_at': auto.get('weekly_at') if term._num(auto.get('weekly_at')) else 95}
    if not own:
        return '%s switches at the shared lines: %s.' % (label, lines_words(shared))
    return '%s switches at %s.' % (label, term.SEP.join('%s%% %s%s' % (term.number(own.get(k, shared[k])), w,
                                                                       ' (its own)' if k in own else '')
                                                         for k, w in WINDOW_WORDS))


def account_words(own, rank):
    """What `switchboard auto` adds for an account's own lines and rank ('rank 1', 'its own line 85% 5-hour'), or ''
    when it has neither."""
    parts = (['rank %d' % rank] if rank is not None else []) + \
        (['its own line%s %s' % ('s' if len(own) > 1 else '', lines_words(own))] if own else [])
    return ', '.join(parts)


def own_lines_of(auto, account_id):
    """The account's own lines from the auto settings ({} for none)."""
    own = (auto.get('own_lines') or {}) if isinstance(auto.get('own_lines'), dict) else {}
    v = own.get(account_id)
    return dict((k, v[k]) for k, _ in WINDOW_WORDS if isinstance(v, dict) and term._num(v.get(k)))


def rank_of(auto, account_id):
    ranks = auto.get('ranks') if isinstance(auto.get('ranks'), dict) else {}
    r = ranks.get(account_id)
    return r if isinstance(r, int) and not isinstance(r, bool) else None


def account_lines(accounts, auto):
    """`switchboard auto`, after its line: one line for each account with a rule, its own lines or a rank, in the
    person's order: 'maya@ivy.example is used last, rank 2.' `accounts`: the engine's (api.Account), every one."""
    rules = auto.get('rules') if isinstance(auto.get('rules'), dict) else {}
    named = names.all_of(accounts)
    out = []
    for a in accounts:
        rule = term.RULE_NOTES.get(rules.get(a.id))
        more = account_words(own_lines_of(auto, a.id), rank_of(auto, a.id))
        if rule:
            out.append('%s is %s%s.' % (named.get(a.id) or a.id, rule, ', ' + more if more else ''))
        elif more:
            out.append('%s: %s.' % (named.get(a.id) or a.id, more))
    return out


# ---------- the commands ----------

def _number(text, flag):
    words = '%s takes a number from %g to %g.' % (flag, engine.LINE_MIN, engine.LINE_MAX)     # autocheck.line_words
    try:
        return engine.check_line(float(text))
    except (TypeError, ValueError, SwitchError):
        raise SwitchError(words)


def parse_line(args):
    """`auto line NAME ...`'s words after NAME, and whether they make sense: ({'session_at': N, 'weekly_at': N} (each
    only when given), True), (None, True) for `off`, ({}, True) to say its lines; else (None, False). A number is
    checked as the shared lines are (engine.check_line)."""
    if args == ['off']:
        return None, True
    got = {}
    rest = list(args)
    while rest:
        flag = rest.pop(0)
        if flag not in FLAGS or FLAGS[flag] in got or not rest:
            return None, False
        got[FLAGS[flag]] = _number(rest.pop(0), flag)
    return got, True


def auto_line(args, ask_page, outside_the_page, history):
    """`switchboard auto line NAME [--five-hour N] [--week N]`: that account's own switch lines, through the running
    page (POST /api/auto) when it answers, else here; `off` puts it back on the shared lines; with neither flag, its
    lines are said. Then one sentence (line_said)."""
    if not args or args[0].startswith('-') or args[0] == 'off':
        print('\n'.join((LINE_FORM, LINE_OFF_FORM)))
        return 2
    wanted, ok = parse_line(args[1:])
    if not ok:
        print('\n'.join((LINE_FORM, LINE_OFF_FORM)))
        return 2
    if core.DEMO:
        raise SwitchError("The demo has made-up accounts; give an account its own lines on its page.")
    from . import bearers
    a = bearers.sign_in(args[0], 'auto line')       # engine.find only; a token's or a key's name: for sign-ins
    label = names.of_account(a, engine.accounts())
    if wanted == {}:
        print(lines_now(label, engine.auto_settings(), a.id))
        return 0
    lines = wanted
    try:
        ask_page('/api/auto', {'account': a.id, 'own_lines': lines})
    except OSError:
        with outside_the_page():
            engine.set_auto(line=(a.id, lines))
        auto = engine.auto_settings()
        history.note(line_said(label, own_lines_of(auto, a.id), rule_of(auto, a.id)), h='setting', by='you')
    auto = engine.auto_settings()
    print(line_said(label, own_lines_of(auto, a.id), rule_of(auto, a.id)))
    return 0


def auto_rank(args, ask_page, outside_the_page, history):
    """`switchboard auto rank NAME N|off`: that account's rank (1 is used first), through the running page when it
    answers, else here. Then one sentence (rank_said)."""
    if len(args) != 2:
        print(RANK_FORM)
        return 2
    if args[1] == 'off':
        n = None
    else:
        try:
            n = int(args[1])
        except ValueError:
            raise SwitchError(engine.RANK_WORDS)
        engine.check_rank((None, n))
    if core.DEMO:
        raise SwitchError("The demo has made-up accounts; rank an account on its page.")
    from . import bearers
    a = bearers.sign_in(args[0], 'auto rank')       # engine.find only; a token's or a key's name: for sign-ins
    label = names.of_account(a, engine.accounts())
    try:
        ask_page('/api/auto', {'account': a.id, 'rank': n})
    except OSError:
        with outside_the_page():
            engine.set_auto(rank=(a.id, n))
        history.note(rank_said(label, n, rule_of(engine.auto_settings(), a.id)), h='setting', by='you')
    print(rank_said(label, n, rule_of(engine.auto_settings(), a.id)))
    return 0


def best_said(decision, email):
    """The one line `switchboard switch --best` prints once Claude Code is on the account."""
    return 'Claude Code is on %s now: %s.' % (email, decision.reason.replace(email, 'it', 1))


def switch_best(current_view, switch):
    """`switchboard switch --best`: move Claude Code now to the account auto-switch would land on (engine.best), the
    same move as `switchboard switch NAME` (`switch(email, view)`: through the running page when it answers). One line
    says which account and why. When there is none, a refusal that says why and when the first account refills (exit
    1), and nothing moves."""
    d = engine.best()
    if d.action != 'switch' or not d.target:
        raise SwitchError(d.reason)
    a = engine.find(d.target)
    name = names.of_account(a, engine.accounts())      # the page's name for it: two accounts may share an email
    switch(name, current_view())
    print(best_said(d, name))
    return 0
