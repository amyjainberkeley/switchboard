"""Every setting of Switchboard as one table, and `switchboard config`, which reads and sets them from a terminal.

A row is one setting: its key, the page's group it shows under, its type, its default, its range or choices, one line
of words, how its value is read, and how it is set. Keys are the ones the page's Settings view shows (hover a row's
title): where a setting is a key of Switchboard's own settings file, the same key, so a person reads one word on the
page, in the file and in the terminal; a setting kept elsewhere gets a plain word of its own (`statusline`).

How a row is set:
- `post`: through the page's own request (the path and body a press on the page sends), to the running page when it
  answers, else to the same handler in this process (server.act), so the value is checked by the page's own code and
  applied the way a press applies it. A row with `page_only` runs inside the page: without the page it is refused.
- `command`: a setting with a command of its own (the four kept in Claude Code's settings, and a few more): config
  names that command and never sets it.
- `where`: set on the page, or by hand in the settings file: config names the place and never sets it.

No secret is a setting: the settings file's `key` (the links' secret) is Switchboard's own bookkeeping, never listed
and never printed. Adding a setting is one row in ROWS; bookkeeping keys the code reads go in OWN_KEYS.
"""
import difflib
import importlib
import json
import os
import re
import sys
from contextlib import contextmanager

from . import autorules, browser, core, engine, gateway, names, term
from .accounts import policy
from .core import SwitchError

SCHEMA = 1
GROUPS = (('switching', 'Switching'), ('working', 'While Claude works'), ('code', 'In Claude Code'),
          ('links', 'Links'), ('keys', 'Keys'), ('install', 'This install'))
ON_WORDS = {'on': True, 'true': True, 'yes': True, 'off': False, 'false': False, 'no': False}
USAGE = 'switchboard config [get KEY | set KEY VALUE | unset KEY | path] [--json] [--changed] [--as-commands]'


def settings_file():
    return core.data_path('settings.json')


def claude_settings_file():
    return os.path.join(core.claude_dir(), 'settings.json')


class Row(object):
    """One setting. kind: 'on-off', 'number', 'choice', 'text' or 'accounts' (one value per account)."""

    def __init__(self, key, group, kind, default, words, read, post=None, page_only=False, command=None, where=None,
                 choices=None, low=None, high=None, page=None, page_why=None, file_key=None, as_command=None,
                 setter=None, each=None):
        self.key, self.group, self.kind, self.default, self.words, self.read = key, group, kind, default, words, read
        self.post, self.page_only, self.command, self.where = post, page_only, command, where
        self.setter = setter                        # a key of the settings file set by its own module: setter(value)
        self.each = each                            # 'accounts': one account's value in words
        self.choices, self.low, self.high = choices, low, high
        self.page = key if page is None and page_why is None else page      # the key the page's row shows
        self.page_why = page_why                    # why the page shows no key for it
        self.file_key = file_key                    # its top-level key in Switchboard's settings file, if it has one
        self.as_command = as_command                # its value as the line of its own command, for --as-commands

    def set_with(self):
        """How it is set, in words a person can follow."""
        if self.post or self.setter:
            return 'switchboard config set %s VALUE' % self.key
        return self.command or self.where


# ---------- reading the values: each fact asked once per command ----------

class Facts(object):
    """What the rows read, each asked at most once."""

    def __init__(self):
        self._got = {}

    def _once(self, name, fn):
        if name not in self._got:
            try:
                self._got[name] = fn()
            except Exception as e:  # noqa: BLE001 - a fact that can't be read is a value not known, never a stop
                core.log('config: reading %s: %r' % (name, e))
                self._got[name] = None
        return self._got[name]

    def auto(self):
        return self._once('auto', engine.auto_settings) or {}

    def file(self):
        return core.settings()

    def accounts(self):
        return self._once('accounts', engine.accounts) or []

    def helpers(self):
        from . import server
        return self._once('helpers', server.helper_settings) or {}

    def mac(self):
        from . import server
        return self._once('mac', server.mac_facts) or {}

    def statusline(self):
        from . import server
        return (self._once('statusline', server.statusline_status) or {}).get('on')

    def models(self):
        from . import server
        return (self._once('models', server.models_view) or {}).get('enabled')

    def models_helpers(self):
        from . import server
        return ((self._once('models', server.models_view) or {}).get('helpers') or {}).get('enabled')

    def hooks(self):
        from . import session_hooks
        return (self._once('hooks', session_hooks.status) or {}).get('in')

    def pack(self, name):
        from . import claudecode
        view = {'house': claudecode.house_view, 'codemap': claudecode.codemap_view,
                'claudemem': claudecode.claudemem_view}[name]
        got = self._once(name, view)
        return None if got is None else bool(got.get('on'))

    def links(self):
        from . import linkapp
        got = self._once('links', linkapp.status)
        return None if got is None else bool(got.get('on'))


def _per_account(f, field):
    """{account's name: its value} of one of auto-switch's per-account settings ({account id: value}), for each
    account that has one, in the person's order. The name is the page's (names.of_account): two accounts with one
    email are two names."""
    got = f.auto().get(field)
    got = got if isinstance(got, dict) else {}
    accounts = f.accounts()
    named = names.all_of(accounts)
    return dict((named[a.id], got[a.id]) for a in accounts if a.id in got)


def _rules(f):
    """{account: 'last' | 'out'} for each account with a rule, in the person's order."""
    return _per_account(f, 'rules')


def _own_lines(f):
    """{account: {'session_at' and/or 'weekly_at': its own line}} for each account with a line of its own."""
    own = _per_account(f, 'own_lines')
    return dict((k, dict((w, v[w]) for w in ('session_at', 'weekly_at') if w in v))
                for k, v in own.items() if isinstance(v, dict) and v)


def _ranks(f):
    """{account: its rank, 1 to 99} for each ranked account."""
    return dict((k, v) for k, v in _per_account(f, 'ranks').items() if isinstance(v, int) and not isinstance(v, bool))


def _models(f):
    """The models whose own week counts, as `config set auto_policy.model` takes them: 'Fable' or 'Fable,Opus'."""
    a = f.auto()
    models = [m for m in a.get('models') or [] if isinstance(m, str) and m.strip()]
    return ','.join(models) if models else a.get('model')


def _typed(name):
    """An account's name as one word a shell takes back (a name with its organization has spaces)."""
    return name if BARE.match(name) else "'%s'" % name.replace("'", "'\\''")


def _line_command(name, own):
    flags = ''.join(' %s %s' % (flag, term.number(own[k])) for flag, k in (('--five-hour', 'session_at'),
                                                                             ('--week', 'weekly_at')) if k in own)
    return 'switchboard auto line %s%s' % (_typed(name), flags)


def _of(module, fn):
    """A function of the module that keeps a setting, imported only when a row reads or sets it: each setting is
    read and checked by its own module's code, never a copy of it here."""
    def call(*args):
        return getattr(importlib.import_module('switchboard.' + module), fn)(*args)
    return call


def _read_of(module, fn):
    """A row's read (it is given the Facts) by the module's own reader, which takes nothing."""
    read = _of(module, fn)
    return lambda f: read()


def _home_profile(f):
    return core.read_text(core.data_path('home-profile.txt')) or None


def _file_value(name, default):
    return lambda f: f.file().get(name, default)


def _on_off(cmd):
    return lambda v: 'switchboard %s %s' % (cmd, 'on' if v else 'off')


# The page's words for each row are its own (web/index.html, the Settings view's GROUPS): one voice in both places.
ROWS = (
    Row('auto', 'switching', 'on-off', False,
        'Moves Claude Code to another account before it reaches a line. Off, the accounts are still read.',
        lambda f: f.auto().get('enabled'), post=('/api/auto', 'enabled'), file_key='auto',
        as_command=_on_off('auto')),
    Row('auto_policy.session_at', 'switching', 'number', engine.DEFAULT_SESSION,
        'The 5-hour line: auto-switch moves Claude Code on when the 5-hour limit reaches it.',
        lambda f: f.auto().get('session_at'), post=('/api/auto', 'session_at'), low=engine.LINE_MIN,
        high=engine.LINE_MAX, file_key='auto_policy'),
    Row('auto_policy.weekly_at', 'switching', 'number', engine.DEFAULT_WEEKLY,
        'The weekly line: auto-switch moves Claude Code on when the weekly limit reaches it.',
        lambda f: f.auto().get('weekly_at'), post=('/api/auto', 'weekly_at'), low=engine.LINE_MIN,
        high=engine.LINE_MAX, file_key='auto_policy'),
    Row('auto_policy.model_at', 'switching', 'number', engine.DEFAULT_MODEL_LINE,
        "The line for the model counted below, at its own weekly limit.",
        lambda f: f.auto().get('model_at'), post=('/api/auto', 'model_at'), low=engine.LINE_MIN,
        high=engine.LINE_MAX, file_key='auto_policy'),
    Row('auto_policy.model', 'switching', 'text', None,
        "Also switch at a model's own weekly limit: one name or several with commas, like Fable or Fable,Opus "
        "(none counts no model).",
        _models, post=('/api/auto', 'model'), file_key='auto_policy'),
    Row('auto_policy.strategy', 'switching', 'choice', engine.DEFAULT_STRATEGY,
        'Refills first can move early, to the account that refills soonest. Most room waits for a line. Stay, then '
        'refills first waits for a line, then goes to what refills first.',
        lambda f: f.auto().get('strategy'), post=('/api/auto', 'strategy'), choices=engine.STRATEGIES,
        file_key='auto_policy'),
    Row('auto_policy.rules', 'switching', 'accounts', {},
        'Each account: used as usual, used last, or kept out of auto-switch.',
        _rules, command='switchboard auto last|out|in NAME', file_key='auto_policy',
        as_command=lambda v: ['switchboard auto %s %s' % (rule, _typed(name)) for name, rule in (v or {}).items()]),
    Row('auto_policy.own_lines', 'switching', 'accounts', {},
        "An account's own switch lines, in place of the shared ones for that account.",
        _own_lines, command='switchboard auto line NAME [--five-hour N] [--week N]', file_key='auto_policy',
        page_why="the page sets it in each account's row of Use last and Keep out", each=autorules.lines_words,
        as_command=lambda v: [_line_command(name, own) for name, own in (v or {}).items()]),
    Row('auto_policy.ranks', 'switching', 'accounts', {},
        'Ranks: among the accounts a switch may land on, rank 1 is used first.',
        _ranks, command='switchboard auto rank NAME N|off', file_key='auto_policy',
        page_why="the page sets it in each account's row of Use last and Keep out", each=lambda n: 'rank %d' % n,
        as_command=lambda v: ['switchboard auto rank %s %d' % (_typed(name), n) for name, n in (v or {}).items()]),
    Row('auto_policy.sign_lines', 'switching', 'on-off', policy.Settings.sign_lines,
        "Move before Claude Code's usage sign shows (60% of the 5-hour window, 65% of the week). Each move makes open "
        "chats read their conversation again.",
        lambda f: f.auto().get('sign_lines'), post=('/api/auto', 'sign_lines'), file_key='auto_policy',
        page_why='the page has no row for it yet'),
    Row('auto_policy.pick_hold', 'switching', 'choice', policy.Settings.pick_hold,
        'When you pick an account yourself, automatic switching stays there: 12 hours for a better refill, 2 hours '
        'for a model or the usage sign (default), until a limit makes it move (until_out), or no hold (off).',
        lambda f: f.auto().get('pick_hold'), post=('/api/auto', 'pick_hold'), choices=policy.PICK_HOLDS,
        file_key='auto_policy', page_why='the page has no row for it yet'),
    Row('awake', 'working', 'on-off', True, 'Keep the Mac awake: held while Claude Code works, let go 2 minutes '
        'after it stops.', lambda f: f.helpers().get('awake'), post=('/api/awake', 'on'), file_key='awake'),
    Row('notify', 'working', 'on-off', False, 'Notifications: a switch, a limit near or refilled, needs you, and done '
        'when notify_done is on.', lambda f: f.mac().get('notify'), command='switchboard notify on|off',
        file_key='notify', as_command=_on_off('notify')),
    Row('notify_done', 'working', 'on-off', False, 'A notification when a turn of a minute or more ends, while '
        'Notifications are on.', _read_of('notify_hooks', 'done_on'), setter=_of('keep', 'notify_done_by_you'),
        page_why='the page has no row for it: switchboard notify done on|off sets it', file_key='notify_done',
        as_command=lambda v: 'switchboard notify done %s' % ('on' if v else 'off')),
    Row('statusline', 'code', 'on-off', False, 'The account, % left and refill time under each Claude Code window.',
        lambda f: f.statusline(), command='switchboard statusline on|off', page='statusLine',
        as_command=_on_off('statusline')),
    Row('models', 'code', 'on-off', False, 'Models by the job: four helpers, each on the model that fits its job '
        '(lookups on Haiku; reading, review and checks on Sonnet).', lambda f: f.models_helpers(),
        command='switchboard models on|off', page='switchboard-*.md', as_command=_on_off('models')),
    Row('models_sonnet', 'code', 'on-off', False, 'Sonnet for helpers: helpers and workflow steps that name no model '
        'run on Sonnet. The chat keeps its own.', lambda f: f.models(), command='switchboard models sonnet on|off',
        page='env.CLAUDE_CODE_SUBAGENT_MODEL', as_command=_on_off('models sonnet')),
    Row('hooks', 'code', 'on-off', False, 'The session hooks: they tell an open Claude Code session what a switch '
        'means for it.', lambda f: f.hooks(), command='switchboard hooks on|off',
        page_why='the page has no row for them: they go in with the first run, and this command changes them',
        as_command=_on_off('hooks')),
    Row('house_rules', 'code', 'on-off', False, 'A short block of working rules at the top of CLAUDE.md.',
        lambda f: f.pack('house'), where='on the page (Settings, In Claude Code)', page='CLAUDE.md'),
    Row('code_map', 'code', 'on-off', False, 'Claude Code asks codegraph before it searches files.',
        lambda f: f.pack('codemap'), where='on the page (Settings, In Claude Code)', page='CLAUDE.md'),
    Row('claude_mem', 'code', 'on-off', False, 'Notes from your chats, for Claude Code to look up.',
        lambda f: f.pack('claudemem'), where='on the page (Settings, In Claude Code)',
        page_why='the page shows the version its switch installs, not a key'),
    Row('run_history', 'code', 'choice', 'shared', 'Where a switchboard run keeps its chat: with your other chats '
        "(shared), or apart, in the run's own folder, gone when the run ends.",
        _read_of('foldercmd', 'history_setting'), setter=_of('foldercmd', 'set_history'), choices=('shared', 'apart'),
        page_why='set with switchboard config', file_key='run_history'),
    Row('links', 'links', 'on-off', False, 'Links you click anywhere open as their owner (Switchboard Links is the '
        'default browser).', lambda f: f.links(), command='switchboard links on|off',
        page_why='the page says it at the top of Links, with its own button', as_command=_on_off('links')),
    Row('home_profile', 'links', 'text', None, 'Ordinary links, not artifacts, open in this browser profile (none: '
        'the last window).', _home_profile, where='on the page (Settings, Links)', page='home-profile.txt'),
    Row('browser', 'links', 'choice', None, 'Which browser holds the profiles (none: the first one found).',
        _file_value('browser', None), where='by hand in the settings file', choices=tuple(browser.BROWSERS),
        page_why='set by hand in the settings file', file_key='browser'),
    Row('gateway_on', 'keys', 'on-off', False, 'When every account is out, Claude Code runs on your API keys, in one '
        'terminal.', lambda f: f.file().get('gateway_on') is True, post=('/api/gateway', 'on'), page_only=True,
        file_key='gateway_on'),
    Row('gateway_port', 'keys', 'number', gateway.DEFAULT_PORT, "The keys gateway's port on this Mac.",
        _file_value('gateway_port', gateway.DEFAULT_PORT), where='by hand in the settings file', low=1024, high=65535,
        page_why='set by hand in the settings file', file_key='gateway_port'),
    Row('port', 'install', 'number', 7799, "The page's port on this Mac.", _file_value('port', 7799),
        command='switchboard update --port N', low=1024, high=65535,
        page_why="the page runs on it; switchboard update --port N moves the page's background job too",
        file_key='port', as_command=lambda v: 'switchboard update --port %d' % v),
    Row('menubar_position', 'install', 'on-off', True, 'The first time the menu bar item is made, it asks to sit '
        'beside the clock (off: macOS places it).', lambda f: f.file().get('menubar_position') is not False,
        where='by hand in the settings file', page_why='set by hand in the settings file',
        file_key='menubar_position'),
    Row('update_check', 'install', 'choice', 'off', 'Look for a newer version once a day (daily), or only when you ask '
        'with switchboard update --check (off).', _read_of('newer', 'check_setting'), setter=_of('newer', 'set_check'),
        choices=('off', 'daily'), page_why='set with switchboard config', file_key='update_check'),
    Row('network_trust', 'install', 'choice', 'mac', "What secure connections trust: Python's certificates and this "
        "Mac's trusted ones (mac), or Python's alone (python).", _read_of('trust', 'mode'),
        setter=_of('trust', 'set_mode'), choices=('mac', 'python'), page_why='set with switchboard config',
        file_key='network_trust'),
)
BY_KEY = dict((r.key, r) for r in ROWS)

# Keys of the settings file the code reads that are Switchboard's own bookkeeping (what it did by itself, what it
# said once, what it found), not settings: never listed, and never "a key nothing reads". `key` is the links' secret.
OWN_KEYS = ('key', 'let_go', 'auto_on_said', 'auto_set_by_you', 'notify_set_by_you', 'notify_by_itself', 'notify_seen',
            'links_wanted', 'links_by_itself', 'links_forwarded', 'session_hooks', 'session_hooks_set_by_you',
            'session_hooks_by_itself', 'statusline_set_by_you', 'statusline_by_itself', 'statusline_theirs_said',
            'setup_hidden', 'claude_path', 'desktop_note_said', 'skill', 'skill_set_by_you', 'skill_by_itself',
            'skill_theirs_said')
# read by policy.settings_from
POLICY_KEYS = ('session_at', 'weekly_at', 'model_at', 'model', 'strategy', 'rules', 'own_lines', 'ranks',
               'stay_signed_in', 'sign_lines', 'pick_hold', 'sign_session_at', 'sign_weekly_at', 'sign_model_at')
# read by accounts.putback: Claude Code's plain login file kept after a put-back (the hold) and as a standing copy
SIGNIN_FILE_KEYS = ('hold_signin_file', 'keep_signin_file')


# ---------- values as words ----------

def shown(row, v):
    """A value as the terminal says it."""
    if v is None:
        return 'none' if row.kind in ('text', 'choice') else 'not known'
    if row.kind == 'on-off':
        return 'on' if v else 'off'
    if row.kind == 'number':
        return '%g' % v
    if row.kind == 'accounts':
        return ', '.join('%s %s' % (e, row.each(r) if row.each else r) for e, r in v.items()) or 'none'
    return str(v)


def as_json(row, v):
    if row.kind == 'on-off':
        return None if v is None else bool(v)
    if row.kind == 'number' and isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def same(row, a, b):
    if row.kind == 'on-off':
        return a is not None and b is not None and bool(a) == bool(b)
    if row.kind == 'accounts':
        return (a or {}) == (b or {})
    if row.kind == 'number':
        return a is not None and b is not None and float(a) == float(b)
    return (a or None) == (b or None)


def value_of(row, facts):
    try:
        return row.read(facts)
    except Exception as e:  # noqa: BLE001 - a value that can't be read is not known
        core.log('config: reading %s: %r' % (row.key, e))
        return None


def changed(row, v):
    """True when the value is known and differs from the default."""
    if v is None and row.kind not in ('text', 'choice'):
        return False
    return not same(row, v, row.default)


# ---------- a key nothing reads ----------

def nearest(key, among=None):
    """The real key closest to `key`, or None."""
    among = list(among if among is not None else [r.key for r in ROWS] + list(OWN_KEYS))
    found = difflib.get_close_matches(key, among, n=1, cutoff=0.6)
    return found[0] if found else None


def not_read(s=None):
    """[(key, nearest real key or None)] for each key in the settings file that no code reads: a typing mistake, or a
    key from an older version. It only looks: nothing is deleted. `s` is the file's contents (core.settings())."""
    s = core.settings() if s is None else s
    known = set(r.file_key for r in ROWS if r.file_key) | set(OWN_KEYS)
    real = [r.key for r in ROWS] + list(OWN_KEYS)
    out = [(k, nearest(k, real)) for k in sorted(s) if k not in known]
    policy = s.get('auto_policy')
    if isinstance(policy, dict):
        read = POLICY_KEYS + SIGNIN_FILE_KEYS
        subs = ['auto_policy.' + k for k in read]
        out += [('auto_policy.' + k, nearest('auto_policy.' + k, subs)) for k in sorted(policy) if k not in read]
    return out


def not_read_lines(s=None):
    """The lines that say so, for `switchboard config` (and doctor, if it wants them)."""
    lines = []
    for k, near in not_read(s):
        lines.append('%s is in %s, and nothing reads it.%s' % (
            k, _short(settings_file()), ' Did you mean %s?' % near if near else ''))
    return lines


def _short(path):
    home = os.path.normpath(core.home())
    return '~' + path[len(home):] if path.startswith(home + os.sep) else path


# ---------- the command ----------

def unknown_key(key, as_json_doc=False):
    near = nearest(key, [r.key for r in ROWS])
    said = 'Unknown setting: %s.%s' % (key, ' Did you mean %s?' % near if near else ' `switchboard config` lists them.')
    if as_json_doc:
        _print({'schema': SCHEMA, 'key': key, 'value': None, 'default': None, 'refused': said})
        sys.stderr.write(said + '\n')
    else:
        print(said)
    return 2


def _print(doc):
    print(json.dumps(doc, indent=1, ensure_ascii=False))


class _LoopLeftToPage(object):
    """The loop's host while a change is made here because the page is not running: everything as it is, except that
    it never starts the loop that reads the accounts and switches, which runs only in the page (the page starts it)."""

    def __init__(self, real):
        self._real = real

    def ensure(self, now=None):
        pass

    def __getattr__(self, name):
        return getattr(self._real, name)


@contextmanager
def _here():
    real = engine.HOST
    engine.HOST = _LoopLeftToPage(real)
    try:
        yield
    finally:
        engine.HOST = real


def main(args, ask):
    """`switchboard config ...`. `ask(path, body)` is the running page's own request: it raises OSError when the page
    does not answer, and then the same handler runs here (server.act)."""
    flags = set(a for a in args if a.startswith('--'))
    words = [a for a in args if not a.startswith('--')]
    js = '--json' in flags
    if flags - {'--json', '--changed', '--as-commands'}:
        return _usage(js)
    if not words:
        return listing(js, only_changed='--changed' in flags, commands='--as-commands' in flags)
    what, rest = words[0], words[1:]
    if flags - {'--json'} or (js and what not in ('get',)):
        return _usage(js)
    if what == 'path' and not rest:
        print(settings_file())
        print(claude_settings_file())
        return 0
    if what == 'get' and len(rest) == 1:
        return get(rest[0], js)
    if what == 'set' and len(rest) == 2:
        return set_key(rest[0], rest[1], ask)
    if what == 'unset' and len(rest) == 1:
        return set_key(rest[0], None, ask)
    return _usage(js)


def _usage(js):
    """Words `config` does not take: its usage line, exit 2; with --json, one document that says so."""
    if js:
        from . import scripted
        return scripted.wrong_words(USAGE)
    print(USAGE)
    return 2


def get(key, js=False):
    """`config get KEY`: the value alone, as `config` says it (--json: {'schema', 'key', 'value', 'default'})."""
    row = BY_KEY.get(key)
    if row is None:
        return unknown_key(key, js)
    v = value_of(row, Facts())
    if js:
        _print({'schema': SCHEMA, 'key': key, 'value': as_json(row, v), 'default': as_json(row, row.default),
                'refused': None})
    else:
        print(shown(row, v))
    return 0


def parse(row, text):
    """The value a person typed, as the page would send it. A word that is not a value of its kind is sent as it is,
    so the page's own check says no in its own words; on and off are words only this command takes."""
    if row.kind == 'on-off':
        v = ON_WORDS.get(text.strip().lower())
        if v is None:
            raise SwitchError('%s takes on or off.' % row.key)
        return v
    if row.kind == 'number':
        try:
            n = float(text)
        except ValueError:
            n = None
        if n is None or (row.low is not None and not row.low <= n <= row.high):     # nan fails this too
            raise SwitchError('%s takes a number from %g to %g.' % (row.key, row.low, row.high) if row.low is not None
                              else '%s takes a number.' % row.key)
        return int(n) if n.is_integer() else n
    if row.kind == 'choice' and row.choices and text not in row.choices:
        raise SwitchError('%s takes %s.' % (row.key, or_words(row.choices)))
    if row.kind == 'text' and text.strip().lower() == 'none':
        return ''
    return text


def or_words(items):
    """'a, b or c'."""
    items = list(items)
    return items[0] if len(items) == 1 else '%s or %s' % (', '.join(items[:-1]), items[-1])


def set_key(key, text, ask):
    """`config set KEY VALUE`, and `config unset KEY` (text None: its default). One line says what changed; run twice,
    it says it is already so."""
    row = BY_KEY.get(key)
    if row is None:
        return unknown_key(key)
    if not row.post and not row.setter:
        raise SwitchError('%s is set with %s.' % (key, row.command) if row.command else
                          '%s is set %s, not here.' % (key, row.where))
    if core.DEMO:
        raise SwitchError('The demo has made-up accounts; change its settings on its page.')
    want = row.default if text is None else parse(row, text)
    if row.kind == 'text' and want is None:
        want = ''
    before = value_of(row, Facts())
    if row.setter:                              # a key of the settings file: its own module checks and writes it
        row.setter(want)
        return _said(row, before, value_of(row, Facts()))
    path, field = row.post
    body = {field: want}
    try:
        ask(path, body)                         # the page checks it and applies it, as a press there does
    except OSError:
        if row.page_only:
            raise SwitchError('%s runs in the page, which is not running. Start it with: switchboard serve' % key)
        from . import server
        with _here():                           # the same handler, here: the same checks, the same change
            server.act(path, body)
    return _said(row, before, value_of(row, Facts()))


def _said(row, before, after):
    """The one line a change says, or that it was so already."""
    key = row.key
    if same(row, before, after):
        print('%s is %s already.' % (key, shown(row, after)))
    else:
        print('%s is %s now (it was %s).' % (key, shown(row, after), shown(row, before)))
    return 0


def listing(js=False, only_changed=False, commands=False):
    """`switchboard config`: every setting, grouped as the page groups them, with its value, its default and what it
    does; then any key in the settings file that nothing reads. --changed: only those that differ from the default.
    --as-commands: those that differ, as the commands that set them again (as_commands)."""
    facts = Facts()
    values = [(r, value_of(r, facts)) for r in ROWS]
    if only_changed or commands:
        values = [(r, v) for r, v in values if changed(r, v)]
    if commands:
        if js:                                  # one document: the same lines, in a list
            _print({'schema': SCHEMA, 'commands': as_commands(values).splitlines()})
        else:
            print(as_commands(values), end='')
        return 0
    stray = not_read()
    if js:
        _print({'schema': SCHEMA, 'file': settings_file(),
                'settings': [{'key': r.key, 'group': dict(GROUPS)[r.group], 'value': as_json(r, v),
                              'default': as_json(r, r.default), 'changed': changed(r, v), 'type': r.kind,
                              'choices': list(r.choices) if r.choices else None, 'low': r.low, 'high': r.high,
                              'words': r.words, 'set_with': r.set_with()} for r, v in values],
                'not_read': [{'key': k, 'nearest': n} for k, n in stray]})
        return 0
    key_w = max(len(r.key) for r, _ in values) if values else 0
    val_w = max([len(shown(r, v)) for r, v in values if r.kind != 'accounts'] + [3])
    first = True
    for gid, title in GROUPS:
        rows = [(r, v) for r, v in values if r.group == gid]
        if not rows:
            continue
        if not first:
            print()
        first = False
        print(title)
        for r, v in rows:
            print('  %-*s  %-*s  default %s' % (key_w, r.key, val_w, shown(r, v), shown(r, r.default)))
            print('  %s  %s' % (' ' * key_w, r.words + ('' if r.post or r.setter else ' Set with: %s.' % r.command
                                                        if r.command else ' Set %s.' % r.where)))
    if not values:
        print('Every setting is at its default.')
    print()
    print('Kept in %s.' % _short(settings_file()))
    for line in not_read_lines():
        print(line)
    return 0


def as_commands(values):
    """The lines that set these values again, one per line: a row's own command where it has one, else `switchboard
    config set KEY VALUE`; a value only the page or the file sets is a comment that says where."""
    lines = []
    for r, v in values:
        if r.as_command:
            got = r.as_command(v)
            lines += got if isinstance(got, list) else [got]
        elif r.post or r.setter:
            lines.append('switchboard config set %s %s' % (r.key, _word(r, v)))
        else:
            lines.append('# %s %s: set %s' % (r.key, shown(r, v), r.where))
    return ''.join(x + '\n' for x in lines)


BARE = re.compile(r'^[^\s\'"]+$')        # a value a shell takes as one word as it is (the page's cfgWord too)


def _word(row, v):
    """A value as one word `config set` takes back, quoted for a shell when it needs it, as the page's Copy as commands
    writes it."""
    if row.kind == 'text' and not v:
        return 'none'
    w = shown(row, v)
    return w if BARE.match(w) else "'%s'" % w.replace("'", "'\\''")
