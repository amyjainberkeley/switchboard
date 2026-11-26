"""One-year tokens and API keys kept as accounts, as the terminal, the page and scripts see them.

The engine keeps them (accounts/bearer.py, reached through accounts.api); this module is the command
`switchboard add --token`, the switch, removal and rule onto one by its name, and the words each surface shows.

What a person sees, in a few words and never a bar without a number:
- a token kept for an account: that account's own row says it also has a one-year token, and when it was added;
- a token with a name of its own: a row of its own, "one-year token · model requests only; its limits are not known";
- an API key: a row of its own, "API key · billed per token by Anthropic".
The token carries no end date, so only the day Switchboard kept it is shown ("added Nov 25"), never a guess.
"""
import getpass
import os
import sys

from . import core, engine, term
from .accounts import api, bearer
from .accounts.bearer import A_WHAT, AFTER_SWITCH, CANNOT, KEY, KEY_SWITCH, NAME_IT, REFUSED, TOKEN, WHAT, kind_of
from .core import SwitchError

USAGE = 'switchboard add --token [-] [--for NAME | --label WORD] [--as SHORT]'
KINDS_JSON = {TOKEN: 'one_year', KEY: 'api_key'}           # status --json's word for each; a sign-in is 'sign_in'
MOST = 1024                 # characters of standard input read for one secret: twice the longest bearer.kind_of takes
TOO_LONG = 'That is longer than any one-year token or API key (at most %d characters). Nothing was kept.' % MOST
RULE_SAID = {'last': 'Auto-switch keeps %s for last: it never moves Claude Code there while an account has room.',
             'out': 'Auto-switch now leaves %s alone: it never moves Claude Code there, or away from it.'}


# ---------- reading ----------

def rows():
    """Every kept token and key (api.kept_tokens), or [] in the demo or when the engine can't be asked."""
    if core.DEMO:
        return []
    try:
        return list(api.kept_tokens() or [])
    except (api.AccountsError, NotImplementedError, AttributeError) as e:
        core.log('reading the kept tokens: %r' % e)
        return []


def find(name):
    """A kept token or key by its id, short name or label, or None."""
    if core.DEMO:
        return None
    try:
        return api.find_token(str(name))
    except (api.AccountsError, NotImplementedError, AttributeError):
        return None


def sign_in(name, command):
    """The saved account `name` names (engine.find), for a command that works on sign-ins only. A name that only a
    kept token or key has is refused in one line that says the command is for sign-ins; any other name no account has
    gets engine.find's own sentence."""
    try:
        return engine.find(name)
    except SwitchError:
        t = find(name)
        if t is None:
            raise
        raise SwitchError(bearer.sign_ins_only(t, name, command))


def added_words(t):
    """'added Nov 25', or '' when the day is not known."""
    return 'added %s' % term.day(t) if term._when_ok(t) else ''


def words(t):
    """The few words of a row of its own: what it is and what it cannot do, its rule, when it was added."""
    out = ['in use'] if t.get('active') else []
    out += [WHAT[t['kind']], CANNOT[t['kind']]]
    if added_words(t.get('added_at')):
        out.append(added_words(t.get('added_at')))
    out.append(term.RULE_NOTES['last' if t.get('rule') == 'last' else 'out'])
    return out


def on_words(view):
    """What Claude Code is on when that is a kept token: 'the one-year token for a@example.com', else None."""
    t = next((x for x in view.get('tokens') or [] if x.get('active')), None)
    return 'the %s for %s' % (WHAT[t['kind']], t['name']) if t else None


# ---------- the terminal ----------

def term_lines(view, colour=False):
    """The rows of their own, after the accounts in `switchboard` and `switchboard status`: a blank line, then each
    one's name and its words on one line."""
    own = [t for t in view.get('tokens') or [] if not t.get('for')]
    out = []
    for t in own:
        segs = [('   ', None), (t['name'], term.BOLD), ('  ', None)]
        for i, w in enumerate(words(t)):
            segs += ([(term.SEP, term.DIM)] if i else []) + [(w, term.BLUE if w == 'in use' else term.DIM)]
        out += ['', term.paint(segs, colour)]
    return out


def ask(prompt):
    """One answer from the person at this terminal ('' when there is none)."""
    try:
        return input(prompt).strip()
    except EOFError:
        return ''


def add_command(args):
    """`switchboard add --token [-] [--for NAME | --label WORD] [--as SHORT]`: keep a one-year token or an API key
    as an account. The secret is asked for without showing it, or read from standard input with `-`; never from an
    argument or the environment. Sends nothing to Anthropic. Exit 2 for a wrong command line."""
    rest = [a for a in args if a != '--token']
    opts, stdin = {}, False
    while rest:
        word = rest.pop(0)
        if word == '-':
            stdin = True
        elif word in ('--for', '--label', '--as') and rest and not rest[0].startswith('-') and word not in opts:
            opts[word] = rest.pop(0)
        else:
            print('Unknown word: %s.' % word, file=sys.stderr)
            print(USAGE, file=sys.stderr)
            return 2
    if '--for' in opts and '--label' in opts:
        print('Give --for or --label, not both.', file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2
    if core.DEMO:
        raise SwitchError('The demo has made-up accounts, so there is nothing to add.')
    at_terminal = _terminal()
    if stdin:
        text = sys.stdin.readline(MOST + 2)                 # one line, and never more than this (a line end: 2 more)
        if len(text.rstrip('\r\n')) > MOST:
            text = None
            raise SwitchError(TOO_LONG)
    elif at_terminal:
        text = getpass.getpass('One-year token or API key (not shown): ')
    else:
        print('Give the token on standard input: switchboard add --token -', file=sys.stderr)
        return 2
    kind = kind_of(text)
    if kind is None:
        text = None
        raise SwitchError(REFUSED)
    if '--for' not in opts and '--label' not in opts:
        if stdin or not at_terminal:           # standard input is spent on the token: nothing to ask with
            text = None
            print(NAME_IT, file=sys.stderr)
            return 2
        opts.update(_which(kind))
    try:
        entry, what = engine._call(api.add_token, text, for_target=opts.get('--for'), label=opts.get('--label'),
                                   alias=opts.get('--as'))
    finally:
        text = None
    print(added_line(entry, what))
    return 0


def _terminal():
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _which(kind):
    """Ask which kept account a token is for, or a name of its own (a key always gets a name of its own)."""
    if kind == KEY:
        return {'--label': ask('A name for this API key (letters, digits, dots, dashes): ')}
    saved = engine.accounts()
    for i, a in enumerate(saved, 1):
        print('%d  %s' % (i, a.email))
    said = ask('Which account is this one-year token for? Its number or email, or a new name for it: ')
    if said and any(said.lower() in (str(i), (a.email or '').lower(), (a.alias or '').lower())
                    for i, a in enumerate(saved, 1)):
        return {'--for': said}
    return {'--label': said}


def added_line(entry, what):
    """One line for what `add --token` did."""
    name, kind = entry.get('name') or '', entry.get('kind') or TOKEN
    if what == 'same':
        return 'That %s for %s is kept already.' % (WHAT[kind], name)
    head = 'Kept the new %s for %s in place of the old one' % (WHAT[kind], name) if what == 'replaced' else \
        'Kept %s for %s' % (A_WHAT[kind], name)
    return '%s (ends in %s) in your Keychain; auto-switch keeps it out.' % (head, entry.get('last4') or '?')


def switch_to(t):
    """`switchboard switch NAME` onto a kept token (an API key: how to run one terminal on it, exit 1)."""
    if t['kind'] == KEY:
        print(KEY_SWITCH % t['name'], file=sys.stderr)
        return 1
    engine.switch(t['id'], source='cli', reason='Picked in a terminal.')
    print(term.now_on('the %s for %s' % (WHAT[TOKEN], t['name']), term.colour_ok(sys.stdout, os.environ)))
    print(AFTER_SWITCH[TOKEN])
    return 0


def remove(t, yes=False):
    """`switchboard remove NAME [--yes]` for a kept token or key."""
    if not yes:
        if not (_terminal() and sys.stdout.isatty()):
            raise SwitchError('Add --yes to remove it without being asked.')
        if ask('Forget the %s for %s? Its Keychain item goes too. [y/N] ' % (WHAT[t['kind']], t['name'])).lower() \
                not in ('y', 'yes'):
            print('Nothing removed.')
            return 1
    engine._call(api.remove_token, t['id'])          # a token Claude Code is on is refused: switch away first
    print('Removed the %s for %s.' % (WHAT[t['kind']], t['name']))
    return 0


def set_rule(name, rule):
    """A kept token's or key's rule (`switchboard auto last|out|in NAME`, the page's POST): the sentence that says
    what happens now. A key, or `in` for a token, is refused in one line."""
    who = engine._call(api.set_token_rule, str(name), rule)
    return RULE_SAID[rule] % who


# ---------- status --json ----------

def report_rows(view, when):
    """Each row of its own as `status --json` gives an account (report.account's keys, and kind, label, added_at).
    A token's limits are not known (null percents, a week with no pace); an API key has no plan limits (null). Neither
    has a spend limit, and neither is read on another Mac, so a script reads every account the same way."""
    out = []
    for t in view.get('tokens') or []:
        if t.get('for'):
            continue
        unknown = {'used_percent': None, 'refills_at': None}
        out.append({'number': None, 'email': None, 'short_name': t.get('alias') or None, 'plan': None,
                    'organization': None, 'in_use': bool(t.get('active')), 'next': False, 'needs_sign_in': False,
                    'in_switchboard': True, 'sign_in_ends_by': None,
                    'limits': {'five_hour': dict(unknown), 'week': dict(unknown, pace=None), 'models': [],
                               'spend': None} if t['kind'] == TOKEN
                    else {'five_hour': None, 'week': None, 'models': [], 'spend': None},
                    'checked_at': None, 'from_another_mac': False, 'kind': KINDS_JSON[t['kind']],
                    'label': t.get('label') or t['name'], 'added_at': when(t.get('added_at')),
                    'one_year_added_at': None})
    return out
