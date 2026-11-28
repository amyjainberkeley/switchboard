"""`switchboard demo`: the real page with made-up accounts.

For trying it before setting anything up, and for the picture in the README.
Nothing here touches Claude Code, the engine or the browser. Buttons change
only this in-memory copy.
"""
import copy
import time
from datetime import datetime, timezone

from . import engine
from . import history as hist
from . import insights
from . import server
from .core import SwitchError, blank_state, event, name_of, parse_iso, set_active

H, D = 3600, 86400
# The made-up accounts' plans, as the engine reports them: its label, the rate tier and the organisation's type.
PLANS = {'team': ('Team', 'default_claude_max_5x', 'claude_team'),
         'enterprise': ('Enterprise', 'default_claude_max_5x', 'claude_enterprise'),
         'max20': ('Ultra', 'default_claude_max_20x', 'claude_max'),
         'max5': ('Max 5x', 'default_claude_max_5x', 'claude_max')}
STATE = {}
FACTS = {}
CLOCK = {}          # when the demo's times were last moved forward (see _keep_fresh)

# What History's Work shows in the demo: the same shape and the same kind of sentences the server
# builds from real history (usage.for_page). Each cut adds to 100.
def _who(workflows, helpers, chat):
    return {'workflows': workflows, 'helpers': helpers, 'chat': chat}


USAGE = {'days': 7, 'moved': {'share': 37, 'to': 'Sonnet', 'from': ['Opus']}, 'cuts': {
    'work': [{'label': 'Website', 'share': 73, 'note': '86% workflows, 11% helpers, 3% chat. Mostly Opus.', 'who': _who(86, 11, 3)},
             {'label': 'Mobile app', 'share': 13, 'note': '92% workflows, 8% chat. Opus and Sonnet.', 'who': _who(92, 0, 8)},
             {'label': 'Notes', 'share': 4, 'note': '51% chat, 49% workflows. Sonnet and Fable.', 'who': _who(49, 0, 51)},
             {'label': 'Design system', 'share': 2, 'note': 'All workflows. Mostly Sonnet.', 'who': _who(100, 0, 0)},
             {'label': 'Onboarding', 'share': 2, 'note': 'All workflows. Mostly Opus.', 'who': _who(100, 0, 0)},
             {'label': 'Billing', 'share': 2, 'note': '88% chat, 12% helpers. Opus and Sonnet.', 'who': _who(0, 12, 88)},
             {'label': 'Security review', 'share': 2, 'note': '97% chat, 3% helpers. All Opus.', 'who': _who(0, 3, 97)},
             {'label': '18 more', 'share': 2, 'note': '18 smaller chats.', 'who': _who(38, 21, 41)}],
    'who': [{'label': 'Workflows', 'share': 81, 'note': '32 workflows, 410 agents. The largest took 19%.'},
            {'label': 'Helpers', 'share': 10, 'note': '57 helpers started from chats.'},
            {'label': 'You, in chat', 'share': 9, 'note': 'Your own turns in 26 chats.'}],
    'model': [{'label': 'Opus', 'share': 72, 'note': '14,530 requests.'},
              {'label': 'Sonnet', 'share': 20, 'note': '4,210 requests.'},
              {'label': 'Fable', 'share': 8, 'note': '610 requests.'}],
    'kind': [{'label': 'Not labelled', 'share': 54, 'note': '9,880 requests.'},
             {'label': 'Build', 'share': 21, 'note': '3,120 requests.'},
             {'label': 'Review', 'share': 11, 'note': '1,904 requests.'},
             {'label': 'Research', 'share': 9, 'note': '1,377 requests.'},
             {'label': 'Fix', 'share': 5, 'note': '702 requests.'}]}}


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def _hour(t):
    """A made-up reset time, on the hour as Anthropic's are."""
    return _iso(round(t / H) * H)


def _build():
    now = time.time()
    st = blank_state()

    def account(email, alias, slot, session, weekly, pace, profile, org, plan, **more):
        st['order'].append(email)
        label, tier, kind = PLANS[plan]
        st['accounts'][email] = dict({
            'email': email, 'alias': alias, 'slot': slot, 'connected': True, 'org': org, 'org_id': 'demo-org-%d' % slot,
            'id': 'demo-account-%d' % slot,     # what an account's rule in auto-switch is kept under
            'plan': label, 'tier': tier, 'org_type': kind,
            'profile': profile, 'profile_src': 'checked', 'usage_at': now - 40,
            # the 5-hour window as the engine reports it: 0% with no reset when nothing was used in the last five
            # hours ("Not started"), and none at all for an account with no 5-hour limit (Ultra: session None)
            'usage': {'session': None if session is None else
                      {'pct': session, 'resets': _hour(now + 2.4 * H) if session else None, 'pace': None, 'lasts': None},
                      'weekly': {'pct': weekly, 'resets': _hour(now + (2 + slot) * D), 'pace': pace, 'lasts': weekly < 85},
                      'scoped': [], 'spend': None}}, **more)

    account('maya@northwind.example', 'work', 1, 62, 70, 55, 'Profile 1', 'Northwind', 'team')
    account('maya.reyes@mail.example', 'personal', 2, None, 91, 60, 'Default', "maya.reyes@mail.example's Organization",
            'max20')
    account('maya@fernhill.example', 'fernhill', 3, 0, 23, 41, 'Claude Fernhill', 'Fernhill Studio', 'team')
    account('m.reyes@harbor.example', 'harbor', 4, 0, 5, 30, 'Claude Harbor', 'Harbor & Co', 'enterprise',
            signed_out=now - 2 * H)
    account('maya@ivy.example', 'ivy', 5, 20, 65, 50, 'Claude Ivy', "maya@ivy.example's Organization", 'max5',
            models_seen=['Fable'])
    ivy = st['accounts']['maya@ivy.example']['usage']
    ivy['weekly']['resets'] = _hour(now + 1.4 * D)
    ivy['session']['resets'] = _hour(now + 3.1 * H)
    ivy.update(scoped=[{'name': 'Fable', 'pct': 80, 'resets': _hour(now + 1.4 * D)}],     # room under its 95 line
               spend={'used': 9.04, 'limit': 70.84, 'currency': 'USD'})
    # Each kind of account row: harbor's Fable week is over its 95 line (the short red note at the end of its weekly
    # line), and it still works on other models, at 5% weekly; personal has no 5-hour limit, and needs a sign-in since
    # yesterday, so its reading is too old to show (a dash, its age under it).
    harbor = st['accounts']['m.reyes@harbor.example']
    harbor['usage']['scoped'] = [{'name': 'Fable', 'pct': 97, 'resets': harbor['usage']['weekly']['resets']}]
    harbor['models_seen'] = ['Fable']
    st['accounts']['maya.reyes@mail.example']['usage_at'] = now - 26 * H
    # When each sign-in ends: fernhill's in under 3 days (it still works, and says so with a way to sign in again),
    # personal's ended yesterday, so it needs a sign-in; the others' far off, where nothing is said.
    for email, ends in (('maya@northwind.example', now + 24 * D), ('maya@fernhill.example', round((now + 2.9 * D) / H) * H),
                        ('m.reyes@harbor.example', now + 19 * D), ('maya@ivy.example', now + 26 * D),
                        ('maya.reyes@mail.example', now - D)):
        st['accounts'][email]['signin_ends_at'] = ends
    st['accounts']['maya.reyes@mail.example'].update(needs_login=True, status='needs_login')

    order = ['maya.reyes@mail.example', 'maya@northwind.example', 'maya@fernhill.example', 'maya@northwind.example',
             'maya.reyes@mail.example', 'maya@northwind.example', 'maya@fernhill.example', 'maya@northwind.example',
             'm.reyes@harbor.example', 'maya@northwind.example']
    gaps = [7.2, 6.5, 5.9, 5.1, 4.2, 3.6, 2.4, 1.9, 0.6, 0.35]
    st['timeline'] = [[e, now - g * D] for e, g in zip(order, gaps)]
    st['active'] = order[-1]          # work: 70% used four days into its week, so at that pace it runs out first

    pages = [('Q4 pricing review', 'maya@northwind.example', 0.02), ('Weekly sync, one page', 'maya@northwind.example', 0.3),
             ('Brand guide draft', 'maya@fernhill.example', 0.9), ('Trip budget', 'maya.reyes@mail.example', 1.4),
             ('Vendor comparison', 'm.reyes@harbor.example', 2.2), ('Onboarding checklist', 'maya@northwind.example', 3.1),
             ('Reading list', 'maya.reyes@mail.example', 4.0), ('Launch notes', None, 4.6),
             ('Interview loop', 'maya@northwind.example', 5.5)]
    for i, (title, owner, age) in enumerate(pages):
        st['artifacts'].append({'id': 'demo%012d' % i, 'url': 'https://claude.ai/artifact/demo%012d0000' % i,
                                'title': title, 'name': None, 'owner': owner, 'src': 'seen' if owner else None,
                                't': now - age * D, 'first': now - age * D, 'ot': now, 'profile': None})
    st['history_lines'] = _history(now)
    st['events'] = [{'t': now - 0.35 * D, 'kind': 'switch', 'text': 'Claude Code is now on maya@northwind.example'},
                    {'t': now - 0.3 * D, 'kind': 'info',
                     'text': 'Learned that "Weekly sync, one page" opens as maya@northwind.example'},
                    {'t': now - 2 * H, 'kind': 'warn',
                     'text': 'The browser profile for m.reyes@harbor.example is signed out of claude.ai'}]
    st['tick_at'] = now - 20
    facts = {'profiles': {'Profile 1': 'Work', 'Default': 'Personal', 'Claude Fernhill': 'Claude Fernhill',
                          'Claude Harbor': 'Claude Harbor', 'Claude Ivy': 'Claude Ivy'},
             'browser': {'name': 'Google Chrome', 'present': True, 'tabs_blocked': False},
             'engine': {'installed': True, 'claude': True},
             'links': {'installed': True, 'on': True, 'browser': 'Switchboard', 'home': '', 'key': 'demo'},
             # What refills first, so the list reads soonest refill first; Fable's week counts at its own line
             'auto': {'available': True, 'service': True, 'enabled': True, 'running': True, 'problem': None,
                      'on': True, 'starting': False, 'stage': None, 'waiting_for': None, 'restarted': None,
                      'reading': True, 'reading_wanted': True, 'reading_problem': None,
                      'session_at': 90, 'weekly_at': 95, 'model_at': 95, 'weekly_set': True,
                      'strategy': 'refills-first', 'model': 'Fable', 'models': ['Fable'],
                      'rules': {}},             # no account has a rule at the start (the pictures start here)
             'guard': None,
             'mac': {'awake': True, 'notify': True, 'statusline': False, 'statusline_theirs': True},
             'models': False, 'helpers': False,          # Sonnet for helpers, Models by the job: both off
             'keys': {'count': 2, 'on': True, 'command': 'switchboard claude'},
             'packs': {'house': False, 'codemap': True, 'claudemem': True, 'cap': 10},
             # the Claude extension, in some of the profiles (Chrome setup)
             'extensions': {'Profile 1': True, 'Default': True, 'Claude Fernhill': False, 'Claude Harbor': False,
                            'Claude Ivy': True},
             'first_name': 'Maya',
             'setup_hidden': True, 'demo': True, 'connecting': []}
    return st, facts


def _history(now):
    """The demo's log: made-up lines in the shape history.jsonl keeps (oldest first)."""
    from . import notify
    n, p, f, c, ivy = ('maya@northwind.example', 'maya.reyes@mail.example', 'maya@fernhill.example',
                       'm.reyes@harbor.example', 'maya@ivy.example')

    def switch(frm, to, by, reason=None):
        return ('Claude Code is now on %s' % to if by != 'you' else 'Switched Claude Code to %s' % to,
                dict({'h': 'switch', 'from': frm, 'to': to, 'account': to, 'by': by}, **({'reason': reason} if reason else {})))

    def limit(email, words, pct=None, line=None):
        if pct is None:
            return ('%s: %s limit refilled' % (email, words), {'h': 'limit', 'account': email, 'window': words,
                                                             'reason': '%s refilled' % words})
        return ('%s reached its %s switch line: %d%%' % (email, words, pct),
                {'h': 'limit', 'account': email, 'window': words, 'pct': pct, 'line': line,
                 'reason': '%s at %d%%' % (words, pct)})
    rows = [(6.9 * D, ('Kept %s · 5 accounts' % ivy, {})),
            (6.88 * D, (notify.AUTO_ON, {'reason': '5 accounts to switch between'})),
            (6.5 * D, switch(p, n, 'auto', 'weekly at 95%, refills first')),
            (5.1 * D, switch(f, n, 'you')),
            (3.6 * D, switch(p, n, 'auto', 'refills first')),
            (2.4 * D, switch(n, f, 'claude-code')),
            (2.2 * D, ('Keep the Mac awake: on', {})),
            (1.9 * D, switch(f, n, 'you')),
            (1.05 * D, limit(ivy, '5-hour', 90, 90)),
            (0.82 * D, limit(ivy, '5-hour')),
            (0.6 * D, switch(n, c, 'auto', '5-hour at 92%, refills first')),
            (0.36 * D, limit(c, 'weekly', 95, 95)),
            (0.35 * D, switch(c, n, 'auto', 'weekly at 95%, refills first')),
            (0.3 * D, ('Learned that "Weekly sync, one page" opens as %s' % n, {})),
            (2 * H, ('The browser profile for %s is signed out of claude.ai' % c, {})),
            (1.2 * H, ('The sign-in timed out. Press Add account to try again.', {'h': 'problem'})),
            (0.7 * H, limit(c, 'weekly'))]
    out = []
    for age, (text, facts) in rows:
        kind, got = hist.classify(text)
        facts = dict(facts)
        kind = facts.pop('h', None) or kind
        got.update(facts)
        out.append(dict(got, t=now - age, kind=kind, text=text))
    return out


def files():
    """GET /api/artifacts in the demo: the made-up accounts as folders of their made-up artifacts."""
    from . import artifacts
    view()                                              # the made-up times move with the clock first
    return artifacts.folders(STATE, FACTS['profiles'])


def _entries():
    """The made-up log, newest first, each with the log's own two columns (history.page's shape)."""
    out = []
    for x in sorted(STATE.get('history_lines') or [], key=lambda x: -x['t']):
        subject, tail = hist.parts(x)
        out.append(dict(x, subject=subject, tail=tail))
    return out


def history():
    """GET /api/history in the demo: the made-up log, newest first, and the made-up Usage cuts."""
    _ready()
    return {'entries': _entries(), 'work': usage()}


def _ready():
    if not STATE:
        st, facts = _build()
        STATE.update(st)
        FACTS.update(facts)
        CLOCK['at'] = time.time()


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _keep_fresh(now):
    """A demo left running reads like a fresh one: every time in it moves forward with the clock, all by the same
    amount, so a reading stays 40 s old, no window passes its reset, and the switch history keeps its order. A time
    set on purpose keeps its distance from now too (a test's two-hour-old reading stays two hours old). Without
    this, an hour in, every reading would be too old to trust, so the page would show a dash for every account and "All
    nearly out"."""
    last, CLOCK['at'] = CLOCK.get('at'), now
    if last is None or now <= last:
        return
    dt = now - last
    for a in STATE['accounts'].values():
        for k in ('usage_at', 'signed_out', 'signin_ends_at'):
            if _num(a.get(k)):
                a[k] += dt
        u = a.get('usage') or {}
        for w in [u.get('session'), u.get('weekly')] + list(u.get('scoped') or []):
            t = parse_iso(w.get('resets')) if isinstance(w, dict) else None
            if t is not None:
                w['resets'] = _iso(t + dt)
    for row in STATE.get('timeline') or []:
        row[1:] = [t + dt if _num(t) else t for t in row[1:]]
    for item in (STATE.get('events') or []) + (STATE.get('artifacts') or []) + (STATE.get('history_lines') or []):
        for k in ('t', 'first', 'ot'):
            if _num(item.get(k)):
                item[k] += dt


def view():
    _ready()
    now = time.time()
    if STATE.get('ticking', True):                  # a long-running demo still looks freshly read
        STATE['tick_at'] = now - 20
        _keep_fresh(now)
    from . import chats as ch
    board = FACTS['providers'] if 'providers' in FACTS else providers(now)
    rows = FACTS['provider_rows'] if 'provider_rows' in FACTS else _rows_of(board, now)
    return server.render(STATE, dict(FACTS, now=now, history=_entries()[:server.HISTORY_SHOWN],
                                     chats=ch.counts(chats()), signins=_signins(now),
                                     providers=board, provider_rows=rows))


def _signins(now):
    """Sign in to all in the demo: where a press left it (act), else nothing under way."""
    from . import signins
    return FACTS.get('signins') or dict({'state': 'idle'}, **signins.due(server.signins_accounts(STATE), now))


def providers(now, codex=True, out=False):
    """The made-up board of assistants, in providers/view.py's shape: Claude (the accounts above) with room and, unless
    codex is False, one made-up Codex, added. By default it has room: Plus, maya@northwind.example, 5-hour 8% and
    weekly 21%, read 40 seconds ago. out=True: ChatGPT Pro, out on its weekly window until the day after tomorrow.
    codex=False: Codex is on this Mac and not added. The state's rows are derived from this board (provider_rows), so
    the two cannot disagree. A test may set FACTS['providers'] (and FACTS['provider_rows']) itself."""
    claude = {'id': 'claude', 'name': 'Claude', 'vendor': 'Anthropic', 'app': 'Claude Code', 'added': True,
              'account_list': True, 'installed': True, 'signed_in': None, 'version': '2.1.330', 'state': 'room',
              'why': 'has room', 'back_at': None, 'read_at': now - 40, 'plan': None, 'count': 5, 'with_room': 3,
              'can_carry': True, 'seats': [], 'error': None, 'email': None, 'has_app': False, 'newest_seen': None,
              'fit_why': None}
    if not codex:
        return {'v': 1, 'now': now, 'order': ['claude'], 'providers': [claude], 'carry': False,
                'add': [{'id': 'codex', 'name': 'Codex', 'app': 'Codex', 'version': '0.169.1'}]}
    if out:
        back = round((now + 1.6 * D) / H) * H
        read, label, plan, state, why = now - 12 * 60, 'ChatGPT Pro', 'pro', 'out', 'is out'
        windows = [{'label': 'weekly', 'used_pct': 100.0, 'resets_at': back, 'length_min': 10080, 'line': 95.0}]
    else:
        back = None
        read, label, plan, state, why = now - 40, 'ChatGPT Plus', 'plus', 'room', 'has room'
        windows = [{'label': '5-hour', 'used_pct': 8.0, 'resets_at': round((now + 3.7 * H) / 60) * 60,
                    'length_min': 300, 'line': 90.0},
                   {'label': 'weekly', 'used_pct': 21.0, 'resets_at': round((now + 5.6 * D) / H) * H,
                    'length_min': 10080, 'line': 95.0}]
    codex_entry = {'id': 'codex', 'name': 'Codex', 'vendor': 'OpenAI', 'app': 'Codex', 'added': True,
                   'account_list': False, 'installed': True, 'signed_in': True, 'version': '0.169.1', 'state': state,
                   'why': why, 'back_at': back, 'read_at': read, 'plan': label, 'count': 0, 'with_room': 0,
                   'can_carry': True, 'error': None, 'email': 'maya@northwind.example', 'has_app': True,
                   'newest_seen': '0.169.1', 'fit_why': None,
                   'seats': [{'label': label, 'plan': plan, 'in_use': True, 'read_at': read, 'state': state,
                              'back_at': back, 'windows': windows}]}
    return {'v': 1, 'now': now, 'order': ['claude', 'codex'], 'providers': [claude, codex_entry], 'carry': True,
            'add': []}


def provider_rows(now, codex=True, out=False):
    """The state's rows for the made-up board above (providers()), derived the way the server derives its own
    (providers.rows.from_board), each with the demo's newest made-up chat as the one that would go, so the one list
    shows without touching anything. Its times move with the clock."""
    return _rows_of(providers(now, codex=codex, out=out), now)


def _rows_of(board, now):
    from .providers import rows
    chat = {'name': newest_chat_name(), 'mark': newest_chat_mark()}
    return [dict(r, chat=dict(chat)) for r in rows.from_board(board, now=now)]


def newest_chat_name():
    """The name of the newest of the demo's made-up chats that has one."""
    return min((c for c in DEMO_CHATS if c[1]), key=lambda c: c[5])[1]


def newest_chat_mark():
    """That chat's mark, as a face sends it back with a press (providers.press.mark of its made-up id)."""
    from .providers import press
    return press.mark(min((c for c in DEMO_CHATS if c[1]), key=lambda c: c[5])[0])


# Made-up chats for GET /api/chats and `switchboard chats`: one of each case the page draws. Each is (id, title,
# folder, started under, [(hours ago, account it moved to)], hours since it last wrote, helpers, own login), the
# accounts by their place in the demo's list (1 work ... 5 ivy; 0 an organization Switchboard doesn't keep; None
# a chat whose file names no account). The first one follows the demo's own switch history.
DEMO_CHATS = [
    ('c0ffee01-7d1e-4b8a-9c2f-5a6b7c8d9e01', 'Billing export fix', 'billing', 2, 'timeline', 0.01, 3, False),
    ('77b0e4d1-2a3b-4c5d-8e6f-112233445566', 'Onboarding copy', 'website', 1, [], 3.0, 0, False),
    ('3b5d7f90-aaaa-4bbb-8ccc-0123456789ab', 'Brand guide, second pass', 'design-system', 3, [], 2.2, 1, False),
    ('9e8d7c6b-1111-4222-8333-444455556666', 'Vendor comparison', 'vendors', 4, [(8.4, 1)], 5.0, 0, False),
    ('d0c5f00d-4e1a-4b2c-8d3e-5f6a7b8c9d0e', 'Quarterly numbers', 'finance', 3, [(1.5, 1)], 0.8, 0, False),
    ('f1ab1e00-5555-4666-8777-888899990000', 'Fable eval run', 'evals', 5, [], 0.02, 6, True),
    ('0dd5e1f0-9999-4aaa-8bbb-ccccddddeeee', 'Notes from the old laptop', 'notes', 0, [], 6.0, 0, False),
    ('5ca1ab1e-1234-4567-89ab-cdef01234567', None, 'scratch', None, [], 20.0, 0, False),
    ('a11ce0ff-7777-4888-8999-aaaabbbbcccc', 'Trip budget', 'personal', 2, [], 70.0, 0, False),
]


def chats(days=None):
    """GET /api/chats in the demo: the made-up chats above across the made-up accounts, through the same view the
    real ones go through (chats.view), so it has every case the page draws: working and idle, a chat that moved
    many times and one that never moved, one holding a connector and a page that belong to another account (working,
    and one idle away from home: the page's "Reopen at home" can be pressed for that one), an
    account with no chat on it, a chat on its own login, one on an account Switchboard doesn't keep, one whose
    account is not known."""
    from . import chats as ch
    _ready()
    now = time.time()
    # By the accounts' places in the list; page tests put other made-up accounts here, with no organization and a
    # short history, and the chats then sit on accounts not known.
    orgs = dict((a.get('slot'), a['org_id']) for a in STATE['accounts'].values() if a.get('org_id'))
    orgs[0] = 'e15e0e1f-0000-4000-8000-000000000000'
    by_email = dict((e, a['org_id']) for e, a in STATE['accounts'].items() if a.get('org_id'))
    entries, extras, published, own = {}, {}, {}, {}
    for cid, title, folder, start, moves, quiet, helpers, own_login in DEMO_CHATS:
        owner = orgs.get(start)
        if moves == 'timeline':
            steps = [[t, by_email[e]] for e, t in STATE.get('timeline') or [] if e in by_email]
            if len(steps) > 2 and owner:
                steps[2:2] = [[steps[2][0] - 40, owner]]    # a flutter on the way to fernhill: folded, no move
        else:
            steps = ([[now - 30 * H, owner]] if owner else []) + [[now - h * H, orgs[to]] for h, to in moves
                                                                   if orgs.get(to)]
        last = now - quiet * H
        entries[cid] = {'file': '/Users/me/.claude/projects/demo/%s.jsonl' % cid, 'end': 1, 'owner': owner,
                        'steps': steps, 'cwd': '/Users/me/work/%s' % folder, 'last': last}
        x = ch._blank_extra()
        x.update(end=1, size=1, custom=title, helpers=helpers, by={(steps[-1][1] if steps else owner) or '': last})
        extras[cid] = x
        if own_login:
            own[cid] = True
    first = DEMO_CHATS[0][0]
    work, personal = orgs.get(1), orgs.get(2)
    turns, connectors = {}, {}
    if work and personal:
        # It used Google Drive under personal (4 days ago), where work has never had it answer; its two calls since
        # failed. It published one page while on fernhill (2.1 days ago) and one on work, where it is now.
        extras[first]['by'] = {personal: now - 4.0 * D, work: now - 30}
        extras[first]['conn'] = {'Google Drive': {'ok': {personal: now - 4.0 * D}, 'err': {work: [2, now - 600]}},
                                 'Linear': {'ok': {work: now - 2 * H}, 'err': {}}}
        published[first] = [(now - 2.1 * D, 'demoChatPage00000001'), (now - 0.2 * D, 'demoChatPage00000002')]
        connectors = {personal: {'Google Drive': now - 4.0 * D, 'Gmail': now - 3 * D}, work: {'Linear': now - 2 * H}}
    fernhill, numbers = orgs.get(3), 'd0c5f00d-4e1a-4b2c-8d3e-5f6a7b8c9d0e'
    if work and fernhill:
        # Idle on work since it moved there, away from fernhill, where Notion answered it and where it made a page:
        # the case where the page's "Reopen at home" can be pressed and its second line shows.
        extras[numbers]['conn'] = {'Notion': {'ok': {fernhill: now - 26 * H}, 'err': {}}}
        published[numbers] = [(now - 20 * H, 'demoChatPage00000003')]
        connectors.setdefault(fernhill, {})['Notion'] = now - 26 * H
    for org, hours in ((work, ((0.1, 14), (1.2, 22), (5.0, 9))), (personal, ((20.0, 31),)),
                       (orgs.get(3), ((2.3, 12),)), (orgs.get(5), ((0.05, 40),))):
        if org:
            turns[org] = dict((str(int((now - h * H) // ch.BUCKET)), n) for h, n in hours)
    index = {'v': 1, 'at': now - 20, 'orgs': {}, 'chats': entries, 'done': True, 'since': now - 7 * D,
             'connectors': connectors}
    side = {'v': ch.VIEW_V, 'chats': extras, 'turns': turns, 'seen': {},
            'look': {'chats': len(entries), 'read': len(entries), 'left': 0, 'bytes': 0}}
    return ch.view(index, side, STATE, now=now, days=ch.DAYS if days is None else days, published=published,
                   own=own)


def usage():
    _ready()
    now = time.time()
    if STATE.get('ticking', True):                  # the made-up History moves with the clock, as in view()
        _keep_fresh(now)
    u = copy.deepcopy(USAGE)
    u['claude_mem'] = {'share': 4.2, 'cap': FACTS['packs']['cap'], 'paused': False} if FACTS['packs']['claudemem'] else None
    u.update(_week(now))
    u['insights'] = insights.find(u)
    return u


# ---------- the week under the accounts, made up (the same shape and the same rules as insights.week and .find) ----------
#
# Each account's readings over the last 3 days, as (hours ago, percent) points: a straight line between two, and a
# refill where two points share a time. They follow the made-up History (_history): work in use until its 5-hour line
# moved Claude Code to harbor (0.6 days ago), harbor's weekly line (0.36 days) and the switch back (0.35 days), ivy's
# 5-hour line (1.05 days) and its refill, harbor's weekly refill (0.7 hours), and each ends at its account's numbers.
# The work by hour follows the readings (an hour's work is what its readings climbed), on a quiet daily rhythm, so
# the one clear spike is the hour harbor's week climbed to its line.
WEEK_READINGS = {
    'maya@northwind.example': {
        'five': [(72, 0), (45.6, 0), (40.6, 48), (40.6, 0), (39, 0), (34, 36), (34, 0), (26, 0), (21, 40), (21, 0),
                 (18.4, 0), (14.4, 92), (13.4, 92), (13.4, 0), (8.4, 0), (3.4, 70), (3.4, 0), (3, 0), (0, 62)],
        'week': [(72, 47), (45.6, 49), (14.4, 64), (8.4, 64), (0, 70)]},
    'maya.reyes@mail.example': {'week': [(72, 91), (0, 91)]},
    'maya@fernhill.example': {'five': [(72, 0), (0, 0)], 'week': [(72, 23), (0, 23)]},
    'm.reyes@harbor.example': {
        'five': [(72, 0), (14.4, 0), (9.4, 55), (9.4, 0), (8.4, 30), (4.4, 30), (4.4, 0), (0, 0)],
        'week': [(72, 78), (14.4, 80), (9.6, 86), (8.64, 95), (8.4, 95.4), (0.7, 95.4), (0.7, 5), (0, 5)]},
    'maya@ivy.example': {
        'five': [(72, 0), (30, 0), (25.2, 90), (24.9, 91), (19.68, 91), (19.68, 0), (3, 0), (0, 20)],
        'week': [(72, 56), (30, 57), (24.9, 62), (0, 65)],
        'Fable': [(72, 71), (30, 72), (24.9, 77), (0, 80)]},
}
RHYTHM = (0.9, 1.0, 0.8, 0.9, 1.0, 0.7, 0.6, 0.9, 1.0, 0.9, 0.8, 0.6, 0.5, 0.4, 0.3, 0.2, 0, 0, 0, 0, 0, 0, 0.3, 0.6)
UNIT = 2400000.0            # weighted tokens in a busy hour
WEEK_PROJECTS = (('Website', 0.73), ('Mobile app', 0.13), ('Notes', 0.04), ('Design system', 0.021),
                 ('Onboarding', 0.021), ('Billing', 0.02), ('Security review', 0.02), ('Release notes', 0.018))
WEEK_MODELS = (('Opus', 0.72), ('Sonnet', 0.20), ('Fable', 0.08))
WEEK_KINDS = (('not labelled', 0.54), ('build', 0.21), ('review', 0.11), ('research', 0.09), ('fix', 0.05))
WEEK_FLOWS = (('website-rebuild', 'Website', 0.19, 48, ['Build', 'Review', 'Fix']),
              ('mobile-release', 'Mobile app', 0.12, 36, ['Build', 'Tests']),
              ('site-copy-pass', 'Website', 0.09, 30, ['Draft', 'Review']),
              ('onboarding-flow', 'Onboarding', 0.07, 22, ['Build', 'Verify']),
              ('design-tokens', 'Design system', 0.06, 18, ['Build']),
              ('checkout-fixes', 'Website', 0.05, 16, ['Fix', 'Verify']),
              ('notes-digest', 'Notes', 0.04, 12, ['Research', 'Draft']),
              ('perf-audit', 'Website', 0.03, 10, ['Review']),
              ('store-screens', 'Mobile app', 0.03, 9, ['Build']),
              ('a11y-check', 'Website', 0.02, 8, ['Review']))


def _curve(points, age):
    """The percent `age` hours ago on a line through the points (the earlier one where two share a time)."""
    if age >= points[0][0]:
        return points[0][1]
    for (a0, v0), (a1, v1) in zip(points, points[1:]):
        if a0 >= age >= a1 and a0 > a1:
            return v0 + (v1 - v0) * (a0 - age) / (a0 - a1)
    return points[-1][1]


def _climbed(points, older, newer):
    """How far the line rose between two ages (refills not counted), in 5-minute steps."""
    up, steps = 0.0, max(1, int(round((older - newer) * 12)))
    for i in range(steps):
        a, b = older - (older - newer) * i / steps, older - (older - newer) * (i + 1) / steps
        up += max(0.0, _curve(points, b) - _curve(points, a))
    return up


def _demo_readings(now):
    """Made-up readings log rows by account: a reading every quarter hour and at each point, a row only when a number
    changed (as the log keeps them)."""
    out = {}
    for email, lines in WEEK_READINGS.items():
        ages = set(i / 4.0 for i in range(72 * 4 + 1))
        for points in lines.values():
            for (a0, _), (a1, _) in zip(points, points[1:]):
                ages.update([a0] if a0 > a1 else [a0, a1 - 1 / 60.0])      # a refill: the peak, then a minute on
        rows = []
        for age in sorted((a for a in ages if a >= 0), reverse=True):
            row = {'t': round(now - age * H), 'five': insights._pct(_curve(lines['five'], age)) if 'five' in lines else None,
                   'week': insights._pct(_curve(lines['week'], age)),
                   'models': dict((k, insights._pct(_curve(v, age))) for k, v in lines.items() if k not in ('five', 'week'))}
            if not rows or (rows[-1]['five'], rows[-1]['week'], rows[-1]['models']) != (row['five'], row['week'], row['models']):
                rows.append(row)
        out[email] = rows
    return out


def _week(now):
    """The made-up week, through insights.week: 8 days and the hours since the first, last week, the tokens, the
    workflows, the made-up History's switches and limits, and the readings."""
    from .usage import FAMILIES, KINDS, WEIGHTS, WHO, WHO_LABEL, WRITE_1H, hour_start
    start = insights.series_start(now)
    spike = hour_start(now - 0.36 * D - 600)        # the hour harbor's week reached its line (History: 0.36 days ago)
    hours, h = [], hour_start(start)
    while h < now:
        age = (now - (h + 1800)) / H                 # hours ago, at the middle of the hour
        units = RHYTHM[int(age) % 24] * (0.85 + 0.3 * ((int(age) * 7) % 5) / 4.0)
        if age < 72:                                 # the readings' 3 days: the work is what they climbed
            older, newer = min(72.0, (now - h) / H), max(0.0, (now - h - H) / H)
            five = sum(_climbed(x['five'], older, newer) for x in WEEK_READINGS.values() if 'five' in x)
            week = sum(_climbed(x['week'], older, newer) for x in WEEK_READINGS.values())
            units = 0.6 * units + 0.045 * five + 0.25 * week
        hours.append({'t': h, 'units': units})
        h += H
    used = [x['units'] for x in hours if x['units'] > 0 and x['t'] != spike]
    for x in hours:
        if x['t'] == spike:                          # the one clear spike: 8x an average hour, and well past the next
            x['units'] = max(8 * sum(used) / len(used), 2.5 * max(used))
        split = (0.02, 0.05, 0.93) if x['t'] == spike else (0.09, 0.10, 0.81)
        x['weight'] = round(UNIT * x.pop('units'), 2)
        x['who'] = dict(zip(WHO, (x['weight'] * p for p in split)))
        if x['weight'] > 0:                          # where the hour went, as the reader says it for a real one
            w = x['weight']
            mix = (('Website', 0.9), ('Mobile app', 0.1)) if x['t'] == spike else WEEK_PROJECTS
            written = int(w * 0.36 / WRITE_1H)
            ran = int(w * (split[1] + split[2]) / 150000)        # helpers and workflow agents at work in it
            x.update(projects=dict((label, w * p) for label, p in mix),
                     models=dict((m, w * dict(WEEK_MODELS).get(m, 0)) for m in FAMILIES),
                     kinds=dict((k, w * dict(WEEK_KINDS).get(k, 0)) for k in KINDS),
                     tokens={'input': int(w * 0.02), 'output': int(w * 0.20 / WEIGHTS['output']),
                             'cache_write': written, 'cache_read': int(w * 0.42 / WEIGHTS['cache_read']),
                             'cache_write_1h': written, 'cache_read_low': 0},     # every read at 0.05, as on Opus
                     tools=int(w / 20000), agents=['a%d-%d' % (x['t'], i) for i in range(ran)],
                     runs=['r%d-%d' % (x['t'], i) for i in range(min(3, (ran + 5) // 6))])
    days = {}
    for x in hours:
        d = days.setdefault(datetime.fromtimestamp(x['t']).strftime('%Y-%m-%d'), {'weight': 0.0, 'who': {}})
        d['weight'] += x['weight']
        for w_, v in x['who'].items():
            d['who'][w_] = d['who'].get(w_, 0.0) + v
    rows = []
    for i, (name, d) in enumerate(sorted(days.items())):
        lean = 0.04 * (i % 3 - 1)                    # Website and Mobile app trade a little from day to day
        mix = dict(WEEK_PROJECTS, Website=WEEK_PROJECTS[0][1] + lean, **{'Mobile app': WEEK_PROJECTS[1][1] - lean})
        weight, who = d['weight'], d['who']
        rows.append({'day': name, 'weight': weight, 'who': who,
                     'projects': dict((label, weight * p) for label, p in mix.items()),
                     'models': dict((m, weight * dict(WEEK_MODELS).get(m, 0)) for m in FAMILIES),
                     'kinds': dict((x, weight * dict(WEEK_KINDS).get(x, 0)) for x in KINDS)})
    total = sum(x['weight'] for x in hours[-insights.HOURS:])
    # weighed at today's prices, every write a 1-hour one (as Claude Code's are): 42% re-reading, 36% saving, 20% writing
    written = int(total * 0.36 / WRITE_1H)
    tokens = {'input': int(total * 0.02), 'output': int(total * 0.20 / WEIGHTS['output']), 'cache_write': written,
              'cache_read': int(total * 0.42 / WEIGHTS['cache_read']), 'cache_write_1h': written,
              'cache_read_low': 0}                            # every read at 0.05, as on Opus: none at Fable's 0.025
    split = {'chat': 0.09, 'helpers': 0.10, 'workflows': 0.81}
    who = lambda weight, parts: [{'label': WHO_LABEL[k], 'detail': {'weight': weight * parts[k]}} for k in WHO]  # noqa: E731
    last = total / 1.31                              # last week: 31% less, mostly workflows (they ran fewer)
    summary = {'window': {'since': now - 7 * D, 'until': now},
               'totals': {'weight': total, 'tokens': tokens, 'workflows': 32, 'workflow_agents': 410, 'helper_agents': 57},
               'who': who(total, split),
               'workflows': [{'name': n, 'chat': c, 'share': s, 'weight': total * s, 'agents': a, 'phases': p}
                             for n, c, s, a, p in WEEK_FLOWS],
               'series': {'since': start, 'until': now, 'hours': hours, 'days': rows}}
    then = {'chat': 0.12, 'helpers': 0.12, 'workflows': 0.76}
    last_days = [{'day': 'last week, day %d' % i, 'weight': last / 7} for i in range(7)]       # use on each of its days
    return insights.week(summary, now, last={'totals': {'weight': last}, 'who': who(last, then), 'days': last_days},
                         lines=STATE.get('history_lines'), readings=_demo_readings(now))


# Made-up past chats for the Memory page: title, days since its last message, days since the best match (a long
# chat's match can be far back), look-alikes folded in, folder, the command's folder part, two lines from the chat.
CHATS = [
    ('Fix the login loop on Safari', 1.2, 9.0, 0, '~/work/website', 'cd /Users/me/work/website',
     ['the redirect after \x02login\x03 sends Safari back to /start', 'cookie is set on the wrong path, so the \x02login\x03 never sticks']),
    ('Pricing page copy, second pass', 2.5, None, 0, '~/work/website', 'cd /Users/me/work/website',
     ['shorter lines for the three plans', 'keep the yearly price under the monthly one']),
    ('Release checklist for the mobile app', 3.1, None, 0, '~/work/mobile', 'cd /Users/me/work/mobile',
     ['bump the build number before the \x02release\x03', 'screenshots for the store at 6.7 inch']),
    ('Why the nightly import is slow', 4.8, None, 0, '~/work/data', 'cd /Users/me/work/data',
     ['the import reads every row twice', 'an index on created_at took it from 40 min to 3']),
    ('Login rate limits for the API', 6.0, None, 0, '~/work/api', 'cd /Users/me/work/api',
     ['five tries a minute per address for \x02login\x03', 'a 429 with retry-after, never a silent drop']),
    ('Draft: weekly notes', 9.4, None, 3, '~/notes', 'cd /Users/me/notes',
     ['what shipped, what slipped, what is next', 'the \x02release\x03 moved to Thursday']),
]


def memory(query=''):
    """What the Memory page shows in the demo: the same shape GET /api/memory answers."""
    words = [w for w in (query or '').lower().split() if len(w) > 2]
    now = time.time()
    day = lambda age: datetime.fromtimestamp(now - age * D).strftime('%Y-%m-%d')    # noqa: E731
    found = [c for c in CHATS if words and any(w in (c[0] + ' ' + ' '.join(c[6])).lower() for w in words)]
    return {'state': 'ready', 'updating': False, 'query': query, 'demo': True,
            'health': "Claude Code's memory for work-website is over its limit: 38 lines of it do not load at the start "
                      'of a chat.',
            'results': [{'title': t, 'date': day(age), 'folder': f, 'snippets': list(snip),
                         'resume': '%s && claude --resume 6f1c%04d-0000-4000-8000-000000000000' % (cd, i),
                         'matched': day(m if m is not None else age), 'alike': alike,
                         'alike_last': day(age + 7) if alike else None}
                        for i, (t, age, m, alike, f, cd, snip) in enumerate(found)]}


def claudecode():
    """The sheet's Claude Code switches in the demo: the same shape GET /api/claudecode answers."""
    _ready()
    from .packs import codemap, houserules, memory as mem
    steps = lambda xs: [{'step': x.get('step'), 'run': x.get('run'), 'or': x.get('or'), 'then': x.get('then')} for x in xs]  # noqa: E731
    p = FACTS['packs']
    return {'statusline': {'on': FACTS['mac']['statusline'], 'theirs': FACTS['mac'].get('statusline_theirs')},
            'house': {'on': p['house'], 'state': 'on' if p['house'] else 'off', 'lines': houserules.size()['lines'],
                      'tokens': houserules.size()['tokens_estimate'], 'file': '~/.claude/CLAUDE.md', 'text': houserules.text()},
            'codemap': {'on': p['codemap'], 'state': 'on' if p['codemap'] else 'off', 'found': ['graphify'],
                        'nothing_to_add': False, 'add': steps(codemap.ADD_CODEGRAPH)},
            'claudemem': {'on': p['claudemem'], 'installed': True, 'turned_off_in_claude': False, 'cap': p['cap'],
                          'paused': False, 'helpers_dropped': p['claudemem'], 'watching': True,
                          'install': steps(mem.CM_INSTALL), 'remove': steps(mem.CM_REMOVE)}}


def keys():
    """The keys and the gateway in the demo: the same shape GET /api/keys answers."""
    _ready()
    on = FACTS['keys']['on']
    lanes = [('console', 'anthropic', '9f2c', 41, 2, 1_204_000, 88_000), ('openrouter', 'openrouter', 'a71b', 6, 0, 150_000, 9_400)]
    return {'on': on, 'port': 7797, 'command': 'switchboard claude', 'wanted': on,
            'lanes': [{'name': n, 'kind': k, 'last4': l4,
                       'counts': {'answered': a, 'passed_on': f, 'errors': 0, 'tokens_in': ti, 'tokens_out': to} if on else None}
                      for n, k, l4, a, f, ti, to in lanes[:FACTS['keys']['count']]]}


def models():
    """What the Models page shows in the demo: the same shape GET /api/models answers. The made-up Mac's Claude Code
    reads the effort line, so Sonnet for helpers is at high effort."""
    _ready()
    return {'enabled': FACTS['models'], 'decided_by': None, 'blocked': None, 'model': 'Sonnet', 'effort': 'high',
            'helpers': {'enabled': bool(FACTS.get('helpers')), 'blocked': None, 'left_out': []}, 'effort_level': None,
            'moved': 37,
            'exact': True, 'log': [{'helper': 'Workflow steps', 'share': 81, 'models': [{'family': 'Opus', 'share': 76},
                                                                                       {'family': 'Sonnet', 'share': 18}]},
                                   {'helper': 'Helpers', 'share': 10, 'models': [{'family': 'Opus', 'share': 85},
                                                                                {'family': 'Sonnet', 'share': 15}]},
                                   {'helper': 'Explore', 'share': 3, 'models': [{'family': 'Opus', 'share': 100}]},
                                   {'helper': 'Forks', 'share': 2, 'models': [{'family': 'Opus', 'share': 100}]}]}


def more():
    """What the More area shows in the demo: the same shape GET /api/more answers."""
    return {'status': ['Switchboard %s keeps 5 accounts.' % server.VERSION]}


def act(path, body):
    """Change the in-memory copy where that is harmless; everything else just says it is the demo."""
    _ready()
    email = (body.get('email') or '').lower()
    if path == '/api/switch' and email in STATE['accounts']:
        set_active(STATE, email)
        event(STATE, 'Switched Claude Code to %s' % email, 'switch')
    elif path == '/api/auto':
        for k in ('session_at', 'weekly_at', 'model_at'):
            server.number(body, k)              # the same refusal as the real page gets
        for k in ('enabled', 'session_at', 'weekly_at', 'model_at'):
            if body.get(k) is not None:
                FACTS['auto'][k] = body[k]
        if body.get('enabled') is not None:
            FACTS['auto']['on'] = bool(body['enabled'])
        if body.get('strategy') in engine.STRATEGIES:
            FACTS['auto']['strategy'] = body['strategy']
        if isinstance(body.get('model'), str):          # one model's week counts, or none
            model = body['model'].strip() or None
            FACTS['auto'].update(model=model, models=engine.model_names(model or ''))
        if isinstance(body.get('model'), list):         # several, ticked on the page
            models = engine.check_models(body['model']) or []
            models = [models] if isinstance(models, str) else models
            FACTS['auto'].update(model=models[0] if models else None, models=models)
        if 'rule' in body:                              # one account's rule, in memory, as the real page refuses
            rule, who = body.get('rule'), body.get('account')
            if rule not in engine.RULES:
                raise SwitchError(engine.RULE_WORDS)
            a = next((x for x in STATE['accounts'].values() if who and who in (x.get('id'), x.get('email'))), None)
            if a is None:
                raise SwitchError('That account is not saved in Switchboard.')
            rules = dict((k, v) for k, v in (FACTS['auto'].get('rules') or {}).items() if k != a['id'])
            FACTS['auto']['rules'] = dict(rules, **({a['id']: rule} if rule != 'in' else {}))
        if 'own_lines' in body or 'rank' in body:       # its own lines or rank, in memory, checked as on the real page
            line = engine.check_own((None, body['own_lines'])) if 'own_lines' in body else None
            rank = engine.check_rank((None, body['rank'])) if 'rank' in body else None
            who = body.get('account')
            a = next((x for x in STATE['accounts'].values() if who and who in (x.get('id'), x.get('email'))), None)
            if a is None:
                raise SwitchError('That account is not saved in Switchboard.')
            if line is not None:
                own = dict((FACTS['auto'].get('own_lines') or {}).get(a['id']) or {})
                for k, v in (line[1] or dict.fromkeys(engine.OWN_KEYS)).items():
                    if v is None:
                        own.pop(k, None)
                    else:
                        own[k] = v
                lines = dict((k, v) for k, v in (FACTS['auto'].get('own_lines') or {}).items() if k != a['id'])
                FACTS['auto']['own_lines'] = dict(lines, **({a['id']: own} if own else {}))
            if rank is not None:
                ranks = dict((k, v) for k, v in (FACTS['auto'].get('ranks') or {}).items() if k != a['id'])
                FACTS['auto']['ranks'] = dict(ranks, **({a['id']: rank[1]} if rank[1] is not None else {}))
    elif path == '/api/continue':                   # the made-up OpenAI row: nothing opens
        raise SwitchError("The demo's OpenAI row is made up, so nothing opens.")
    elif path == '/api/models':
        FACTS['helpers' if body.get('part') == 'helpers' else 'models'] = bool(body.get('enabled'))
    elif path == '/api/links':
        if 'on' in body:
            FACTS['links']['on'] = bool(body['on'])
        if 'home' in body:
            FACTS['links']['home'] = body['home']
    elif path == '/api/profile' and email in STATE['accounts'] and body.get('profile') in FACTS['profiles']:
        STATE['accounts'][email].update(profile=body['profile'], profile_src='you')
        STATE['accounts'][email].pop('signed_out', None)
    elif path == '/api/setup':
        FACTS['setup_hidden'] = bool(body.get('hidden'))
    elif path in ('/api/awake', '/api/notify', '/api/statusline'):
        FACTS['mac'][path.rsplit('/', 1)[1]] = bool(body.get('on'))
    elif path == '/api/pack' and body.get('pack') in FACTS['packs']:
        if 'on' in body:
            FACTS['packs'][body['pack']] = body.get('on') is True
        if isinstance(body.get('cap'), (int, float)) and 0 < body['cap'] <= 100:
            FACTS['packs']['cap'] = body['cap']
    elif path == '/api/gateway':
        FACTS['keys']['on'] = body.get('on') is True
    elif path == '/api/keys':
        body.pop('key', None)
        return {'demo': True, 'keys': keys()}
    elif path == '/api/go':
        time.sleep(0.5)
        item = next((x for x in STATE['artifacts'] if x['url'] == body.get('url')), None) or STATE['artifacts'][0]
        owner = item.get('owner') or STATE['active']
        item.update(owner=owner, src='seen')
        return {'email': owner, 'name': name_of(STATE, owner), 'checked': True, 'title': item['title'], 'tried': 1,
                'demo': True}
    elif path == '/api/signins':
        return {'demo': True, 'signins': _signins_act(body.get('do'))}
    elif path == '/api/check':
        return {'tabs': True, 'demo': True, 'results': [
            {'email': e, 'result': 'signin' if a.get('signed_out') else 'ok'} for e, a in STATE['accounts'].items()]}
    return {'demo': True}


def _signins_act(what):
    """Sign in to all, pressed in the demo: it shows the first account being signed in and stops there (no sign-in
    runs, nothing opens); Stop ends it with its line; Hide takes the line away."""
    from . import signins
    now = time.time()
    lists = signins.due(server.signins_accounts(STATE), now)
    due = lists['due']
    q = FACTS.get('signins') or {}
    if what == 'start' and q.get('state') != 'running' and due:
        FACTS['signins'] = dict({'state': 'running', 'current': due[0], 'waiting': False, 'done': 0, 'of': len(due),
                                 'emails': due, 'kept': [], 'skipped': []}, **lists)
    elif what == 'stop' and q.get('state') == 'running':
        FACTS['signins'] = dict({'state': 'ended', 'line': 'Stopped. Signed in again to 0 of %d.' % q.get('of', 0),
                                 'kept': [], 'skipped': []}, **lists)
    elif what == 'hide':
        FACTS.pop('signins', None)
    return _signins(now)
