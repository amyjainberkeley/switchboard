"""The stand-in `fake-codex`, and the small JSON Schema checker it carries.

`fake-codex` is a Python program a test writes (FakeCodex below). It copies one real Codex version, named in its spec:
- `--version` / `-V` prints `codex-cli <version>` as the real program does (0.155.1 and 0.169.1 print exactly that).
- every flag is checked against that version's flags file (tests/fixtures/codex/flags-<version>.json, made only from
  that version's own --help output). A flag the help lacks, or a value-taking flag with no value, gets the real
  command line parser's usage error and exit 2 [not checked: the real program's code; 2 is what its parser (clap)
  documents for a usage error, and nobody ran the real one with a bad flag here].
- `app-server` speaks one JSON object per line on stdin and stdout, as the generated schema of its version says
  (tests/fixtures/codex/schema/<version>/, copied from `codex app-server generate-json-schema`): `initialize`, then it
  needs the `initialized` notification before anything else and answers `Not initialized` until then; a second
  `initialize` is `Already initialized`; `account/read` and `account/rateLimits/read` answer from the spec; any
  other method is error -32601. The spec can make it send a server request first, send a notification first, stall
  on a method, close at a method, answer a method with an error (-32001), or write a lot to standard error first.
  Every line it gets is checked against the schema and every line it sends is checked too; a mismatch is recorded as
  a "violation" (tests assert there is none).
- `exec` reads the prompt from standard input when the last word is `-` (or no prompt is given), writes the spec's
  answer to the `-o` file, and can sleep, start a child that outlives nothing, write to standard error and exit
  non-zero.
- the bare interactive form, `resume`, `fork` and `exec resume` record their argv and exit 0.
- `login`, `logout`, `cloud`, `remote-control`, `app-server daemon`, `app-server proxy`, `--listen`,
  `--startup-sign-out` and any subcommand this stand-in does not model exit 3 and are recorded as "forbidden": a test
  asserts none ever is.
It records every argv, the names (never the values) of its environment, its folder, and what it was sent, one JSON
object per line in its record file. Nothing here is a library; the tests that use it hold the tests.
"""
import json
import os
import plistlib

from fakes_providers import stand_in

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, 'fixtures', 'codex')
VERSIONS = ('0.155.1', '0.169.1')          # the versions whose help and schema are in fixtures/

# ---------- the schema checker (also pasted into the stand-in, which runs isolated with -I) ----------

CHECKER_SOURCE = r'''
_KNOWN = frozenset(('$schema', '$ref', 'definitions', 'title', 'description', 'default', 'examples', 'type', 'enum',
                    'const', 'format', 'properties', 'required', 'additionalProperties', 'items', 'anyOf', 'oneOf',
                    'allOf'))
_INT_RANGE = {'int32': (-(1 << 31), (1 << 31) - 1), 'int64': (-(1 << 63), (1 << 63) - 1),
              'uint32': (0, (1 << 32) - 1), 'uint64': (0, (1 << 64) - 1), 'uint': (0, (1 << 64) - 1)}


def _is(kind, value):
    if kind == 'null':
        return value is None
    if kind == 'boolean':
        return isinstance(value, bool)
    if kind == 'integer':
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == 'number':
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == 'string':
        return isinstance(value, str)
    if kind == 'object':
        return isinstance(value, dict)
    if kind == 'array':
        return isinstance(value, list)
    raise ValueError('type %r not understood by this checker' % (kind,))


def _same(a, b):
    return type(a) is type(b) and a == b


def schema_errors(schema, value, root=None, path='$'):
    """Every way value fails schema (JSON Schema draft-07, the keywords Codex's generated files use), as short lines
    naming where. [] when it fits. A keyword this checker does not know is itself an error, so it is never kinder
    than the schema."""
    root = schema if root is None else root
    if schema is True:
        return []
    if schema is False:
        return ['%s: nothing is allowed here' % path]
    if not isinstance(schema, dict):
        return ['%s: the schema is not an object' % path]
    unknown = sorted(set(schema) - _KNOWN)
    if unknown:
        return ['%s: keyword %s not understood by this checker' % (path, ', '.join(unknown))]
    errs = []
    if '$ref' in schema:
        ref = schema['$ref']
        if not ref.startswith('#/'):
            return ['%s: $ref %s is not local' % (path, ref)]
        target = root
        for part in ref[2:].split('/'):
            if not isinstance(target, dict) or part not in target:
                return ['%s: $ref %s not found' % (path, ref)]
            target = target[part]
        errs += schema_errors(target, value, root, path)
    if 'type' in schema:
        kinds = schema['type'] if isinstance(schema['type'], list) else [schema['type']]
        if not any(_is(k, value) for k in kinds):
            return errs + ['%s: %s is not %s' % (path, type(value).__name__, ' or '.join(kinds))]
    if 'enum' in schema and not any(_same(value, e) for e in schema['enum']):
        errs.append('%s: %r is not one of %s' % (path, value, schema['enum']))
    if 'const' in schema and not _same(value, schema['const']):
        errs.append('%s: %r is not %r' % (path, value, schema['const']))
    if 'format' in schema and isinstance(value, int) and not isinstance(value, bool):
        lo, hi = _INT_RANGE.get(schema['format'], (None, None))
        if lo is not None and not lo <= value <= hi:
            errs.append('%s: %r is out of range for %s' % (path, value, schema['format']))
    if isinstance(value, dict):
        for name in schema.get('required', ()):
            if name not in value:
                errs.append('%s: %s is required' % (path, name))
        props = schema.get('properties', {})
        for name, sub in props.items():
            if name in value:
                errs += schema_errors(sub, value[name], root, '%s.%s' % (path, name))
        extra = schema.get('additionalProperties', True)
        for name in value:
            if name in props:
                continue
            if extra is False:
                errs.append('%s: %s is not allowed here' % (path, name))
            elif isinstance(extra, dict):
                errs += schema_errors(extra, value[name], root, '%s.%s' % (path, name))
    if isinstance(value, list) and 'items' in schema:
        for i, item in enumerate(value):
            errs += schema_errors(schema['items'], item, root, '%s[%d]' % (path, i))
    for sub in schema.get('allOf', ()):
        errs += schema_errors(sub, value, root, path)
    if 'anyOf' in schema:
        tries = [schema_errors(sub, value, root, path) for sub in schema['anyOf']]
        if all(tries):
            errs.append('%s: fits none of anyOf (%s)' % (path, '; '.join(t[0] for t in tries)))
    if 'oneOf' in schema:
        tries = [schema_errors(sub, value, root, path) for sub in schema['oneOf']]
        fits = sum(1 for t in tries if not t)
        if fits != 1:
            errs.append('%s: fits %d of oneOf, not exactly one (%s)' % (path, fits,
                                                                        '; '.join(t[0] for t in tries if t)))
    return errs
'''

exec(compile(CHECKER_SOURCE, __file__ + ':CHECKER_SOURCE', 'exec'))   # defines schema_errors here too
# Bound again by name, so a tool that only reads this file (it runs nothing) sees what the tests import from it.
schema_errors = globals()['schema_errors']


def schema(version, name):
    """One copied schema file of a version, by its base name ('GetAccountResponse')."""
    with open(os.path.join(FIXTURES, 'schema', version, name + '.json')) as f:
        return json.load(f)


def flags(version):
    with open(os.path.join(FIXTURES, 'flags-%s.json' % version)) as f:
        return json.load(f)


# What each answer and each request's params must fit, by method, in every copied version.
PARAMS = {'initialize': 'InitializeParams', 'account/read': 'GetAccountParams',
          'account/rateLimits/read': 'NullableGetAccountRateLimitsParams'}
RESULTS = {'initialize': 'InitializeResponse', 'account/read': 'GetAccountResponse',
           'account/rateLimits/read': 'GetAccountRateLimitsResponse'}

# ---------- the stand-in program ----------

FAKE_BODY = r'''
import json, os, subprocess, sys, time

ME = os.path.realpath(sys.argv[0])
with open(ME + '.spec.json') as _f:
    SPEC = json.load(_f)
FORBIDDEN_WORDS = ('login', 'logout', 'cloud', 'remote-control')
FORBIDDEN_FLAGS = ('--listen', '--startup-sign-out')


def record(**entry):
    with open(SPEC['record'], 'a') as f:
        f.write(json.dumps(entry, sort_keys=True) + '\n')


def schema(name):
    with open(os.path.join(SPEC['schema_dir'], name + '.json')) as f:
        return json.load(f)


def check(name, value, what):
    errs = schema_errors(schema(name), value)
    if errs:
        record(violation='%s: %s' % (what, '; '.join(errs[:5])))


def usage_error(text, usage):
    sys.stderr.write("error: %s\n\nUsage: %s\n\nFor more information, try '--help'.\n" % (text, usage))
    sys.exit(2)


record(argv=sys.argv[1:], env=sorted(os.environ), cwd=os.getcwd(), program=sys.argv[0])
if SPEC.get('stderr_bytes'):
    sys.stderr.write('x' * int(SPEC['stderr_bytes']))
    sys.stderr.flush()

with open(SPEC['flags']) as _f:
    COMMANDS = json.load(_f)['commands']

args = sys.argv[1:]
path, cmd, opts, pos, i = '', COMMANDS[''], {}, [], 0
while i < len(args):
    a = args[i]
    if a == '--':
        pos.extend(args[i + 1:])
        break
    if a.startswith('-') and a != '-':
        if a.startswith('--'):
            name, eq, val = a.partition('=')
            found = [o for o in cmd['options'] if o['long'] == name]
        else:
            name, eq, val = a[:2], len(a) > 2, a[2:]
            found = [o for o in cmd['options'] if o['short'] == name]
        if name in FORBIDDEN_FLAGS:
            record(forbidden=name)
            sys.exit(3)
        if not found:
            usage_error("unexpected argument '%s' found" % name, cmd['usage'])
        opt = found[0]
        key = opt['long'] or opt['short']
        if opt['value']:
            if not eq:
                i += 1
                if i >= len(args):
                    usage_error("a value is required for '%s <%s>' but none was supplied" % (key, opt['value']),
                                cmd['usage'])
                val = args[i]
            opts.setdefault(key, []).append(val)
        else:
            opts.setdefault(key, []).append(True)
        i += 1
        continue
    if not pos and a in cmd.get('subcommands', ()):
        if (path == '' and a in FORBIDDEN_WORDS) or (path == 'app-server' and a in ('daemon', 'proxy')):
            record(forbidden=(path + ' ' + a).strip())
            sys.exit(3)
        path = (path + ' ' + a).strip()
        if path not in COMMANDS:
            record(forbidden=path, why='not modelled by this stand-in')
            sys.exit(3)
        cmd = COMMANDS[path]
        i += 1
        continue
    pos.append(a)
    i += 1

if '--help' in opts:
    sys.stdout.write('Usage: %s\n' % cmd['usage'])
    sys.exit(0)
if '--version' in opts:
    sys.stdout.write('codex-cli %s\n' % SPEC['version'])
    sys.exit(0)
if len(pos) > len(cmd['positionals']):
    usage_error("unexpected argument '%s' found" % pos[len(cmd['positionals'])], cmd['usage'])
record(command=path, options=opts, positionals=pos)


def exec_mode():
    ex = SPEC.get('exec', {})
    prompt = pos[0] if pos else '-'
    if prompt == '-':
        prompt = sys.stdin.read()
    record(stdin=prompt)
    if ex.get('child'):
        kid = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(120)'])
        record(child=kid.pid)
    if ex.get('sleep'):
        time.sleep(float(ex['sleep']))
    if ex.get('stderr'):
        sys.stderr.write(ex['stderr'])
    out = opts.get('--output-last-message', [None])[-1]
    if out and ex.get('answer') is not None:
        with open(out, 'w') as f:
            f.write(ex['answer'])
    sys.exit(int(ex.get('exit', 0)))


def send(msg, result_schema=None):
    line = json.dumps(msg, separators=(',', ':'))
    record(sent=line)
    if 'error' in msg:
        check('JSONRPCError', msg, 'sent error')
    elif 'method' in msg and 'id' in msg:
        check('JSONRPCRequest', msg, 'sent request')
    elif 'method' in msg:
        check('JSONRPCNotification', msg, 'sent notification')
    else:
        check('JSONRPCResponse', msg, 'sent response')
        if result_schema:
            check(result_schema, msg['result'], 'sent result')
    sys.stdout.write(line + '\n')
    sys.stdout.flush()


def server():
    PARAMS = {'initialize': 'InitializeParams', 'account/read': 'GetAccountParams',
              'account/rateLimits/read': 'NullableGetAccountRateLimitsParams'}
    RESULTS = {'initialize': 'InitializeResponse', 'account/read': 'GetAccountResponse',
               'account/rateLimits/read': 'GetAccountRateLimitsResponse'}
    started, ready = False, False
    errors = SPEC.get('errors', {})
    while True:
        raw = sys.stdin.readline()
        if not raw:
            return
        line = raw.rstrip('\n')
        record(got=line)
        try:
            msg = json.loads(line)
        except ValueError:
            send({'id': None, 'error': {'code': -32700, 'message': 'Parse error'}})
            continue
        if not isinstance(msg, dict):
            record(violation='got a line that is not an object')
            continue
        if 'jsonrpc' in msg:
            record(note='got a jsonrpc field')
        method, mid = msg.get('method'), msg.get('id')
        if method is None:
            check('JSONRPCError' if 'error' in msg else 'JSONRPCResponse', msg, 'got answer')
            record(answer=msg)
            continue
        if mid is None:
            check('JSONRPCNotification', msg, 'got notification')
            if method == 'initialized':
                check('ClientNotification', msg, 'got initialized')
                ready = started
                if ready and SPEC.get('server_request_first'):
                    send({'id': 900, 'method': 'account/chatgptAuthTokens/refresh',
                          'params': {'reason': 'unauthorized', 'previousAccountId': None}})
                    check('ChatgptAuthTokensRefreshParams', {'reason': 'unauthorized', 'previousAccountId': None},
                          'sent server request params')
            continue
        check('JSONRPCRequest', msg, 'got request')
        if method in PARAMS and 'params' in msg:
            check(PARAMS[method], msg['params'], 'got %s params' % method)
        if method == SPEC.get('close_at'):
            sys.exit(0)
        if method == SPEC.get('stall_at'):
            continue
        if method in errors:
            code, text = errors[method]
            send({'id': mid, 'error': {'code': code, 'message': text}})
            continue
        if method == 'initialize':
            if started:
                send({'id': mid, 'error': {'code': -32600, 'message': 'Already initialized'}})
                continue
            started = True
            home = SPEC.get('codex_home') or os.environ.get('CODEX_HOME') or \
                os.path.join(os.environ.get('HOME', '/'), '.codex')
            send({'id': mid, 'result': {'codexHome': home, 'platformFamily': 'unix', 'platformOs': 'macos',
                                        'userAgent': 'fake-codex/%s' % SPEC['version']}}, RESULTS[method])
            continue
        if not ready:
            send({'id': mid, 'error': {'code': -32600, 'message': 'Not initialized'}})
            continue
        if method == 'account/read':
            send({'id': mid, 'result': SPEC['account']}, RESULTS[method])
        elif method == 'account/rateLimits/read':
            if SPEC.get('notify_first'):
                send({'method': 'account/rateLimits/updated',
                      'params': {'rateLimits': SPEC['rate_limits']['rateLimits']}})
            send({'id': mid, 'result': SPEC['rate_limits']}, RESULTS[method])
        else:
            send({'id': mid, 'error': {'code': -32601, 'message': 'Method not found'}})


if path == 'exec':
    exec_mode()
elif path == 'app-server':
    server()
    sys.exit(0)
else:
    sys.exit(0)            # the interactive form, resume, fork, exec resume: recorded above
'''

ACCOUNT = {'account': {'type': 'chatgpt', 'email': 'maya@northwind.example', 'planType': 'pro'},
           'requiresOpenaiAuth': True}
SNAPSHOT = {'limitId': 'codex', 'limitName': None, 'planType': 'pro', 'rateLimitReachedType': None,
            'primary': {'usedPercent': 42, 'windowDurationMins': 10080, 'resetsAt': 1800000000},
            'secondary': None, 'credits': {'hasCredits': False, 'unlimited': False, 'balance': None}}
RATE_LIMITS = {'rateLimits': SNAPSHOT, 'rateLimitsByLimitId': {'codex': SNAPSHOT}, 'ordinaryUsageAllowed': True}


class FakeCodex:
    """One stand-in copy of Codex at folder/name (name is never 'codex'), copying `version`. spec(**changes) changes
    what it does; records() is every entry it wrote, oldest first."""

    def __init__(self, folder, version='0.169.1', name='fake-codex', like=None, **spec):
        # A version whose help and schema were not copied (0.164.0-alpha.11.1, 0.167.0-alpha.4.1, a future one)
        # prints its own version and borrows the flags and schema of `like`, a copied one.
        like = like or (version if version in VERSIONS else '0.169.1')
        if like not in VERSIONS:
            raise ValueError('no help or schema copied for %s' % like)
        self.path = stand_in(folder, name, CHECKER_SOURCE + FAKE_BODY)
        self.record_path = self.path + '.record.jsonl'
        self._spec = {'version': version, 'flags': os.path.join(FIXTURES, 'flags-%s.json' % like),
                      'schema_dir': os.path.join(FIXTURES, 'schema', like), 'record': self.record_path,
                      'account': ACCOUNT, 'rate_limits': RATE_LIMITS}
        self.spec(**spec)

    @property
    def version(self):
        return self._spec['version']

    def spec(self, **changes):
        self._spec.update(changes)
        with open(self.path + '.spec.json', 'w') as f:
            json.dump(self._spec, f)
        return self

    def records(self):
        try:
            with open(self.record_path) as f:
                return [json.loads(line) for line in f if line.strip()]
        except FileNotFoundError:
            return []

    def runs(self):
        """The argv (without the program) of every run, in order."""
        return [r['argv'] for r in self.records() if 'argv' in r]

    def got(self):
        return [r['got'] for r in self.records() if 'got' in r]

    def sent(self):
        return [r['sent'] for r in self.records() if 'sent' in r]

    def violations(self):
        return [r['violation'] for r in self.records() if 'violation' in r]

    def forbidden(self):
        return [r['forbidden'] for r in self.records() if 'forbidden' in r]


def make_app(apps_dir, app_name, version, program='Contents/Resources/codex-cli/bin/fake-codex',
             bundle_id='com.openai.codex', **spec):
    """A made-up desktop app: apps_dir/app_name with a Contents/Info.plist naming bundle_id, and a FakeCodex at
    program inside it. Returns the FakeCodex."""
    app = os.path.join(apps_dir, app_name)
    os.makedirs(os.path.join(app, 'Contents'), exist_ok=True)
    with open(os.path.join(app, 'Contents', 'Info.plist'), 'wb') as f:
        plistlib.dump({'CFBundleIdentifier': bundle_id, 'CFBundleName': 'Made-up', 'CFBundleExecutable': 'Made-up'},
                      f)
    folder, name = os.path.split(os.path.join(app, program))
    return FakeCodex(folder, version, name=name, **spec)


# ---------- a made-up Mac for the Codex adapter's tests ----------

class Clock:
    """A wall clock a test moves by hand."""

    def __init__(self, t=1800000000.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class ChatFiles:
    """Stands in for codex_chats (lane C's reader), which the adapter reaches only through `chatfiles`: each answer is
    set by the test, each call is noted."""

    def __init__(self, newest=None, saved=None):
        self.newest, self.saved, self.chat_list, self.transcript, self.calls = newest, saved, (), None, []

    def newest_seen(self, reach):
        self.calls.append('newest_seen')
        return self.newest

    def saved_limits(self, reach, now):
        self.calls.append(('saved_limits', now))
        return self.saved

    def chats(self, reach, folder, now):
        self.calls.append(('chats', folder, now))
        return self.chat_list

    def read(self, reach, chat, limits):
        self.calls.append(('read', chat.id, limits))
        return self.transcript


class Choices:
    """Stands in for choices (lane A): only the one yes the ask needs."""

    def __init__(self, ok=False):
        self.ok = ok
        self.asked_for = []         # the provider each ask_ok was for

    def ask_ok(self, pid):
        self.asked_for.append(pid)
        return self.ok


def flock_locks(data_dir):
    """A lock that does not wait, as run.try_lock (lane A) is specified: flock(LOCK_EX | LOCK_NB) on <data>/<name>,
    yielding True when held and False when another holder has it. Another open of the same file in this process
    counts as another holder (BSD flock locks an open file description)."""
    import contextlib
    import fcntl

    @contextlib.contextmanager
    def try_lock(name):
        os.makedirs(data_dir, exist_ok=True)
        fd = os.open(os.path.join(data_dir, name), os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                yield False
                return
            yield True
        finally:
            os.close(fd)
    return try_lock


class Redact:
    """Stands in for redact (lane A): scrub() replaces each made-up secret it is told about."""

    def __init__(self, secrets=()):
        self.secrets = tuple(secrets)

    def env_secrets(self, env):
        return tuple(v for k, v in env.items() if 'KEY' in k and len(v) >= 8)

    def scrub(self, text, known=()):
        n = 0
        for s in self.secrets + tuple(known):
            n += text.count(s)
            text = text.replace(s, '[secret removed]')
        return text, {'known_value': n} if n else {}


PROGRAM = 'fake-codex'
APP_PROGRAM = 'Contents/Resources/codex-cli/bin/' + PROGRAM
PLUGIN_PROGRAM = 'plugins/.plugin-appserver/codex-cli/bin/' + PROGRAM


class Rig:
    """One made-up Mac under a throwaway folder: copies of fake-codex on a made-up PATH, in made-up apps and in the
    Codex folder's plugin place; a Reach of stand-ins whose data folder is this rig's own; a CodexProvider whose facts
    name fake-codex and whose chat reader, choices, lock and scrubber are stand-ins."""

    def __init__(self, test, env=None, tty=False, **reach_args):
        import dataclasses
        import shutil
        import tempfile
        from fakes_providers import make_reach
        from switchboard.providers import codex_facts
        self.root = os.path.realpath(tempfile.mkdtemp(prefix='codex-rig-'))
        test.addCleanup(shutil.rmtree, self.root, True)
        self.bin = os.path.join(self.root, 'bin')
        self.apps = os.path.join(self.root, 'Applications')
        os.makedirs(self.bin)
        os.makedirs(self.apps)
        self.clock = Clock()
        home = os.path.join(self.root, 'home')
        the_env = {'HOME': home, 'PATH': self.bin + ':/usr/bin:/bin', 'LANG': 'en_US.UTF-8'}
        the_env.update(env or {})
        reach, self.calls = make_reach(self.root, env=the_env, path_dirs=(self.bin,), app_dirs=(self.apps,), tty=tty,
                                       wall=self.clock, **reach_args)
        self.data = os.path.join(self.root, 'data')
        self.reach = dataclasses.replace(reach, data_dir=self.data)
        self.home = self.reach.home
        self.facts = dict(codex_facts.FACTS, program_names=(PROGRAM,), app_program=APP_PROGRAM,
                          plugin_program=PLUGIN_PROGRAM, ask_grace=2.0)
        self.chatfiles, self.choices, self.redact = ChatFiles(), Choices(), Redact()
        self.locks = flock_locks(self.data)
        self.provider = self.make()

    def make(self):
        from switchboard.providers.codex import CodexProvider
        p = CodexProvider(self.reach)
        p.facts, p.chatfiles, p.choices, p.locks, p.redact = (self.facts, self.chatfiles, self.choices, self.locks,
                                                               self.redact)
        return p

    def on_path(self, version, **spec):
        """A copy kept elsewhere and linked onto PATH, as npm does."""
        real = FakeCodex(os.path.join(self.root, 'npm', 'lib', 'codex-cli', 'bin'), version, **spec)
        os.symlink(real.path, os.path.join(self.bin, PROGRAM))
        return real

    def app(self, name, version, **spec):
        return make_app(self.apps, name, version, program=APP_PROGRAM, **spec)

    def plugin(self, version, **spec):
        folder, name = os.path.split(os.path.join(self.home, '.codex', PLUGIN_PROGRAM))
        return FakeCodex(folder, version, name=name, **spec)

    def probes(self):
        """The paths that were run with --version, in order."""
        return [argv[0] for argv, _, _, _ in self.calls.runs if list(argv[1:]) == ['--version']]
