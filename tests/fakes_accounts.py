"""Fakes for the engine tests: a stand-in for /usr/bin/security, Anthropic's token and profile endpoints, a
made-up home with Claude Code's files, and a process table. Nothing here touches the real Keychain, network or home.

FakeSecurity writes a small program (never named "security": tests may not run that) that keeps its items in a JSON
file, follows scripted rules (exit codes, delays, how many times), counts how many copies run at once, and logs each
call (verb, service, account, whether it came on standard input or in the arguments; never a value). Like the real
tool, `-i` runs one command per line of its standard input, cuts a line past 4095 bytes short without a word, and
says "<command>: returned N" on its error stream when a command fails.

FakeAnthropic models what matters about refresh tokens: each works once; a refresh retires it and issues the next;
a cut-off answer still retires it (the server acted, the caller never heard). profile answers for live access tokens.
"""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import shutil
import sys
import tempfile
import threading
import time

from switchboard.accounts import context, facts, keychain, net

_SCRIPT = r'''#!%(python)s
import fcntl, json, os, sys, time
STATE = %(state)r
def locked(name):
    fd = os.open(os.path.join(STATE, name + '.lock'), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd
def load(name, default):
    try:
        with open(os.path.join(STATE, name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default
def save(name, obj):
    tmp = os.path.join(STATE, name + '.tmp%%d' %% os.getpid())
    with open(tmp, 'w') as f:
        json.dump(obj, f)
    os.replace(tmp, os.path.join(STATE, name))
def count(delta):
    fd = locked('conc')
    try:
        c = load('conc.json', {'now': 0, 'max': 0})
        c['now'] += delta
        c['max'] = max(c['max'], c['now'])
        save('conc.json', c)
    finally:
        os.close(fd)
LINE_MAX = 4096        # security -i reads each command into a buffer this size: a longer line is cut short, silently
def split(line):
    """security -i's word splitting, as far as these tests need it: words apart by blanks, "quoted" words whole."""
    out, cur, word, quoted, i = [], [], False, False, 0
    while i < len(line):
        ch = line[i]
        if quoted:
            if ch == '\\' and i + 1 < len(line):
                cur.append(line[i + 1]); i += 2; continue
            if ch == '"':
                quoted = False
            else:
                cur.append(ch)
            i += 1; continue
        if ch == '"':
            quoted = word = True
        elif ch in ' \t\r\n':
            if word:
                out.append(''.join(cur)); cur, word = [], False
        else:
            cur.append(ch); word = True
        i += 1
    if word:
        out.append(''.join(cur))
    return out
def run(args, via):
    """One command: (exit status, stdout, stderr, rule)."""
    verb = args[0] if args else ''
    opts, flags, i = {}, set(), 1
    while i < len(args):
        a = args[i]
        if a in ('-a', '-s', '-X', '-k', '-d') and i + 1 < len(args) and verb != 'list-keychains':
            opts[a] = args[i + 1]; i += 2
        else:
            flags.add(a); i += 1
    kind = {'find-generic-password': 'find' if '-w' in flags else 'exists', 'add-generic-password': 'add',
            'delete-generic-password': 'delete', 'list-keychains': 'list'}.get(verb, verb)
    svc, acct = opts.get('-s', ''), opts.get('-a', '')
    fd = locked('log')
    try:
        with open(os.path.join(STATE, 'calls.jsonl'), 'a') as f:
            f.write(json.dumps({'verb': kind, 'service': svc, 'account': acct, 'pid': os.getpid(),
                                'flags': sorted(flags), 'via': via, 'argv_value': '-X' in sys.argv[1:]}) + '\n')
    finally:
        os.close(fd)
    rule = None
    fd = locked('rules')
    try:
        rules = load('rules.json', [])
        for r in rules:
            if r.get('verb') not in (None, kind):
                continue
            if r.get('service') not in (None, svc) or r.get('account') not in (None, acct):
                continue
            if r.get('times', -1) == 0:
                continue
            if r.get('times', -1) > 0:
                r['times'] -= 1
            rule = dict(r)
            break
        save('rules.json', rules)
    finally:
        os.close(fd)
    if rule and rule.get('delay'):
        time.sleep(rule['delay'])
    if rule and 'exit' in rule:
        return rule['exit'], rule.get('stdout', ''), rule.get('stderr', ''), rule
    key = svc + '\u0000' + acct
    fd = locked('store')
    try:
        store = load('store.json', {})
        if kind == 'list':
            return 0, load('keychains.json', ['    "/Users/tester/Library/Keychains/login.keychain-db"\n'])[0], '', rule
        if kind in ('find', 'exists'):
            item = store.get(key)
            if item is None:
                return 44, '', ('security: SecKeychainSearchCopyNext: The specified item could not be found in the '
                                'keychain.\n'), rule
            if kind == 'exists':
                return 0, ('keychain: "/Users/tester/Library/Keychains/login.keychain-db"\n'
                           'attributes:\n    "mdat"<timedate>=0x00 "%%s"\n' %% item['mdat']), '', rule
            v = item['value']
            printable = all(32 <= ord(ch) < 127 for ch in v)
            return 0, (v if printable else v.encode('utf-8').hex()) + '\n', '', rule
        if kind == 'add':
            if '-U' not in flags and key in store:
                return 45, '', '', rule
            raw = opts.get('-X', '')
            raw = raw[:len(raw) - len(raw) %% 2]                # a line cut short may end in half a byte
            try:
                value = bytes.fromhex(raw).decode('utf-8', 'replace')
            except ValueError:
                return 1, '', 'security: the -X value is not hex\n', rule
            store[key] = {'value': value,
                          'mdat': time.strftime('%%Y%%m%%d%%H%%M%%SZ', time.gmtime()) + str(time.time())}
            save('store.json', store)
            with open(os.path.join(STATE, 'landed.jsonl'), 'a') as f:
                f.write(json.dumps({'service': svc, 'account': acct, 'pid': os.getpid(), 'at': time.time()}) + '\n')
            return 0, '', '', rule
        if kind == 'delete':
            if key not in store:
                return 44, '', '', rule
            del store[key]
            save('store.json', store)
            return 0, '', '', rule
        return 1, '', '', rule
    finally:
        os.close(fd)
count(1)
try:
    if sys.argv[1:] == ['-i']:
        status = 0
        exit_zero = False
        for line in sys.stdin:
            line = line[:LINE_MAX - 1]
            words = split(line)
            if not words:
                continue
            status, out, err, rule = run(words, 'stdin')
            if rule and rule.get('after'):
                time.sleep(rule['after'])
            exit_zero = exit_zero or bool(rule and rule.get('exit_zero'))
            sys.stdout.write(out)
            sys.stderr.write(err)
            if status:
                sys.stderr.write('%%s: returned %%d\n' %% (words[0], status))
        sys.exit(0 if exit_zero else status)
    status, out, err, rule = run(sys.argv[1:], 'argv')
    if rule and rule.get('after'):
        time.sleep(rule['after'])
    sys.stdout.write(out)
    sys.stderr.write(err)
    sys.exit(status)
finally:
    count(-1)
'''


class FakeSecurity:
    """A stand-in for /usr/bin/security. tool: its path (hand it to keychain.Keychain)."""

    def __init__(self, root):
        self.state = os.path.join(root, 'fake-keychain')
        os.makedirs(self.state, exist_ok=True)
        self.tool = os.path.join(root, 'fake-security')
        with open(self.tool, 'w') as f:
            f.write(_SCRIPT % {'python': sys.executable, 'state': self.state})
        os.chmod(self.tool, 0o700)

    def _path(self, name):
        return os.path.join(self.state, name)

    def _load(self, name, default):
        try:
            with open(self._path(name)) as f:
                return json.load(f)
        except (OSError, ValueError):
            return default

    def _save(self, name, obj):
        tmp = self._path(name + '.tmp')
        with open(tmp, 'w') as f:
            json.dump(obj, f)
        os.replace(tmp, self._path(name))

    def items(self):
        return dict((tuple(k.split('\u0000', 1)), v['value']) for k, v in self._load('store.json', {}).items())

    def get(self, service, account):
        return self.items().get((service, account))

    def put(self, service, account, value):
        store = self._load('store.json', {})
        store[service + '\u0000' + account] = {'value': value, 'mdat': str(time.time())}
        self._save('store.json', store)

    def drop(self, service, account):
        store = self._load('store.json', {})
        store.pop(service + '\u0000' + account, None)
        self._save('store.json', store)

    def rule(self, **r):
        """Add a scripted rule: verb ('find', 'exists', 'add', 'delete', 'list'), service, account, exit, stdout,
        stderr, delay (seconds before the command acts), after (seconds the call still runs once it has acted: a write
        that landed and then hung), times (-1 = always), exit_zero (under `security -i`: the call exits 0 although the
        command failed, saying only "returned N" on its error stream)."""
        rules = self._load('rules.json', [])
        rules.append(r)
        self._save('rules.json', rules)

    def clear_rules(self):
        self._save('rules.json', [])

    def no_login_keychain(self):
        self._save('keychains.json', ['    "/Library/Keychains/System.keychain"\n'])

    def calls(self, verb=None):
        out = []
        try:
            with open(self._path('calls.jsonl')) as f:
                for line in f:
                    c = json.loads(line)
                    if verb is None or c['verb'] == verb:
                        out.append(c)
        except OSError:
            pass
        return out

    def max_concurrent(self):
        return self._load('conc.json', {'max': 0})['max']

    def landed(self, service=None):
        """Each write that changed the store: {'service', 'account', 'pid', 'at' (wall seconds)}, in order."""
        out = []
        try:
            with open(self._path('landed.jsonl')) as f:
                for line in f:
                    c = json.loads(line)
                    if service is None or c['service'] == service:
                        out.append(c)
        except OSError:
            pass
        return out


class FakeKeychain:
    """In-process stand-in with keychain.Keychain's interface and answers, for the engine tests (a process start at
    background priority on a loaded Mac takes seconds; the subprocess layer itself is tested with FakeSecurity).

    rule(verb, service=None, account=None, why='locked', times=1, land=False): the next matching call fails with why
    ('absent' answers "not there"). land=True on a write: it lands but is reported as failed (a helper that timed
    out and finished anyway). why='silent' on a write: reported as done, but nothing is kept (a failure the tool did
    not report). on_call(verb, service, account): called before each call (to change things mid-flight).
    Every call goes through one gate; `max` counts how many were ever inside it at once."""

    def __init__(self):
        self.store = {}
        self.rules = []
        self.calls = []
        self.lock = threading.Lock()
        self.gate = threading.Lock()
        self.inside = 0
        self.max = 0
        self.on_call = None
        self.pending = set()
        self.created = []           # (service, account) of each write made with create_only

    def rule(self, verb, service=None, account=None, why='locked', times=1, land=False):
        with self.lock:
            self.rules.append({'verb': verb, 'service': service, 'account': account, 'why': why, 'times': times,
                               'land': land})

    def _match(self, verb, service, account):
        with self.lock:
            self.calls.append((verb, service, account))
            for r in self.rules:
                if r['verb'] == verb and r['service'] in (None, service) and r['account'] in (None, account) and \
                        r['times'] != 0:
                    if r['times'] > 0:
                        r['times'] -= 1
                    return r
        return None

    def _enter(self):
        self.gate.acquire()
        self.inside += 1
        self.max = max(self.max, self.inside)

    def _leave(self):
        self.inside -= 1
        self.gate.release()

    def read(self, service, account, fresh=False, interactive=False):
        from switchboard.accounts.api import Read, PRESENT, ABSENT, UNREADABLE
        if self.on_call:
            self.on_call('read', service, account)
        self._enter()
        try:
            r = self._match('read', service, account)
            if r is not None:
                return Read(ABSENT) if r['why'] == 'absent' else Read(UNREADABLE, why=r['why'])
            if (service, account) in self.pending:
                return Read(UNREADABLE, why='timeout')
            v = self.get(service, account)
            return Read(ABSENT) if v is None else Read(PRESENT, value=v, provenance='keychain')
        finally:
            self._leave()

    def write(self, service, account, value, interactive=False, create_only=False):
        if self.on_call:
            self.on_call('write', service, account)
        self._enter()
        try:
            if (service, account) in self.pending:
                return keychain.Done(False, 'busy')
            r = self._match('write', service, account)
            if r is not None:
                if r['why'] == 'silent':
                    return keychain.Done(True)
                if r['land']:
                    self.put(service, account, value)
                return keychain.Done(False, r['why'])
            if create_only:
                self.created.append((service, account))
            if create_only and self.get(service, account) is not None:
                return keychain.Done(False, 'exists')       # as security without -U: exit 45, nothing changed
            self.put(service, account, value)
            return keychain.Done(True)
        finally:
            self._leave()

    def delete(self, service, account):
        if self.on_call:
            self.on_call('delete', service, account)
        self._enter()
        try:
            r = self._match('delete', service, account)
            if r is not None:
                return keychain.Done(False, r['why'])
            self.drop(service, account)
            return keychain.Done(True)
        finally:
            self._leave()

    def exists(self, service, account):
        from switchboard.accounts.api import Read, PRESENT, ABSENT
        return Read(PRESENT, value='x') if self.get(service, account) is not None else Read(ABSENT)

    def state(self):
        return {'counts': {}, 'needs_approval_or_slow': sorted(s for s, _ in self.pending), 'resting': [],
                'resting_all': None, 'last_good_at': None, 'searchable': True}

    def count(self, verb, service=None):
        return sum(1 for c in self.calls if c[0] == verb and service in (None, c[1]))

    # FakeSecurity-compatible helpers
    def items(self):
        return dict(self.store)

    def get(self, service, account):
        return self.store.get((service, account))

    def put(self, service, account, value):
        self.store[(service, account)] = value

    def drop(self, service, account):
        self.store.pop((service, account), None)


class FileKeychain(FakeKeychain):
    """FakeKeychain whose items live in a JSON file, so a child process (one a test kills with SIGKILL) and the test
    see the same Keychain."""

    def __init__(self, path):
        super().__init__()
        self.path = path

    def _locked(self, change):
        import fcntl
        fd = os.open(self.path + '.lock', os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                with open(self.path) as f:
                    data = json.load(f)
            except (OSError, ValueError):
                data = {}
            out = change(data)
            tmp = self.path + '.tmp%d' % os.getpid()
            with open(tmp, 'w') as f:
                json.dump(data, f)
            os.replace(tmp, self.path)
            return out
        finally:
            os.close(fd)

    def get(self, service, account):
        return self._locked(lambda d: d.get(service + '\u0000' + account))

    def put(self, service, account, value):
        self._locked(lambda d: d.__setitem__(service + '\u0000' + account, value))

    def drop(self, service, account):
        self._locked(lambda d: d.pop(service + '\u0000' + account, None))

    def items(self):
        return self._locked(lambda d: dict((tuple(k.split('\u0000', 1)), v) for k, v in d.items()))


class FakeAnthropic:
    """Anthropic's token and profile endpoints, as far as refresh tokens go."""

    LIFETIME = 8 * 3600
    # How long one round trip to the token address takes here. More than a stamp's millisecond (with room for the
    # float behind it), so a login and the one renewed from it never share a stamp, even on a clock that ticks once a
    # second; far less than any real round trip and than every span the product counts, so no test that counts
    # seconds sees it (a thousand renewals in one test add five seconds).
    TRIP_S = 0.005

    def __init__(self, wall=time.time):
        self.clock = wall           # the test's own clock, kept as it is
        self.passed = 0.0           # the time the round trips to the token address have taken so far
        self.lock = threading.Lock()
        self.accounts = {}          # uuid -> {'email', 'org', 'n', 'refresh' (valid), 'rx_ms'}
        self.access = {}            # access token -> (uuid, expires_s)
        self.requests = []          # (method, url, refresh token fingerprint or None)
        self.dns_ok = True
        self.script = []            # [(kind 'token'|'profile', action)]: action = (status, obj) | NetError | 'cut'
        self.post_delay = 0.0
        self.used = []              # refresh tokens spent (for "was it spent twice")
        self.on_post = None         # called as each token POST arrives (tests move clocks there)
        self.signins = 0            # sign-ins made so far, so each one's end date differs from every other's

    def wall(self):
        """The time every party of a test reads: this stand-in, the engine of each made-up Mac that uses it (Rig's
        ctx.wall), and Claude Code's own renewal (refresh_for). It is the test's clock plus the round trips so far:
        on a real Mac every renewal is a round trip, so time passes between a login and the stamp of the one renewed
        from it, whoever computes that stamp."""
        return self.clock() + self.passed

    def add(self, uuid, email, org='org-x', rx_days=30):
        """A fresh sign-in for an account (its own lineage; earlier sign-ins stay valid, as on several Macs): returns
        its login JSON text as Claude Code stores it."""
        with self.lock:
            a = self.accounts.setdefault(uuid, {'email': email, 'org': org, 'lineages': {}})
            a['email'], a['org'] = email, org
            n = len(a['lineages']) + 1
            # Every sign-in's end date is its own, as Anthropic's are: two made in the same millisecond (a fast run)
            # once got the same date, and the engine rightly took the second account's login for the first's.
            self.signins += 1
            rx = own = int((self.wall() + rx_days * 86400) * 1000) + self.signins
            # Different sign-ins of one account are made days apart, as real ones are: stamps within
            # facts.SAME_SIGN_IN_MS are one sign-in to the engine (a renewal can lower its stamp that much), so a
            # sign-in made again in the same moment of a test ends a day after the earlier one it would sit beside.
            # That day never puts an earlier sign-in after a later one: a real sign-in's end date is set fresh when it
            # is made, a fixed span after that moment (facts 'refresh.expiry_rule'), so a sign-in whose own date is
            # later ends later. Without this, a sign-in moved a day on ended after one made an hour later in the
            # test's time, and the engine rightly took the newer sign-in for the older one (test_fresh_proof).
            while any(facts.same_sign_in(rx, line['rx_ms']) or
                      (line.get('moved_to') == line['rx_ms'] and line['moved_from'] < own and rx <= line['rx_ms'])
                      for line in a['lineages'].values()):
                rx += 86400 * 1000
            a['lineages'][n] = {'gen': 0, 'rx_ms': rx, 'org': org}
            if rx != own:                           # moved on by the loop above: what its own moment gave, and where
                a['lineages'][n].update(moved_from=own, moved_to=rx)
            a['latest'] = n
            return self._issue(uuid, n)

    def _issue(self, uuid, n):
        line = self.accounts[uuid]['lineages'][n]
        line['gen'] += 1
        tag = '%s-s%d-g%d' % (uuid[:8], n, line['gen'])
        line['refresh'] = 'sk-ant-ort01-' + tag
        access = 'sk-ant-oat01-' + tag
        exp = self.wall() + self.LIFETIME
        self.access[access] = (uuid, exp, line.get('org'))
        return login_text(access, line['refresh'], int(exp * 1000), line['rx_ms'])

    def valid_refresh(self, uuid):
        """The current refresh token of the account's latest sign-in."""
        a = self.accounts[uuid]
        return a['lineages'][a['latest']]['refresh']

    def valid_tokens(self, uuid):
        return [line['refresh'] for line in self.accounts[uuid]['lineages'].values()]

    def revoke(self, uuid):
        """Every sign-in of the account stops working (as after a logout)."""
        for line in self.accounts[uuid]['lineages'].values():
            line['refresh'] = None

    def resolve(self, host, deadline_s):
        return self.dns_ok

    def _next(self, kind):
        with self.lock:
            for i, (k, action) in enumerate(self.script):
                if k == kind:
                    del self.script[i]
                    return action
        return None

    def request(self, method, url, headers=None, body=None, timeout_s=10.0):
        if url == facts.TOKEN_URL:
            return self._token(body, timeout_s)
        if url == facts.PROFILE_URL:
            return self._profile(headers or {})
        return 404, {}, b'{}'

    def _token(self, body, timeout_s):
        req = json.loads(body.decode('utf-8'))
        rt = req.get('refresh_token')
        from switchboard.accounts.live import fingerprint
        with self.lock:
            self.requests.append(('POST', facts.TOKEN_URL, fingerprint(rt)))
        if self.on_post is not None:
            self.on_post(rt)
        if self.post_delay:
            time.sleep(self.post_delay)
        action = self._next('token')
        if isinstance(action, net.NetError) and not action.sent:
            raise action
        with self.lock:
            self.passed += self.TRIP_S          # it went out: its round trip takes time, answered or not
        if isinstance(action, tuple):
            return action[0], {}, json.dumps(action[1]).encode('utf-8')
        if req.get('client_id') != facts.CLIENT_ID or req.get('grant_type') != 'refresh_token':
            return 400, {}, b'{"error": "invalid_client"}'
        with self.lock:
            owner = line_n = None
            for uuid, a in self.accounts.items():
                for n, line in a['lineages'].items():
                    if rt and line.get('refresh') == rt:
                        owner, line_n = uuid, n
            self.used.append(rt)
            if owner is None:
                return 400, {}, json.dumps({'error': 'invalid_grant',
                                            'error_description': 'Refresh token not found or invalid'}).encode()
            a = self.accounts[owner]
            text = self._issue(owner, line_n)
            org = a['lineages'][line_n].get('org')
        o = json.loads(text)['claudeAiOauth']
        answer = {'access_token': o['accessToken'], 'refresh_token': o['refreshToken'], 'expires_in': self.LIFETIME,
                  'scope': 'user:inference user:profile', 'account': {'uuid': owner, 'email_address': a['email']},
                  'organization': {'uuid': org}}
        if action == 'cut' or (isinstance(action, net.NetError) and action.sent):
            raise action if isinstance(action, net.NetError) else net.NetError('timeout', True, 'cut off')
        if action == 'garbled':
            return 200, {}, b'<html>proxy</html>'
        return 200, {}, json.dumps(answer).encode('utf-8')

    def _profile(self, headers):
        action = self._next('profile')
        if isinstance(action, net.NetError):
            raise action
        if isinstance(action, tuple):
            return action[0], {}, json.dumps(action[1]).encode('utf-8')
        tok = (headers.get('Authorization') or '')[len('Bearer '):]
        with self.lock:
            hit = self.access.get(tok)
            if hit is None or hit[1] <= self.wall():
                return 401, {}, b'{"error": {"type": "authentication_error"}}'
            uuid, org = hit[0], hit[2]
            a = self.accounts[uuid]
        return 200, {}, json.dumps({'account': {'uuid': uuid, 'email': a['email']},
                                    'organization': {'uuid': org}}).encode('utf-8')

    def token_posts(self):
        return [r for r in self.requests if r[1] == facts.TOKEN_URL]

    def refresh_for(self, text):
        """Claude Code renewing the login it holds (the server side of its own refresh): the next login text."""
        o = json.loads(text)['claudeAiOauth']
        status, _, body = self._token(json.dumps({'grant_type': 'refresh_token', 'refresh_token': o['refreshToken'],
                                                  'client_id': facts.CLIENT_ID}).encode(), 10)
        assert status == 200, body
        ans = json.loads(body)
        blob = json.loads(text)
        blob['claudeAiOauth'].update({'accessToken': ans['access_token'], 'refreshToken': ans['refresh_token'],
                                      'expiresAt': int((self.wall() + ans['expires_in']) * 1000)})
        return json.dumps(blob, separators=(',', ':'))


def login_text(access, refresh, exp_ms, rx_ms=None, siblings=None, plan='max'):
    o = {'accessToken': access, 'refreshToken': refresh, 'expiresAt': exp_ms,
         'scopes': ['user:inference', 'user:profile'], 'subscriptionType': plan}
    if rx_ms is not None:
        o['refreshTokenExpiresAt'] = rx_ms
    blob = {'claudeAiOauth': o}
    blob.update(siblings or {})
    return json.dumps(blob, separators=(',', ':'))


def identity_block(uuid, email, org='org-x', name='Personal'):
    return {'accountUuid': uuid, 'emailAddress': email, 'organizationUuid': org, 'organizationName': name,
            'displayName': email.split('@')[0]}


class Procs:
    """A made-up process table for claims: pid -> start stamp."""

    def __init__(self):
        self.table = {}

    def alive(self, pid):
        return pid in self.table

    def start(self, pid):
        return self.table.get(pid)


class Rig:
    """One made-up Mac: a home with Claude Code's files, a data folder, a fake Keychain, a fake Anthropic.

    mirror: the standing copy of the sign-in in Claude Code's plain login file (putback.mirror_on, on in the program
    unless the setting says false). Off unless a test asks for it: the tests written before it pin the behaviour that
    stays with the setting off; the standing copy has tests of its own (test_mirror_*). load: the load per core the
    engine sees (keychain.LOAD), so no test depends on how busy the Mac running it is."""

    def __init__(self, extra_env=None, wall=None, mono=None, sleep=None, user='tester', real_keychain=False,
                 mirror=False, load=0.0):
        self.root = tempfile.mkdtemp(prefix='sb-engine-')
        self.home = os.path.join(self.root, 'home')
        self.data = os.path.join(self.root, 'data')
        os.makedirs(os.path.join(self.home, '.claude'), mode=0o700)
        self.server = FakeAnthropic(wall=wall or time.time)
        self.procs = Procs()
        self.procs.table[os.getpid()] = 'self-start'
        self.events = []
        self.logs = []
        self.env = {'USER': user, 'HOME': self.home, 'PATH': '/usr/bin:/bin'}
        self.env.update(extra_env or {})
        if real_keychain:
            # the real wrapper and a real child process per call; generous deadlines (a loaded Mac starts slowly)
            self.sec = FakeSecurity(self.root)
            self.kc = keychain.Keychain(tool=self.sec.tool, read_deadline=60.0, write_deadline=60.0, backoff_s=30.0,
                                        gate_path=os.path.join(self.data, 'keychain.gate'), log=self.logs.append)
        else:
            self.sec = self.kc = FakeKeychain()
        self.free = 10 ** 12
        self.ctx = context.Context(env=self.env, home=self.home, data_dir=self.data, keychain=self.kc,
                                   http=self.server, wall=self._wall, mono=mono or time.monotonic,
                                   sleep=sleep or time.sleep, events=self._event, log=self.logs.append,
                                   disk_free=lambda p: self.free, proc_start=self.procs.start,
                                   pid_alive=self.procs.alive, claude=None)
        from switchboard.accounts import capture, identity, putback, roster, tokens
        from unittest import mock
        self._mirror_off = None
        if not mirror:
            self._mirror_off = mock.patch.object(putback, 'mirror_on', lambda ctx: False)
            self._mirror_off.start()
        self._load = mock.patch.object(keychain, 'LOAD', lambda: load)
        self._load.start()
        capture.reset()
        roster.forget_live()
        tokens.WAKE.reset()
        tokens._UNPERSISTED.clear()
        identity._PROFILE_CACHE.clear()

    def _wall(self):
        """The engine's clock: the stand-in server's (FakeAnthropic.wall), read when asked, so a made-up Mac given
        another Mac's server (tests/test_backup_export.py) sees the same passing of time."""
        return self.server.wall()

    def _event(self, text, kind, facts_):
        self.events.append((text, kind, dict(facts_)))

    def close(self):
        if self._mirror_off is not None:
            self._mirror_off.stop()
            self._mirror_off = None
        if self._load is not None:
            self._load.stop()
            self._load = None
        shutil.rmtree(self.root, ignore_errors=True)

    # ---------- Claude Code's side ----------
    @property
    def service(self):
        return facts.keychain_service(self.env)

    @property
    def account(self):
        return facts.keychain_account(self.env)

    def live_text(self):
        return self.sec.get(self.service, self.account)

    def set_live(self, text):
        self.sec.put(self.service, self.account, text)

    def config_path(self):
        return facts.global_config(self.env, self.home)

    def set_config(self, block, **other):
        obj = self.config()
        obj.update(other)
        if block is None:
            obj.pop('oauthAccount', None)
        else:
            obj['oauthAccount'] = block
        with open(self.config_path(), 'w') as f:
            json.dump(obj, f, indent=2)

    def config(self):
        try:
            with open(self.config_path()) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def sign_in(self, uuid, email, org='org-x', siblings=None, name='Personal'):
        """What /login does: a fresh login in the live item and the identity block in ~/.claude.json."""
        text = self.server.add(uuid, email, org)
        if siblings:
            blob = json.loads(text)
            blob.update(siblings)
            text = json.dumps(blob, separators=(',', ':'))
        self.set_live(text)
        self.set_config(identity_block(uuid, email, org, name))
        return text

    def claude_renews(self):
        """Claude Code renewing the live login by itself (it owns the active lineage)."""
        text = self.server.refresh_for(self.live_text())
        self.set_live(text)
        return text

    # ---------- our side ----------
    def vault(self, aid, service='Switchboard login'):
        return self.sec.get(service, aid)

    def keep(self, uuid, email, org='org-x', siblings=None):
        """/login as an account, then the page's Add: the account is kept, and it is the live login."""
        from switchboard.accounts import roster
        text = self.sign_in(uuid, email, org, siblings=siblings)
        roster.add_current(self.ctx)
        return text

    def two_accounts(self):
        """A and B kept; Claude Code is on B, so A is an idle saved login."""
        a = self.keep('uuid-a', 'a@example.com', 'org-a')
        b = self.keep('uuid-b', 'b@example.com', 'org-b')
        return a, b

    def set_saved_expiry(self, aid, exp_s):
        """Make an account's saved access token expire at exp_s (the bytes change; the refresh token does not)."""
        from switchboard.accounts import live, vault
        text = self.vault(aid)
        blob = json.loads(text)
        blob['claudeAiOauth']['expiresAt'] = int(exp_s * 1000)
        new = json.dumps(blob, separators=(',', ':'))
        self.sec.put('Switchboard login', aid, new)
        with vault.changing(self.ctx) as doc:
            vault.note_login(doc['accounts'][aid], live.parse(new), None, doc['accounts'][aid].get('saved_at'))
        return new

    def doc(self):
        from switchboard.accounts import vault
        return vault.load(self.ctx)

    def holds_valid(self, uuid, latest=True):
        """Somewhere on this Mac (live item, a saved login, an earlier one, a recovery copy or file) holds a refresh
        token the server would still accept for this account (its latest sign-in's, unless latest=False)."""
        tokens_ = [self.server.valid_refresh(uuid)] if latest else self.server.valid_tokens(uuid)
        tokens_ = [t for t in tokens_ if t]
        for value in self.sec.items().values():
            if any(t in value for t in tokens_):
                return True
        folder = os.path.join(self.data, 'recovery')
        for name in (os.listdir(folder) if os.path.isdir(folder) else []):
            with open(os.path.join(folder, name)) as f:
                text = f.read()
                if any(t in text for t in tokens_):
                    return True
        return False
