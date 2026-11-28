"""Takeaways on the week's numbers: what one number of your week means for you, and, where a documented change
exists, one press that makes it and one that takes it back.

takeaways(page, state) is pure. `page` is /api/usage's payload (the week the page draws); `state` is what
router_state() reads (the router's own switches and trial run). Each takeaway hangs on one number the week section
already shows, measured by this program from this Mac's own files: never a number from anywhere else, never an
average, never a saving. A number under 2 percent of the week says nothing. At most three, the largest first:

    {'id': short key, 'on': the number it hangs on ('tokens.cache_read', 'who.helpers', 'who.workflows'),
     'measured': {'value': whole percent, 'unit': 'percent_of_week', 'basis': 'your last 7 days'},
     'line': one sentence (140 characters at most),
     'change': None (advice only) or {'key': the lever, 'label': the press (40 characters at most),
                                      'estimate': None},
     'state': 'off' | 'trying' | 'on', 'can': whether a press can be made, 'why_not': None or one sentence}

The set, kept small on purpose:
  rereading  re-reading context (cache reads), when it is the largest part of the week's weighted tokens: what it
             is, and that a long chat started fresh from a short summary reads less. Advice only.
  who        helpers or workflow steps, when one of them is the largest share of who did the work. It offers the
             press "Try it" only when the router's own trial-run module (switchboard.router.tryit: on, off, state)
             can be imported; without it the takeaway is advice only. That module is imported when it is needed,
             never when this one is: setting the router's switches alone is not a trial run, so this module never
             sets one.
  storing    how often a helper stored its whole context again after a wait, from the router's own count
             (switchboard.router.cache_count, asked for by the page's background work through count_now, never for
             a request), when it is 20 times or more and the cache setting's own status says the key can be set.
             Its measured number is that count ('times'); its window is named only when the count's words name it.

`state` comes from the router's own settings file, trial-run module, count and cache setting; this module keeps none
of its own.

act(id, do) makes or takes back a takeaway's change through the router's own functions only: the trial run
(tryit.on and tryit.off) and the helpers' cache (router.cache_setting.apply and undo, the lever `cache_hour`). Those
functions write with their own record and an exact way back; this module opens no file and writes none. It returns
{'done': bool, 'says': one sentence}, and for the trial run also 'more': the module's other lines, said under the
first.
"""
import importlib

from . import usage
from .router import cache_setting
from .router import settings as rsettings

FLOOR = 2                   # percent of the week: under it a number says nothing
MOST = 3
LINE_MAX = 140
LABEL_MAX = 40
BASIS = 'your last 7 days'
UNIT = 'percent_of_week'

LINE_REREADING = 'Every reply reads the whole chat again, so a long chat started fresh from a short summary reads less.'
LINE_WHO = {'helpers': 'Helpers did the largest part of your week.',
            'workflows': 'Workflow steps did the largest part of your week.'}
# What the trial run can note: only Claude Code chats opened after it is turned on, and nothing is moved.
LINE_WHO_TRY = {'helpers': 'Helpers did the largest part of your week. A trial notes where routing would send work in '
                           'new Claude Code chats; nothing moves.',
                'workflows': 'Workflow steps did the largest part of your week. A trial notes where routing would send '
                             'work in new Claude Code chats; nothing moves.'}
LABEL_TRY = 'Try it'
TRYIT = 'switchboard.router.tryit'
COUNTER = 'switchboard.router.cache_count'
RESTORES_MIN = 20           # the count says nothing under this many restores
TIMES = 'times'
LABEL_CACHE = "Keep helpers' cache for an hour"
LINE_STORING = 'Helpers stored their whole context again after a wait.'

DID_TRY = 'Trial-run switch on; it changes nothing.'
DID_STOP = 'Trial run off.'
TRY_FAILED = 'The trial run could not be started.'
STOP_FAILED = 'The trial run could not be stopped.'
WRITE_FAILED = 'Switchboard could not save that change, so nothing was changed.'
NO_SUCH = 'There is no change to make for that.'
ONLY_TRY = 'This one can only be tried: press Try it, and Undo to stop.'
NO_TRY = 'This one has no trial run: press Turn on, and Undo to take it back.'

# Each takeaway that offers a press: its lever, and the presses that lever takes.
LEVERS = {'who': ('dry', ('try', 'off')), 'storing': ('cache_hour', ('on', 'off'))}


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float('inf')


def _dict(v):
    return v if isinstance(v, dict) else {}


def _tryit(name=TRYIT):
    """One of the router's own modules made for this (the trial run, the count), or None when it cannot be imported
    (it may not be installed yet)."""
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def count_now(limit_s):
    """The router's count of helpers storing their whole context again after a wait, with its own sentences under
    'says', or None (no such module, or it failed). It can take its whole time limit, so only the page's background
    work calls it, never a request."""
    mod = _tryit(COUNTER)
    if mod is None:
        return None
    try:
        got = dict(mod.count(mod.projects_folders(), limit_s=limit_s))
        says = mod.say(got)
    except Exception:
        return None
    got['says'] = [x for x in says if isinstance(x, str)] if isinstance(says, list) else []
    return got


def router_state(count=None, cache=None):
    """What the takeaways' state is read from: {'switches': the router's switches, 'trial': None when the trial-run
    module cannot be imported, else what its state() said ({} when that failed)}; with a `count` (count_now's last
    answer) also 'count' and 'cache', the cache setting's own status (None when it failed; `cache`: its keyword
    arguments). Reads only (an unreadable switches file reads as all off and is not said to History from here)."""
    trial, mod = None, _tryit()
    if mod is not None:
        try:
            trial = _dict(mod.state())
        except Exception:
            trial = {}
    out = {'switches': rsettings.read(say=False), 'trial': trial}
    if isinstance(count, dict):
        try:
            status = cache_setting.status(**dict(cache or {}))
        except Exception:
            status = None
        out.update(count=count, cache=status)
    return out


def _one(id_, on, value, line, change=None, state='off', can=False, why_not=None):
    return {'id': id_, 'on': on, 'measured': {'value': value, 'unit': UNIT, 'basis': BASIS}, 'line': line,
            'change': change, 'state': state, 'can': can, 'why_not': why_not}


def _rereading(page, state):
    """Re-reading's whole percent of the week's weighted tokens, as the week's highlight figures it
    (insights._context), when it is the largest of the four parts."""
    t = _dict(page.get('tokens'))
    if any(k in t and not _num(t[k]) for k in usage.TOKENS):
        return None
    w = usage.weighted(dict((k, float(t.get(k) or 0)) for k in usage.TOKENS))
    parts = [w[k] for k in usage.TOKEN_KINDS]
    if sum(parts) <= 0:
        return None
    pct = dict(zip(usage.TOKEN_KINDS, usage.whole_percents(parts)))
    if any(pct[k] >= pct['cache_read'] for k in usage.TOKEN_KINDS if k != 'cache_read'):
        return None
    return _one('rereading', 'tokens.cache_read', pct['cache_read'], LINE_REREADING)


def _who(page, state):
    """The share of helpers or workflow steps in the legend's who-did-the-work row, when it is the largest."""
    rows = _dict(page.get('cuts')).get('who')
    if not isinstance(rows, list):
        return None
    by = dict((usage.PAGE_WHO[k], k) for k in usage.PAGE_WHO)
    shares = {}
    for r in rows:
        if isinstance(r, dict) and r.get('label') in by and _num(r.get('share')):
            shares[by[r['label']]] = r['share']
    if not shares:
        return None
    top = max(shares, key=lambda k: (shares[k], k != 'chat'))
    if top == 'chat' or any(k != top and v >= shares[top] for k, v in shares.items()):
        return None
    trial = state.get('trial')
    if isinstance(trial, dict):                 # the trial-run module is there: the press, its state from the module
        change = {'key': 'dry', 'label': LABEL_TRY, 'estimate': None}
        return _one('who', 'who.' + top, shares[top], LINE_WHO_TRY[top], change,
                    'trying' if trial.get('trying') is True else 'off', True)
    dry = _dict(state.get('switches')).get('dry') is True
    return _one('who', 'who.' + top, shares[top], LINE_WHO[top], None, 'trying' if dry else 'off')


def _storing(page, state):
    """The router's count of restores, when it is 20 or more and the key can be set (or is Switchboard's own 1h,
    which shows Undo). Its line is a fixed sentence held to this program's word rules: the count's own sentences
    come from another module and are never shown here; they only say whether the window is the last 7 days."""
    count, st = _dict(state.get('count')), state.get('cache')
    restores, says = count.get('restores'), count.get('says')
    if not isinstance(st, dict) or not _num(restores) or restores < RESTORES_MIN:
        return None
    says = says if isinstance(says, list) else []
    if st.get('problem') or st.get('notes') or st.get('new_enough') is False:
        return None
    ours = st.get('state') == '1h' and st.get('set_by') == 'switchboard'
    if st.get('state') != 'unset' and not ours:
        return None
    basis = BASIS if any(isinstance(x, str) and 'last 7 days' in x for x in says) else None
    change = {'key': 'cache_hour', 'label': LABEL_CACHE, 'estimate': None}
    return {'id': 'storing', 'on': 'tokens.cache_write', 'measured': {'value': restores, 'unit': TIMES, 'basis': basis},
            'line': LINE_STORING, 'change': change, 'state': 'on' if ours else 'off', 'can': True, 'why_not': None}


def takeaways(page, state=None):
    """Up to three takeaways for the week in `page`, the largest first; [] when the week has nothing to say."""
    page, state = _dict(page), _dict(state)
    out = []
    for rule in (_rereading, _who, _storing):
        got = rule(page, state)
        if got and (got['measured']['unit'] != UNIT or got['measured']['value'] >= FLOOR):
            out.append(got)
    out.sort(key=lambda t: (t['measured']['unit'] != UNIT, -t['measured']['value']))     # the shares, then counts
    return out[:MOST]


def _offered(shown, id_, key):
    """Whether a takeaway in `shown` offers the press of lever `key` for `id_` and can be pressed now."""
    for t in shown if isinstance(shown, list) else ():
        t = _dict(t)
        change = t.get('change')
        if t.get('id') == id_ and t.get('can') is True and isinstance(change, dict) and change.get('key') == key:
            return True
    return False


def act(id_, do, cache=None, shown=None):
    """Make the change of takeaway `id_` ('try' or 'on'), or take it back ('off'). `shown`: the takeaways showing
    now; a change is made only when one of them offers it, so a takeaway stays a sentence until it is pressed. 'off'
    needs none, so Undo works after the week has moved on. `cache`: the keyword arguments router.cache_setting's
    functions take (its folders and versions); None for this Mac's own. Never raises for a change the router
    refuses: that is said in one sentence."""
    lever = LEVERS.get(id_) if isinstance(id_, str) else None
    if lever is None or not isinstance(do, str) or do not in ('try', 'on', 'off'):
        return {'done': False, 'says': NO_SUCH}
    key, presses = lever
    if do != 'off' and not _offered(shown, id_, key):
        return {'done': False, 'says': NO_SUCH}
    if do not in presses:
        return {'done': False, 'says': ONLY_TRY if key == 'dry' else NO_TRY}
    if key == 'dry':
        return _trial(do == 'try')
    try:
        kw = dict(cache or {})
        got = cache_setting.apply('1h', **kw) if do == 'on' else cache_setting.undo(**kw)
    except rsettings.SettingsError as e:
        return {'done': False, 'says': str(e)}
    except OSError:
        return {'done': False, 'says': WRITE_FAILED}
    return {'done': bool(got.get('written')), 'says': got.get('blocked') or got.get('says') or WRITE_FAILED}


def _trial(on):
    """Start ('try') or stop ('off') the trial run through the router's own module; its lines are what is said. Any
    failure in it is one sentence, and nothing is changed here."""
    mod = _tryit()
    if mod is None:
        return {'done': False, 'says': NO_SUCH}
    try:
        got = mod.on() if on else mod.off()
    except Exception:
        return {'done': False, 'says': TRY_FAILED if on else STOP_FAILED}
    lines = [x.strip() for x in got if isinstance(x, str) and x.strip()] if isinstance(got, list) else []
    if not lines:
        lines = [DID_TRY if on else DID_STOP]
    return {'done': True, 'says': lines[0], 'more': lines[1:]}
