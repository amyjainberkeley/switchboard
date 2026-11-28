"""Stand-ins for a hand-over's two programs (switchboard/router/hand.py): fake-codex and fake-claude.

Each is a small Python program a test writes into its own folder. It answers --version with the version in its spec,
and for any other run it records its arguments, its whole environment (only made-up values are ever in it), its
standard input, its folder and its process id, one JSON object per line in its record file, then does what its spec
says: sleep, end itself by a signal, write an answer, write to standard error, exit with a code. fake-codex writes its
answer to the file named after -o, as `codex exec` does; fake-claude prints stream-json lines (an init line with
apiKeySource, a rate-limit line, filler lines, a result line), as `claude -p --output-format stream-json --verbose`
does. The spec is a JSON file read at each run, so a test changes it between runs. Never a vendor's program, never a
network. No test here; tests/test_router_hand.py holds the tests.
"""
import json
import os

from fakes_providers import stand_in

_COMMON = r'''
import json, os, signal, sys, time
SPEC, RECORD = %(spec)r, %(record)r
with open(SPEC) as f:
    s = json.load(f)
args = sys.argv[1:]
if args[:1] in (['--version'], ['-V'], ['-v']):
    print(%(version_line)s)
    sys.exit(0)
data = sys.stdin.read()
with open(RECORD, 'a') as f:
    f.write(json.dumps({'argv': args, 'env': dict(os.environ), 'stdin': data, 'cwd': os.getcwd(),
                        'pid': os.getpid()}) + '\n')
time.sleep(s.get('sleep', 0))
if s.get('kill'):
    os.kill(os.getpid(), signal.SIGKILL)
'''

_CODEX = _COMMON + r'''
if s.get('answer') is not None and '-o' in args:
    with open(args[args.index('-o') + 1], 'w') as f:
        f.write(s['answer'])
sys.stderr.write(s.get('stderr', ''))
sys.exit(s.get('exit', 0))
'''

_CLAUDE = _COMMON + r'''
lines = []
if s.get('init', True):
    lines.append({'type': 'system', 'subtype': 'init', 'apiKeySource': s.get('source', 'none'), 'tools': []})
if s.get('rejected'):
    lines.append({'type': 'rate_limit_event', 'rate_limit_info': {'status': 'rejected'}})
for _ in range(s.get('noise', 0)):
    lines.append({'type': 'user', 'message': {'content': 'x' * 4000}})
if s.get('result', True):
    lines.append({'type': 'result', 'subtype': 'error' if s.get('is_error') else 'success',
                  'is_error': bool(s.get('is_error')), 'result': s.get('answer', '')})
for line in lines:
    sys.stdout.write(json.dumps(line) + '\n')
sys.stdout.flush()
sys.stderr.write(s.get('stderr', ''))
sys.exit(s.get('exit', 0))
'''


class Program:
    """One stand-in: its path, its spec (set()), and what it recorded (runs())."""

    def __init__(self, folder, name, source, version_line, spec):
        self.spec_path = os.path.join(folder, name + '.spec.json')
        self.record_path = os.path.join(folder, name + '.record.jsonl')
        self.set(**spec)
        self.path = stand_in(folder, name, source % {'spec': self.spec_path, 'record': self.record_path,
                                                     'version_line': version_line})

    def set(self, **spec):
        with open(self.spec_path, 'w') as f:
            json.dump(spec, f)

    def runs(self):
        """Every run but --version, oldest first: {'argv', 'env', 'stdin', 'cwd', 'pid'}."""
        try:
            with open(self.record_path) as f:
                return [json.loads(line) for line in f if line.strip()]
        except FileNotFoundError:
            return []


def fake_codex(folder, version='0.169.1', **spec):
    """fake-codex in folder; --version prints `codex-cli <version>`."""
    return Program(folder, 'fake-codex', _CODEX, repr('codex-cli ' + version), spec)


def fake_claude(folder, version='2.1.330', **spec):
    """fake-claude in folder; --version prints `<version> (Claude Code)`."""
    return Program(folder, 'fake-claude', _CLAUDE, repr(version + ' (Claude Code)'), spec)
