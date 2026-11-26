"""A stand-in for switchboard.accounts.api in Switchboard's tests: made-up accounts, nothing real.

    from fakes_accounts_api import install
    fake = install(self)            # every api function is the fake's own until the test ends (addCleanup)
    fake.hold('ana@work.example', org_id='org-work')
    fake.sign_in('ana@work.example', org_id='org-work')     # Claude Code's identity block, in this test's home

It never touches the Keychain, the network or a real home: Claude Code's "live login" is the identity block in
~/.claude.json of the test's own home (the one tests/_isolate.py made), read and written the way the app reads it.
Every call is noted in fake.calls as (name, args...). fake.fail[name] = an AccountsError (or a callable returning
one) makes that function raise it.
"""
import json
import os
import time

from switchboard import core
from switchboard.accounts import api

NAMES = ('accounts', 'live', 'find', 'switch', 'add_current', 'remove', 'set_alias', 'reorder', 'run_as', 'keep_pass',
         'upkeep_pass', 'resolve_journal', 'health', 'readings', 'feed_status_line', 'activity', 'decide',
         'auto_settings', 'set_auto', 'auto_status', 'request_need_switch')
# add_login: a login signed in to outside Claude Code's live one (the page's Add). remove_all: the uninstaller's --all
# (every login of Switchboard's left in the Keychain). remove_sign_in: a sign-in folder's own Keychain item, removed
# through the engine.
OPTIONAL = ('add_login', 'remove_all', 'remove_sign_in', 'best')


def account_id(email, org_id=''):
    """A made-up Anthropic account id for an email in an organization (stable, so tests can name it)."""
    return 'acct-%s%s' % (email.lower().replace('@', '-at-'), ('-' + org_id) if org_id else '')


def iso(t):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


class FakeAccounts(object):
    def __init__(self):
        self.saved = []             # [{id, email, org_id, org_name, plan, alias, status, ...}] in the person's order
        self.by_id = {}
        self.read = {}              # id -> api.Reading
        self.auto = {'enabled': False, 'session_at': 90.0, 'weekly_at': 95.0, 'model_at': 95.0, 'model': None,
                     'strategy': 'refills-first'}
        self.status = {'pid': None, 'stage': None, 'last_pass_at': None, 'stuck_for_s': None, 'lease': None,
                       'waiting_for': None}
        self.calls = []
        self.fail = {}
        self.live_state = None      # an api.Live to answer with; None: from the identity block
        self.fed = []
        self.journal_note = None
        self.keep_note = None
        self.health_extra = {}
        self.decision = api.Decision(action='stay', reason='')
        self.need_result = None
        self.run_code = 0
        self.run_plain = None       # True or False: run_as calls before(it), as the real one does just before starting
        self.run_kw = None
        self.activity_ = {'busy': False, 'idle_for_s': None, 'sessions': [], 'obligations': []}

    # ---------- setting up ----------

    def hold(self, email, org_id='', id=None, plan='Max 20x', org_name='', status='ok', alias=None,
             refresh_expires_at=None, tier=None, org_type=None):
        """A saved account. Returns its id."""
        email = email.lower()
        aid = id or account_id(email, org_id)
        if aid in self.by_id:
            return aid
        rec = {'id': aid, 'email': email, 'org_id': org_id or None, 'org_name': org_name or None, 'plan': plan,
               'alias': alias, 'status': status, 'refresh_expires_at': refresh_expires_at, 'added_at': time.time(),
               'fingerprint': 'fp-' + aid, 'tier': tier, 'org_type': org_type}
        self.saved.append(rec)
        self.by_id[aid] = rec
        return aid

    def sign_in(self, email, org_id='', id=None, org_name=''):
        """Claude Code signs in: its identity block in ~/.claude.json names this account (other keys kept)."""
        path = core.claude_json()
        data = core.read_json(path)
        data['oauthAccount'] = {'emailAddress': email, 'organizationUuid': org_id,
                                'accountUuid': id or account_id(email.lower(), org_id),
                                'organizationName': org_name}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            json.dump(data, f)

    def sign_out(self):
        path = core.claude_json()
        data = core.read_json(path)
        data.pop('oauthAccount', None)
        with open(path, 'w') as f:
            json.dump(data, f)

    def reading(self, who, five=None, week=None, models=None, at=None, source='endpoint', frozen=False, error=None):
        """An account's numbers: five/week = (pct, resets_at epoch) or None; models = {name: (pct, resets_at)}."""
        aid = self._id(who)
        wins = []
        if five is not None:
            wins.append(api.Window('5-hour', five[0], five[1]))
        if week is not None:
            wins.append(api.Window('weekly', week[0], week[1]))
        for name, (pct, resets) in sorted((models or {}).items()):
            wins.append(api.Window('model:' + name, pct, resets))
        self.read[aid] = api.Reading(aid, tuple(wins), time.time() if at is None else at, source, frozen, error)
        return self.read[aid]

    def _id(self, who):
        if who in self.by_id:
            return who
        for r in self.saved:
            if r['email'] == str(who).lower():
                return r['id']
        return account_id(str(who).lower())

    def _raise(self, name):
        e = self.fail.get(name)
        if e is not None:
            raise e() if callable(e) and not isinstance(e, BaseException) else e

    def _note(self, name, *args):
        self.calls.append((name,) + args)
        self._raise(name)

    def block(self):
        return core.read_json(core.claude_json()).get('oauthAccount') or {}

    # ---------- the api ----------

    def accounts(self):
        self._note('accounts')
        return [api.Account(id=r['id'], email=r['email'], org_id=r['org_id'], org_name=r['org_name'], plan=r['plan'],
                            alias=r['alias'], order=i + 1, status=r['status'],
                            refresh_expires_at=r['refresh_expires_at'], added_at=r['added_at'],
                            fingerprint=r['fingerprint'], tier=r.get('tier'), org_type=r.get('org_type'))
                for i, r in enumerate(self.saved)]

    def live(self):
        self._note('live')
        if self.live_state is not None:
            return self.live_state
        b = self.block()
        email = (b.get('emailAddress') or '').lower()
        if not email:
            return api.Live(state=api.ABSENT, account_id=None, email=None, proof='none')
        org = b.get('organizationUuid') or ''
        held = next((r for r in self.saved if r['email'] == email and (r['org_id'] or '') == org), None)
        aid = held['id'] if held else (b.get('accountUuid') or account_id(email, org))
        return api.Live(state=api.PRESENT, account_id=aid, email=email, proof='fingerprint' if held else 'label')

    def find(self, target):
        self._note('find', target)
        t = str(target).strip().lower()
        hits = [r for r in self.saved if t in (r['id'].lower(), r['email'], (r['alias'] or '').lower())]
        if not hits and t.isdigit() and 1 <= int(t) <= len(self.saved):
            hits = [self.saved[int(t) - 1]]
        if not hits:
            raise api.AccountsError('not_found', 'No account called %s. Run switchboard to see them.' % target)
        if len(hits) > 1:
            raise api.AccountsError('ambiguous', '%s matches more than one account; use its number.' % target)
        h = hits[0]
        return [a for a in self.accounts() if a.id == h['id']][0]

    def switch(self, target, kind='manual', source='cli', reason=''):
        self.calls.append(('switch', target, kind, source, reason))
        self._raise('switch')
        to = self.find(target)
        frm = self.live().account_id
        rec = self.by_id[to.id]
        self.sign_in(rec['email'], rec['org_id'] or '', id=rec['id'], org_name=rec['org_name'] or '')
        return api.SwitchResult(ok=True, from_id=frm, to_id=to.id, kind=kind, source=source,
                                reason=reason or 'Switched to %s.' % rec['email'])

    def add_current(self):
        self._note('add_current')
        b = self.block()
        email = (b.get('emailAddress') or '').lower()
        if not email:
            raise api.AccountsError('unreadable_live', 'Claude Code is not signed in. Run /login in Claude Code first.')
        aid = self.hold(email, b.get('organizationUuid') or '', id=b.get('accountUuid') or None,
                        org_name=b.get('organizationName') or '')
        return [a for a in self.accounts() if a.id == aid][0]

    def add_login(self, config_dir):
        """The login a throwaway config folder holds: its identity block names the account (the fake reads no
        Keychain: a folder whose block names nobody holds no login)."""
        self._note('add_login', config_dir)
        block = core.read_json(os.path.join(config_dir, '.claude.json')).get('oauthAccount') or {}
        email = (block.get('emailAddress') or '').lower()
        if not email:
            raise api.AccountsError('unreadable_live', 'That sign-in left no login behind. Try again.')
        aid = self.hold(email, block.get('organizationUuid') or '', id=block.get('accountUuid') or None,
                        org_name=block.get('organizationName') or '')
        return [a for a in self.accounts() if a.id == aid][0]

    def remove_sign_in(self, config_dir):
        """A sign-in folder's Keychain item (the fake keeps none): noted only."""
        self._note('remove_sign_in', config_dir)
        return True

    def remove(self, target, everything=False):
        self._note('remove', target)
        a = self.find(target)
        rec = self.by_id.pop(a.id)
        self.saved.remove(rec)
        self.read.pop(a.id, None)
        self._rule(a.id, 'in')                  # its rule in auto-switch goes with it, as the real one's does

    def remove_all(self):
        """The uninstaller's --all: every account still held goes (the fake keeps no other login). Its lines."""
        self._note('remove_all')
        said = ['Account %s: removed, with its saved login.' % r['email'] for r in self.saved]
        self.saved[:] = []
        self.by_id.clear()
        self.read.clear()
        return said

    def set_alias(self, target, alias):
        self._note('set_alias', target, alias)
        a = self.find(target)
        self.by_id[a.id]['alias'] = alias

    def reorder(self, ids):
        self._note('reorder', list(ids))
        self.saved.sort(key=lambda r: list(ids).index(r['id']) if r['id'] in ids else len(ids))

    def run_as(self, target, argv, cwd=None, env=None, timeout=None, bare=False, before=None, say=None, apart=False,
               strict=False):
        self._note('run_as', target, list(argv))
        self.run_bare = bare
        self.run_kw = {'cwd': cwd, 'bare': bare, 'apart': apart, 'strict': strict}
        if before is not None and self.run_plain is not None:
            before(self.run_plain)
        return self.run_code

    def keep_pass(self):
        self._note('keep_pass')
        note, self.keep_note = self.keep_note, None
        return note

    def upkeep_pass(self, budget_s=60.0):
        self._note('upkeep_pass', budget_s)
        return []

    def resolve_journal(self):
        self._note('resolve_journal')
        note, self.journal_note = self.journal_note, None
        return note

    def health(self):
        self._note('health')
        return dict({'keychain': 'ok', 'facts': 'ok', 'journal': None, 'store': 1, 'claims': []}, **self.health_extra)

    def readings(self):
        self._note('readings')
        return dict(self.read)

    def feed_status_line(self, account_id, rate_limits, at):
        self._note('feed_status_line', account_id)
        self.fed.append((account_id, rate_limits, at))

    def activity(self):
        self._note('activity')
        return dict(self.activity_)

    def decide(self, now=None):
        self._note('decide')
        return self.decision

    def auto_settings(self):
        self._note('auto_settings')
        return dict(self.auto)

    def set_auto(self, enabled=None, session_at=None, weekly_at=None, model_at=None, model=None, strategy=None,
                 rule=None, line=None, rank=None):
        self.calls.append(('set_auto', dict((k, v) for k, v in (
            ('enabled', enabled), ('session_at', session_at), ('weekly_at', weekly_at), ('model_at', model_at),
            ('model', model), ('strategy', strategy), ('rule', rule), ('line', line), ('rank', rank))
            if v is not None)))
        self._raise('set_auto')
        if strategy is not None and strategy not in ('refills-first', 'most-room', 'stay-then-refills'):
            raise api.AccountsError('other', 'Unknown order.')
        for each in (line, rank):               # an account's own lines or rank: the real one's refusal for a stranger
            if each is not None and each[0] not in self.by_id:
                raise api.AccountsError('not_found', 'That account is not saved in Switchboard.')
        if line is not None:
            self._own(line[0], line[1])
        if rank is not None:
            self._each('ranks', rank[0], rank[1])
        if rule is not None:                    # the real one's refusals (autoloop.save_settings)
            if not isinstance(rule, (tuple, list)) or len(rule) != 2 or rule[1] not in ('in', 'last', 'out'):
                raise api.AccountsError('other', 'Pick in, last or out for an account.')
            if rule[0] not in self.by_id:
                raise api.AccountsError('not_found', 'That account is not saved in Switchboard.')
            self._rule(rule[0], rule[1])
        for k, v in (('enabled', enabled), ('session_at', session_at), ('weekly_at', weekly_at),
                     ('model_at', model_at), ('strategy', strategy)):
            if v is not None:
                self.auto[k] = v
        if model is not None:
            self.auto['model'] = (model[0] if isinstance(model, list) else model) or None
            if isinstance(model, list):
                self.auto['models'] = list(model)
            else:
                self.auto.pop('models', None)
        return dict(self.auto)

    def _each(self, key, aid, value):
        """One account's entry in a setting kept for each account ('ranks', 'own_lines'), kept as the real store keeps
        it: the key only while an account has one."""
        got = dict(self.auto.get(key) or {})
        got.pop(aid, None)
        if value:
            got[aid] = value
        if got:
            self.auto[key] = got
        else:
            self.auto.pop(key, None)

    def _own(self, aid, lines):
        mine = dict((self.auto.get('own_lines') or {}).get(aid) or {})
        for k, v in (lines or {'session_at': None, 'weekly_at': None}).items():
            if v is None:
                mine.pop(k, None)
            else:
                mine[k] = v
        self._each('own_lines', aid, mine)

    def best(self):
        """policy.best on the fake's own accounts, readings, settings and live login, as the real one gathers them."""
        self._note('best')
        from switchboard.accounts import policy
        snap = policy.Snapshot(now=time.time(), accounts=tuple(self.accounts()), readings=dict(self.read),
                               settings=policy.settings_from(dict(self.auto)), active=self.live().account_id)
        return policy.best(snap)

    def _rule(self, aid, rule):
        """An account's rule in auto-switch, kept as the real store keeps it: 'rules' only while one is set."""
        rules = dict(self.auto.get('rules') or {})
        rules.pop(aid, None)
        if rule != 'in':
            rules[aid] = rule
        if rules:
            self.auto['rules'] = rules
        else:
            self.auto.pop('rules', None)

    def auto_status(self):
        self._note('auto_status')
        return dict(self.status)

    def request_need_switch(self, account_id, why):
        self._note('request_need_switch', account_id, why)
        return self.need_result


def install(test, fake=None):
    """Put the fake in the api's place for the length of `test` (a unittest.TestCase). Returns it."""
    fake = fake or FakeAccounts()
    for name in NAMES + OPTIONAL:
        had = hasattr(api, name)
        orig = getattr(api, name, None)
        setattr(api, name, getattr(fake, name))
        if had:
            test.addCleanup(setattr, api, name, orig)
        else:
            test.addCleanup(delattr, api, name)
    return fake
