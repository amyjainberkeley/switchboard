import _isolate  # (first: it gives switchboard a throwaway data folder)
"""Claude as a provider: its accounts and their numbers, which copy of Claude Code runs, and how it is started.

The accounts come through a small stand-in of accounts/api.py's three read functions (accounts(), readings(),
auto_settings()), so these tests run on any line that has the package. The programs are stand-ins called fake-claude
(tests/fakes_providers.stand_in), built from Claude Code 2.1.309's own --help; the real claude is never run. Every
folder, id, address and pid here is made up, and no pid is a real process: a window is live only when the test says
so (make_reach(alive_pids=...)).
"""
import contextlib
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

import fakes_providers as fp

from switchboard import core
from switchboard.providers import claude, claude_facts, contract, run, words
from switchboard.providers.contract import (IN_APP, IN_TERMINAL, PRINT, ChatRef, Handoff, Launch, ProviderError,
                                            Unsupported)

CHAT_ID = 'aaaaaaaa-0000-4000-8000-00000000000a'
OTHER_ID = 'bbbbbbbb-0000-4000-8000-00000000000b'
HOP = 'h-0a0b0c0d'
NOW = 1_800_000_000.0
A_ID, B_ID, C_ID = ('a1a1a1a1-0000-4000-8000-00000000000a', 'b2b2b2b2-0000-4000-8000-00000000000b',
                    'c3c3c3c3-0000-4000-8000-00000000000c')
A, B, C = 'maya@northwind.example', 'maya@fernhill.example', 'maya@ivy.example'
# A made-up entry of the Codex adapter's in the shared file of found programs (its shape is not Claude's business).
CODEX_ENTRY = {'/made/up/bin/codex': {'size': 10, 'mtime_ns': 1, 'version': '0.169.1'}}

# Every long flag and short flag Claude Code 2.1.309's --help lists (stand-in-sources/claude-shapes/help309.txt).
FLAGS_309 = ('--add-dir', '--agent', '--agents', '--allow-dangerously-skip-permissions', '--allowedTools',
             '--allowed-tools', '--append-system-prompt', '--autocompact', '--ax-screen-reader', '--bg', '--background',
             '--bare', '--betas', '--brief', '--chrome', '--cloud', '-c', '--continue',
             '--dangerously-skip-permissions', '-d', '--debug', '--debug-file', '--desktop', '--disable-slash-commands',
             '--disallowedTools', '--disallowed-tools', '--effort', '--environment',
             '--exclude-dynamic-system-prompt-sections', '--fallback-model', '--file', '--fork-session',
             '--forward-subagent-text', '--from-pr', '-h', '--help', '--ide', '--include-hook-events',
             '--include-partial-messages', '--input-format', '--json-schema', '--max-budget-usd', '--mcp-config',
             '--model', '-n', '--name', '--no-chrome', '--no-session-persistence', '--output-format',
             '--permission-mode', '--permission-prompts', '--permission-prompt-tool', '--plugin-dir', '--plugin-url',
             '-p', '--print', '--prompt-suggestions', '--remote-control', '--remote-control-session-name-prefix',
             '--replay-user-messages', '--restricted', '--strict-mcp-config', '-r', '--resume', '--safe-mode',
             '--session-id', '--setting-sources', '--settings', '--system-prompt', '--system-prompt-snapshot',
             '--teleport', '--tmux', '--tools', '--verbose', '-v', '--version', '-w', '--worktree')

# A stand-in for Claude Code's program, from 2.1.309's --help: it answers --version as the real one does
# ("2.1.309 (Claude Code)"), refuses a flag that help does not list as commander does ("error: unknown option",
# exit 1), and starts no session (exit 3, so a test that ran it by mistake sees it).
FAKE_CLAUDE = '''import sys
VERSION = %r
KNOWN = frozenset(%r)
args = sys.argv[1:]
for a in args:
    if a.startswith('-') and a.split('=', 1)[0] not in KNOWN:
        sys.stderr.write("error: unknown option '%%s'\\n" %% a.split('=', 1)[0])
        sys.exit(1)
if args[:1] in (['--version'], ['-v']):
    sys.stdout.write('%%s (Claude Code)\\n' %% VERSION)
    sys.exit(0)
sys.stderr.write('a stand-in starts no session\\n')
sys.exit(3)
'''


def fake_claude(folder, version='2.1.309', name='fake-claude'):
    return fp.stand_in(folder, name, FAKE_CLAUDE % (version, FLAGS_309))


def account(id_, email, status='ok', alias=None, plan='max'):
    """An account as accounts/api.py's Account has it (only the fields the adapter reads, and a few more)."""
    return types.SimpleNamespace(id=id_, email=email, org_id=None, org_name=None, plan=plan, alias=alias, order=1,
                                 status=status, refresh_expires_at=None, added_at=NOW - 86400, fingerprint=None)


def window(name, pct, resets_at=None):
    return types.SimpleNamespace(name=name, pct=pct, resets_at=resets_at, read_at=None, source=None)


def reading(id_, windows, read_at=NOW - 120, checked_at=None, error=None, with_checked=True):
    """A Reading. with_checked=False: the shape of her Mac's line (ff41063), which has no checked_at."""
    fields = dict(account_id=id_, windows=tuple(windows), read_at=read_at, source='status_line', frozen=False,
                  error=error)
    if with_checked:
        fields['checked_at'] = checked_at
    return types.SimpleNamespace(**fields)


class StandInAccounts(object):
    """accounts/api.py's three read functions, as a test sets them. calls: each name called, in order."""

    def __init__(self, accounts=(), readings=None, settings=None, fail=()):
        self._accounts, self._readings = list(accounts), dict(readings or {})
        self._settings = dict(settings if settings is not None else
                              {'enabled': True, 'session_at': 80.0, 'weekly_at': 90.0, 'model_at': 85.0,
                               'model': None, 'strategy': 'refills-first'})
        self.fail, self.calls = set(fail), []

    def _call(self, name, value):
        self.calls.append(name)
        if name in self.fail:
            raise RuntimeError('made-up failure in %s' % name)
        return value

    def accounts(self):
        return self._call('accounts', list(self._accounts))

    def readings(self):
        return self._call('readings', dict(self._readings))

    def auto_settings(self):
        return self._call('auto_settings', dict(self._settings))


class StandInBridge(object):
    """bridge.py as a test sets it: chat_in_use answers what the test says (None: it cannot tell)."""

    def __init__(self, in_use=None):
        self.in_use, self.asked = in_use, []

    def chat_in_use(self, cid, path):
        self.asked.append((cid, path))
        return self.in_use


class StandInChats(object):
    """claude_chats as lane C writes it: chats(reach, folder, now) and read(reach, chat, limits)."""

    def __init__(self):
        self.calls = []

    def chats(self, reach, folder, now):
        self.calls.append(('chats', folder, now))
        return (ChatRef('claude', CHAT_ID, folder, '/made/up.jsonl'),)

    def read(self, reach, chat, limits):
        self.calls.append(('read', chat.id))
        return 'a transcript'


# ---------- one audit hook for the whole file: what is opened and run, and from which of Switchboard's frames ----

PKG = os.path.dirname(os.path.dirname(os.path.abspath(claude.__file__)))         # .../switchboard
PROVIDERS = os.path.join(PKG, 'providers') + os.sep
SEEN = {'on': False, 'opens': [], 'runs': []}


def _innermost_switchboard_file():
    f = sys._getframe(2)
    while f is not None:
        name = os.path.abspath(f.f_code.co_filename)
        if name.startswith(PKG + os.sep):
            return name
        f = f.f_back
    return None


def _watch(event, args):
    if not SEEN['on']:
        return
    if event == 'open' and args and isinstance(args[0], (str, bytes, os.PathLike)):
        SEEN['opens'].append((os.fsdecode(args[0]), _innermost_switchboard_file()))
    elif event in ('subprocess.Popen', 'os.posix_spawn', 'os.exec', 'os.spawn') and len(args) > 1:
        argv = args[1] if not isinstance(args[1], (str, bytes)) else [args[1]]
        SEEN['runs'].append(tuple(os.fsdecode(a) if isinstance(a, bytes) else str(a) for a in argv))


sys.addaudithook(_watch)


class Mac(unittest.TestCase):
    """A made-up Mac: its home, its PATH folder, a data folder of its own, and a Claude adapter on them."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='sb-claude-')
        self.addCleanup(shutil.rmtree, self.root, True)
        self.data = os.path.join(self.root, 'data')
        os.makedirs(self.data, mode=0o700)
        env = mock.patch.dict(os.environ, {'SWITCHBOARD_HOME': self.data})
        env.start()
        self.addCleanup(env.stop)
        self.bin = os.path.join(self.root, 'bin')
        os.makedirs(self.bin)
        self.home = os.path.join(self.root, 'home')
        self.folder = os.path.join(self.root, 'site')
        os.makedirs(self.folder)
        self.accounts = StandInAccounts()
        self.bridge = StandInBridge()
        self.adapter()

    def adapter(self, env=None, alive_pids=(), wall=None, **facts):
        self.reach, self.calls = fp.make_reach(self.root, env=env, path_dirs=(self.bin,), alive_pids=alive_pids,
                                               wall=wall or (lambda: NOW))
        self.p = claude.ClaudeProvider(self.reach)
        self.p.facts = dict(claude_facts.FACTS, program_names=('fake-claude',),
                            extension_program='resources/native-binary/fake-claude', **facts)
        self.p.accounts = self.accounts
        self.p.bridge = self.bridge
        return self.p

    def versions(self, *vs):
        folder = os.path.join(self.home, '.local', 'share', 'claude', 'versions')
        return [fake_claude(folder, v, name=v) for v in vs]

    def extension(self, version, platform='darwin-arm64'):
        folder = os.path.join(self.home, '.vscode', 'extensions', 'anthropic.claude-code-%s-%s' % (version, platform),
                              'resources', 'native-binary')
        return fake_claude(folder, version)

    def handoff(self, from_provider='codex'):
        notes = os.path.join(self.root, '.switchboard-notes', 't-0a0b0c0d')
        os.makedirs(notes, mode=0o700, exist_ok=True)
        md, meta = os.path.join(notes, HOP + '.md'), os.path.join(notes, HOP + '.json')
        with open(md, 'w') as f:
            f.write('# Switchboard note\n')
        with open(meta, 'w') as f:
            json.dump({'v': 1, 'hop': HOP, 'task': 't-0a0b0c0d', 'from': {'provider': from_provider,
                                                                          'chat': OTHER_ID, 'version': '0.169.1'},
                       'to': {'provider': 'claude'}, 'folder': self.folder, 'made_at': NOW}, f)
        return Handoff(hop=HOP, path=md, meta_path=meta, bytes=18, sha256='0' * 64)

    def chat(self, version='2.1.325', written=NOW - 3600, id_=CHAT_ID):
        project = os.path.join(self.home, '.claude', 'projects', '-made-up-site')
        os.makedirs(project, exist_ok=True)
        path = os.path.join(project, id_ + '.jsonl')
        with open(path, 'w') as f:
            f.write('{}\n')
        os.utime(path, (written, written))
        return ChatRef('claude', id_, self.folder, path, name='made-up chat', version=version)


# ---------- seats and numbers ----------

class SeatsAndLimits(Mac):
    def setUp(self):
        super().setUp()
        self.accounts = StandInAccounts(
            accounts=[account(A_ID, A, alias='work'), account(B_ID, B, plan='pro'), account(C_ID, C)],
            readings={A_ID: reading(A_ID, [window('5-hour', 40.0, NOW + 3600), window('weekly', 70.0, NOW + 86400),
                                           window('model:Fable', 12.5, NOW + 86400)]),
                      B_ID: reading(B_ID, [window('5-hour', 95.0, NOW + 600)], read_at=NOW - 30)})
        self.adapter()

    def test_one_seat_per_account_labelled_by_its_short_name_else_its_address_with_its_plan(self):
        seats = self.p.seats()
        self.assertEqual([(s.provider, s.key, s.label, s.plan) for s in seats],
                         [('claude', A_ID, 'work', 'max'), ('claude', B_ID, B, 'pro'), ('claude', C_ID, C, 'max')])
        self.assertEqual(self.accounts.calls, ['accounts'])

    def test_the_account_in_use_is_the_one_switchboards_state_names(self):
        core.write_json(os.path.join(self.data, 'state.json'), {'v': 2, 'active': B})
        self.assertEqual([s.in_use for s in self.p.seats()], [False, True, False])
        core.write_json(os.path.join(self.data, 'state.json'), {'v': 2, 'active': None})
        self.assertEqual([s.in_use for s in self.p.seats()], [False, False, False])

    def test_no_state_file_means_no_account_is_in_use(self):
        self.assertEqual([s.in_use for s in self.p.seats()], [False, False, False])

    def test_each_window_carries_its_line_from_the_automatic_switching_settings(self):
        got = {l.seat: l for l in self.p.limits()}
        a = got[A_ID]
        self.assertEqual((a.provider, a.source, a.plan, a.read_at, a.reached, a.ask),
                         ('claude', 'engine', 'max', NOW - 120, None, None))
        self.assertEqual([(w.label, w.used_pct, w.resets_at, w.length_min, w.line) for w in a.windows],
                         [('5-hour', 40.0, NOW + 3600, 300, 80.0), ('weekly', 70.0, NOW + 86400, 10080, 90.0),
                          ('Fable', 12.5, NOW + 86400, None, 85.0)])
        self.assertEqual([(w.label, w.line) for w in got[B_ID].windows], [('5-hour', 80.0)])

    def test_the_account_codes_own_lines_when_its_settings_do_not_answer(self):
        self.accounts.fail.add('auto_settings')
        a = {l.seat: l for l in self.p.limits()}[A_ID]
        self.assertEqual([w.line for w in a.windows], [90.0, 95.0, 95.0])

    def test_a_reading_without_checked_at_as_on_the_older_line_reads_the_same(self):
        self.accounts._readings[A_ID] = reading(A_ID, [window('weekly', 50.0)], read_at=NOW - 60, with_checked=False)
        self.assertFalse(hasattr(self.accounts._readings[A_ID], 'checked_at'))
        a = {l.seat: l for l in self.p.limits()}[A_ID]
        self.assertEqual((a.read_at, [w.used_pct for w in a.windows]), (NOW - 60, [50.0]))

    def test_a_reading_with_no_time_of_its_own_takes_the_time_it_was_checked(self):
        self.accounts._readings[A_ID] = reading(A_ID, [window('weekly', None)], read_at=None, checked_at=NOW - 5)
        self.assertEqual({l.seat: l for l in self.p.limits()}[A_ID].read_at, NOW - 5)

    def test_an_account_with_no_reading_has_no_windows_and_says_so(self):
        c = {l.seat: l for l in self.p.limits()}[C_ID]
        self.assertEqual((c.windows, c.read_at, c.source), ((), None, 'none'))
        self.assertTrue(c.error)

    def test_an_account_that_cannot_carry_work_counts_as_no_room(self):
        for status in ('needs_login', 'unreadable', 'claimed', 'conflict', 'something-new'):
            self.accounts._accounts[0] = account(A_ID, A, status=status)
            a = {l.seat: l for l in self.p.limits()}[A_ID]
            self.assertEqual(a.reached, status, status)
            self.assertTrue(a.error, status)
        for status in ('ok', 'expiring'):
            self.accounts._accounts[0] = account(A_ID, A, status=status)
            self.assertIsNone({l.seat: l for l in self.p.limits()}[A_ID].reached, status)

    def test_asking_now_is_not_something_claude_does_and_the_saved_numbers_stay_one_call_away(self):
        got = self.p.limits(ask=True)
        self.assertIsInstance(got, Unsupported)
        self.assertEqual((got.provider, got.what), ('claude', contract.ASK))
        self.assertEqual(self.accounts.calls, [])           # nothing was read for an ask that is not made
        self.assertEqual([l.seat for l in self.p.limits()], [A_ID, B_ID, C_ID])

    def test_an_account_list_that_cannot_be_read_is_one_plain_sentence(self):
        self.accounts.fail.add('accounts')
        for call in (self.p.seats, self.p.limits):
            with self.assertRaises(ProviderError) as e:
                call()
            self.assertEqual(e.exception.kind, 'unreadable')
            self.assertNotIn('made-up failure', str(e.exception))

    def test_readings_that_cannot_be_read_leave_the_accounts_without_numbers(self):
        self.accounts.fail.add('readings')
        got = self.p.limits()
        self.assertEqual([(l.seat, l.windows) for l in got], [(A_ID, ()), (B_ID, ()), (C_ID, ())])
        self.assertTrue(all(l.error for l in got))


class NoLoginIsEverOpened(Mac):
    """The guard, for this adapter: while it finds its programs, reads its seats and numbers and makes its starts, no
    login file and no Keychain is reached, no program called security runs, and the account list (accounts.json) is
    never opened by a frame under switchboard/providers/. Run once on the stand-in accounts and once on the real
    accounts/api.py over a made-up data folder that holds an account list and every login file."""

    def plant(self):
        for rel in contract.LOGIN_FILES:
            path = os.path.join(self.home, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                f.write('{"made": "up"}')
        for rel in contract.LOGIN_DATA_FOLDERS:
            os.makedirs(os.path.join(self.data, rel), exist_ok=True)
        run1 = os.path.join(self.data, 'run', '1')
        os.makedirs(run1, exist_ok=True)
        with open(os.path.join(run1, '.credentials.json'), 'w') as f:
            f.write('{}')
        sessions = os.path.join(self.home, '.claude', 'sessions')
        os.makedirs(sessions, exist_ok=True)
        with open(os.path.join(sessions, '4242.json'), 'w') as f:
            json.dump({'sessionId': CHAT_ID, 'cwd': self.folder}, f)
        with open(os.path.join(sessions, '4242.0a0b0c0d.key'), 'w') as f:
            f.write('made-up')
        from switchboard.accounts import vault
        with open(os.path.join(self.data, 'accounts.json'), 'w') as f:
            json.dump(vault.blank(), f)
        for rel in contract.NO_READ_FILES:
            path = os.path.join(self.home, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                f.write('{}')

    def everything(self):
        fake_claude(self.bin, '2.1.330')
        self.assertEqual(self.p.detect().program.version, '2.1.330')
        self.p.seats()
        self.p.limits()
        self.p.start(self.folder, self.handoff(), IN_TERMINAL)
        with self.assertRaises(ProviderError) as e:         # the planted window holds the chat
            self.p.resume(self.chat(version='2.1.300'), self.handoff(), IN_TERMINAL)
        self.assertEqual(e.exception.kind, 'busy')

    def watched(self):
        SEEN.update(on=True, opens=[], runs=[])
        try:
            self.everything()
        finally:
            SEEN['on'] = False
        return SEEN['opens'], SEEN['runs']

    def check(self, opens, runs):
        for path, frame in opens:
            base = os.path.basename(path)
            self.assertNotIn(base, contract.LOGIN_BASENAMES, path)
            self.assertNotIn(os.sep + 'Keychains' + os.sep, path)              # no Keychain file, either
            self.assertFalse(base.endswith(('.keychain', '.keychain-db')), path)
            self.assertIsNone(contract.LOGIN_NAME_RE.match(base), path)
            for rel in contract.LOGIN_DATA_FOLDERS:
                self.assertFalse(os.path.realpath(path).startswith(os.path.realpath(os.path.join(self.data, rel))))
            if frame is not None and frame.startswith(PROVIDERS):
                self.assertNotIn(base, contract.ENGINE_DATA_FILES, (path, frame))
                for rel in contract.NO_READ_FILES:
                    self.assertNotEqual(os.path.realpath(path), os.path.realpath(os.path.join(self.home, rel)))
        for argv in runs:
            self.assertNotEqual(os.path.basename(argv[0]), 'security', argv)
            self.assertNotIn('find-generic-password', argv)

    def test_on_the_stand_in_accounts(self):
        self.plant()
        self.accounts = StandInAccounts(accounts=[account(A_ID, A)], readings={A_ID: reading(A_ID, [])})
        self.adapter(alive_pids=(4242,))
        opens, runs = self.watched()
        self.check(opens, runs)
        self.assertTrue(any(os.path.basename(p) == '4242.json' for p, _ in opens))     # the window check did look

    def test_on_the_real_account_code(self):
        self.plant()
        env = mock.patch.dict(os.environ, {'HOME': self.home, 'CLAUDE_CONFIG_DIR': os.path.join(self.home, '.claude')})
        env.start()
        self.addCleanup(env.stop)
        self.adapter(alive_pids=(4242,))
        del self.p.accounts                     # back to the default: switchboard.accounts.api, imported on first use
        from switchboard.accounts import api
        self.assertIs(self.p.accounts, api)
        opens, runs = self.watched()
        self.check(opens, runs)
        self.assertEqual(self.p.seats(), ())                        # the made-up list holds no account


# ---------- which program runs ----------

class ProgramChoice(Mac):
    def test_path_versions_and_two_extension_folders_the_newest_is_chosen(self):
        on_path = fake_claude(self.bin, '2.1.309')
        v322, v325 = self.versions('2.1.322', '2.1.325')
        e328, e330 = self.extension('2.1.328'), self.extension('2.1.330', 'darwin-x64')
        found = self.p.detect()
        self.assertTrue(found.installed)
        self.assertEqual(found.provider, 'claude')
        self.assertEqual(found.program.path, os.path.realpath(e330))
        self.assertEqual((found.program.version, found.program.source, found.program.refused),
                         ('2.1.330', 'extension', None))
        self.assertEqual([(p.version, p.source) for p in found.programs],
                         [('2.1.330', 'extension'), ('2.1.328', 'extension'), ('2.1.325', 'versions'),
                          ('2.1.322', 'versions'), ('2.1.309', 'path')])
        self.assertEqual(sorted(p.path for p in found.programs),
                         sorted(os.path.realpath(x) for x in (on_path, v322, v325, e328, e330)))
        self.assertTrue(found.tested)
        self.assertIsNone(found.signed_in)          # never told by opening a login
        # Only the PATH copy says its version by running; the others are named by their folder.
        self.assertEqual([r[0] for r in self.calls.runs], [(os.path.realpath(on_path), '--version')])

    def test_a_tie_goes_to_the_earlier_place(self):
        on_path = fake_claude(self.bin, '2.1.325')
        self.versions('2.1.325')
        self.assertEqual(self.p.detect().program.path, os.path.realpath(on_path))

    def test_a_path_copys_version_is_asked_once_until_the_file_changes(self):
        on_path = fake_claude(self.bin, '2.1.322')
        self.assertEqual(self.p.detect().program.version, '2.1.322')
        self.assertEqual(self.adapter().detect().program.version, '2.1.322')
        self.assertEqual(len(self.calls.runs), 0)                   # the second look kept the answer
        fake_claude(self.bin, '2.1.329')
        st = os.stat(on_path)
        os.utime(on_path, (st.st_atime, st.st_mtime + 5))
        self.assertEqual(self.adapter().detect().program.version, '2.1.329')
        self.assertEqual(len(self.calls.runs), 1)

    # The file of found programs is shared with the Codex adapter: Claude's part is the key 'claude', nothing else.

    def programs_file(self):
        return os.path.join(self.reach.data_dir, contract.PROGRAMS_FILE)

    def put_programs(self, doc=None, raw=None):
        path = self.programs_file()
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        with open(path, 'w') as f:
            f.write(raw if raw is not None else json.dumps(doc))
        return path

    def programs(self):
        with open(self.programs_file()) as f:
            return json.load(f)

    def test_a_codex_entry_in_the_programs_file_is_still_there_unchanged_after_claudes_detect(self):
        on_path = fake_claude(self.bin, '2.1.322')
        others = {'v': 1, 'codex': CODEX_ENTRY, 'programs': CODEX_ENTRY, 'made-up-later': [1, {'a': None}]}
        self.put_programs(others)
        self.assertEqual(self.p.detect().program.version, '2.1.322')
        self.assertEqual(len(self.calls.runs), 1)
        doc = self.programs()
        self.assertEqual({k: v for k, v in doc.items() if k != 'claude'}, others)
        st = os.stat(on_path)
        self.assertEqual(doc['claude'], {os.path.realpath(on_path): [st.st_size, st.st_mtime_ns, '2.1.322']})

    def test_claudes_entry_survives_a_rewrite_that_adds_a_codex_key_beside_it(self):
        on_path = fake_claude(self.bin, '2.1.322')
        self.p.detect()
        doc = self.programs()
        mine = doc['claude']
        doc['codex'] = CODEX_ENTRY                  # another writer of the file adds its own key, as it should
        core.write_json(self.programs_file(), doc)
        self.assertEqual(self.adapter().detect().program.version, '2.1.322')
        self.assertEqual(self.calls.runs, [])       # the kept entry answered: it survived
        self.assertEqual(self.programs(), dict(doc, claude=mine))
        fake_claude(self.bin, '2.1.329')            # a new copy is asked once, and the other key still stays
        st = os.stat(on_path)
        os.utime(on_path, (st.st_atime, st.st_mtime + 5))
        self.assertEqual(self.adapter().detect().program.version, '2.1.329')
        self.assertEqual(len(self.calls.runs), 1)
        after = self.programs()
        self.assertEqual(after['codex'], CODEX_ENTRY)
        self.assertEqual([e[2] for e in after['claude'].values()], ['2.1.329'])

    def test_a_programs_file_that_is_not_a_json_object_is_left_as_it_was(self):
        fake_claude(self.bin, '2.1.322')
        for raw in ('{"codex": {"/made/up/bin/codex": ', '[1, 2]', '"codex"', ''):
            with self.subTest(raw=raw):
                path = self.put_programs(raw=raw)
                with open(path, 'rb') as f:
                    before = f.read()
                self.assertEqual(self.adapter().detect().program.version, '2.1.322')
                with open(path, 'rb') as f:
                    self.assertEqual(f.read(), before)  # whatever another writer has there is not Claude's to drop

    def test_the_programs_file_is_read_changed_and_written_under_the_shared_lock(self):
        fake_claude(self.bin, '2.1.322')
        held, writes = {'now': False, 'names': []}, []
        real_lock, real_write = core.file_lock, core.write_json

        @contextlib.contextmanager
        def lock(name):
            with real_lock(name):
                held['now'] = True
                held['names'].append(name)
                try:
                    yield
                finally:
                    held['now'] = False

        def write(path, data, mode=0o600):
            writes.append((path, held['now']))
            return real_write(path, data, mode)

        with mock.patch.object(core, 'file_lock', lock), mock.patch.object(core, 'write_json', write):
            self.p.detect()
        self.assertEqual(held['names'], [contract.LOCK_NAME])
        self.assertEqual(writes, [(self.programs_file(), True)])

    def test_the_version_probe_gets_the_persons_environment_without_billing_names(self):
        fake_claude(self.bin, '2.1.322')
        self.adapter(env={'HOME': self.home, 'PATH': self.bin, 'ANTHROPIC_API_KEY': 'made-up-key-0123',
                          'CLAUDE_CODE_USE_BEDROCK': '1', 'LANG': 'en_US.UTF-8'})
        self.p.detect()
        (argv, stdin, env, cwd), = self.calls.runs
        self.assertEqual(env, {'HOME': self.home, 'PATH': self.bin, 'LANG': 'en_US.UTF-8'})
        self.assertEqual(cwd, self.home)

    def test_a_path_link_into_the_versions_folder_is_one_copy_named_by_its_folder(self):
        v322, = self.versions('2.1.322')
        os.symlink(v322, os.path.join(self.bin, 'fake-claude'))
        found = self.p.detect()
        self.assertEqual([(p.version, p.source) for p in found.programs], [('2.1.322', 'path')])
        self.assertEqual(self.calls.runs, [])

    def test_a_copy_others_can_change_is_listed_and_never_chosen_or_run(self):
        e330 = self.extension('2.1.330')
        self.extension('2.1.328')
        ext_folder = os.path.dirname(os.path.dirname(os.path.dirname(e330)))
        os.chmod(ext_folder, 0o777)
        self.addCleanup(os.chmod, ext_folder, 0o755)
        on_path = fake_claude(self.bin, '2.1.329')
        os.chmod(self.bin, 0o775)       # group-writable by the person's own group (not admin or wheel)
        self.addCleanup(os.chmod, self.bin, 0o755)
        found = self.p.detect()
        self.assertEqual(found.program.version, '2.1.328')
        refused = {p.path: p.refused for p in found.programs if p.refused}
        self.assertEqual(sorted(refused), sorted([os.path.realpath(e330), os.path.realpath(on_path)]))
        self.assertEqual(set(refused.values()), {'its folder can be changed by other users'})
        self.assertEqual(self.calls.runs, [])                       # a refused copy is never probed
        on = {p.path: p.version for p in found.programs}
        self.assertIsNone(on[os.path.realpath(on_path)])

    def test_a_world_writable_program_file_is_refused(self):
        v, = self.versions('2.1.330')
        os.chmod(v, 0o777)
        found = self.p.detect()
        self.assertIsNone(found.program)
        self.assertTrue(found.installed)
        self.assertTrue(found.problems)

    def test_the_desktop_apps_linux_copy_is_never_a_candidate(self):
        vm = os.path.join(self.home, 'Library', 'Application Support', 'Claude', 'claude-code-vm', '2.1.399')
        fake_claude(vm, '2.1.399')
        fake_claude(os.path.join(vm, 'bin'), '2.1.399')
        self.adapter()
        self.reach, self.calls = fp.make_reach(self.root, path_dirs=(self.bin, os.path.join(vm, 'bin'), vm),
                                               wall=lambda: NOW)
        p = claude.ClaudeProvider(self.reach)
        p.facts = self.p.facts
        self.versions('2.1.325')
        found = p.detect()
        self.assertEqual([x.version for x in found.programs], ['2.1.325'])
        self.assertEqual(self.calls.runs, [])

    def test_a_file_that_cannot_be_run_or_a_folder_is_not_a_copy(self):
        v, = self.versions('2.1.330')
        os.chmod(v, 0o644)
        os.makedirs(os.path.join(self.bin, 'fake-claude'))
        found = self.p.detect()
        self.assertEqual((found.installed, found.program, found.programs), (False, None, ()))

    def test_nothing_found_is_not_installed_and_a_start_says_so(self):
        found = self.p.detect()
        self.assertEqual((found.installed, found.program), (False, None))
        self.assertTrue(found.problems)
        for call in (lambda: self.p.start(self.folder, self.handoff(), IN_TERMINAL),
                     lambda: self.p.resume(self.chat(), self.handoff(), IN_TERMINAL)):
            with self.assertRaises(ProviderError) as e:
                call()
            self.assertEqual(e.exception.kind, 'not_installed')

    def test_a_version_outside_the_checked_range_is_not_tested(self):
        self.versions('2.1.400')
        self.assertFalse(self.p.detect().tested)

    def test_claudes_folders_follow_the_reach_never_this_process(self):
        os.environ['CLAUDE_CONFIG_DIR'] = os.path.join(self.root, 'elsewhere')       # (put back by setUp's patch)
        self.versions('2.1.325')
        self.assertEqual(self.p.detect().program.version, '2.1.325')


# ---------- starting it ----------

class Launches(Mac):
    def setUp(self):
        super().setUp()
        self.program = os.path.realpath(self.versions('2.1.330')[0])

    def test_start_runs_the_chosen_program_with_the_first_words_in_the_folder(self):
        h = self.handoff()
        got = self.p.start(self.folder, h, IN_TERMINAL)
        first = words.FIRST_WORDS.format(hop=HOP, path=h.path, from_name='Codex')
        self.assertIsInstance(got, Launch)
        self.assertEqual((got.provider, got.how, got.cwd, got.first_words), ('claude', IN_TERMINAL, self.folder, first))
        self.assertEqual(got.argv, (self.program, first))
        self.assertEqual(got.program.version, '2.1.330')
        self.assertEqual(got.line, run.shell_line(got.argv, self.folder, self.reach.env))
        self.assertEqual(self.calls.execs, [])          # an adapter never starts anything itself
        self.assertEqual(self.calls.runs, [])

    def test_resume_opens_the_chat_again_with_the_words_to_come_back_on(self):
        h = self.handoff()
        chat = self.chat()
        got = self.p.resume(chat, h, IN_TERMINAL)
        back = words.BACK_WORDS.format(hop=HOP, path=h.path, other_name='Codex')
        self.assertEqual(got.argv, (self.program, '--resume', CHAT_ID, back))
        self.assertEqual((got.cwd, got.first_words, got.how), (self.folder, back, IN_TERMINAL))
        self.assertEqual(self.bridge.asked, [(CHAT_ID, chat.path)])

    def test_print_gives_the_line_to_type_and_the_same_start(self):
        got = self.p.start(self.folder, self.handoff(), PRINT)
        self.assertEqual(got.how, PRINT)
        self.assertTrue(got.line.startswith('cd %s && ' % self.folder))
        self.assertIn(self.program, got.line)
        got = self.p.resume(self.chat(), self.handoff(), PRINT)
        self.assertIn('--resume %s' % CHAT_ID, got.line)

    def test_never_a_flag_that_loosens_permissions_or_runs_without_a_person(self):
        for got in (self.p.start(self.folder, self.handoff(), IN_TERMINAL),
                    self.p.resume(self.chat(), self.handoff(), IN_TERMINAL)):
            for flag in claude_facts.NEVER_FLAGS + ('--dangerously-skip-permissions', '--permission-mode', '-p'):
                self.assertNotIn(flag, got.argv[:-1])
            self.assertFalse([a for a in got.argv[1:-1] if a.startswith('-') and a != '--resume'])

    def test_the_desktop_link_is_not_there_for_claude(self):
        got = self.p.start(self.folder, self.handoff(), IN_APP)
        self.assertIsInstance(got, Unsupported)
        self.assertEqual((got.provider, got.what, got.why), ('claude', contract.START_APP, words.NO_CLAUDE_LINK))
        got = self.p.resume(self.chat(), self.handoff(), IN_APP)
        self.assertIsInstance(got, Unsupported)
        self.assertEqual(got.why, words.NO_CLAUDE_LINK)
        self.assertEqual(self.calls.links, [])

    def test_the_environment_is_the_reachs_without_any_billing_name_cloud_switches_too(self):
        env = {'HOME': self.home, 'PATH': self.bin, 'KEEP_ME': 'yes', 'ANTHROPIC_API_KEY': 'made-up-key-0123',
               'ANTHROPIC_AUTH_TOKEN': 'made-up', 'CLAUDE_CODE_OAUTH_TOKEN': 'made-up',
               'CLAUDE_CODE_USE_BEDROCK': '1', 'CLAUDE_CODE_USE_VERTEX': '1', 'CLAUDE_CODE_USE_FOUNDRY': '1',
               'ANTHROPIC_BASE_URL': 'https://gateway.example', 'OPENAI_API_KEY': 'made-up'}
        self.adapter(env=env)
        os.environ['SB_ONLY_IN_THIS_PROCESS'] = 'not-the-reach'          # (put back by setUp's patch)
        for got in (self.p.start(self.folder, self.handoff(), IN_TERMINAL),
                    self.p.resume(self.chat(), self.handoff(), IN_TERMINAL)):
            self.assertEqual(got.env, {'HOME': self.home, 'PATH': self.bin, 'KEEP_ME': 'yes'})
            self.assertEqual(got.env, run.child_env(env))
            self.assertIn(' && env -u ', got.line)
            for name in ('ANTHROPIC_API_KEY', 'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'OPENAI_API_KEY'):
                self.assertIn(' -u %s ' % name, got.line)
            self.assertNotIn('made-up', repr(got))

    def test_a_chat_id_of_another_shape_never_reaches_an_argument_list(self):
        for bad in ('../x', 'AAAAAAAA-0000-4000-8000-00000000000A', CHAT_ID + '; rm', '--help', '', CHAT_ID + '\n'):
            chat = ChatRef('claude', bad, self.folder, os.path.join(self.root, 'x.jsonl'), version='2.1.325')
            with self.assertRaises(ProviderError, msg=repr(bad)):
                self.p.resume(chat, self.handoff(), IN_TERMINAL)
        self.assertEqual(self.bridge.asked, [])

    def test_resume_refuses_a_program_older_than_the_one_that_wrote_the_chat(self):
        with self.assertRaises(ProviderError) as e:
            self.p.resume(self.chat(version='2.1.331'), self.handoff(), IN_TERMINAL)
        self.assertEqual(e.exception.kind, 'too_old')
        self.assertEqual(str(e.exception), words.TOO_OLD.format(name='Claude Code', version='2.1.330',
                                                                seen='2.1.331'))

    def test_resume_goes_on_when_the_chat_is_as_old_or_older_or_says_no_version(self):
        for v in ('2.1.330', '2.1.309', None):
            self.assertIsInstance(self.p.resume(self.chat(version=v), self.handoff(), IN_TERMINAL), Launch, v)

    def test_a_start_is_never_too_old(self):
        self.assertIsInstance(self.p.start(self.folder, self.handoff(), IN_TERMINAL), Launch)

    def said(self, h):
        """The name each of start and resume gives the side the note came from."""
        start = self.p.start(self.folder, h, IN_TERMINAL).first_words
        back = self.p.resume(self.chat(), h, IN_TERMINAL).first_words
        names = {n for n in ('Codex', 'Claude', 'another assistant', 'Zzz', 'zzz')
                 if start == words.FIRST_WORDS.format(hop=HOP, path=h.path, from_name=n)
                 and back == words.BACK_WORDS.format(hop=HOP, path=h.path, other_name=n)}
        self.assertEqual(len(names), 1, (start, back))
        return names.pop()

    def test_a_known_provider_in_the_notes_sidecar_is_named_from_the_table(self):
        self.assertEqual(self.said(self.handoff(from_provider='codex')), 'Codex')
        self.assertEqual(self.said(self.handoff(from_provider='claude')), 'Claude')

    def test_an_id_the_registry_does_not_know_is_another_assistant(self):
        for pid in ('zzz', 'Codex', '../codex', 'codex ', 7, None, ['codex']):
            self.assertEqual(self.said(self.handoff(from_provider=pid)), 'another assistant', pid)

    def test_no_sidecar_or_one_that_cannot_be_read_is_another_assistant(self):
        h = self.handoff()
        os.remove(h.meta_path)
        self.assertEqual(self.said(h), 'another assistant')
        for raw in ('not json', '[]', '{"from": "codex"}', '{"to": {"provider": "codex"}}'):
            h = self.handoff()
            with open(h.meta_path, 'w') as f:
                f.write(raw)
            self.assertEqual(self.said(h), 'another assistant', raw)

    def test_no_word_of_a_chat_is_in_a_launch(self):
        got = self.p.resume(self.chat(), self.handoff(), IN_TERMINAL)
        self.assertEqual(got.argv[-1], words.BACK_WORDS.format(hop=HOP, path=self.handoff().path, other_name='Codex'))
        self.assertNotIn('made-up chat', ' '.join(got.argv) + got.line)


class ResumeWhileOpen(Mac):
    def setUp(self):
        super().setUp()
        self.versions('2.1.330')
        self.sessions = os.path.join(self.home, '.claude', 'sessions')
        os.makedirs(self.sessions)

    def window(self, pid, session_id=CHAT_ID, raw=None):
        with open(os.path.join(self.sessions, '%d.json' % pid), 'w') as f:
            f.write(raw if raw is not None else json.dumps({'sessionId': session_id, 'cwd': self.folder,
                                                            'status': 'idle', 'peerToken': 'made-up'}))

    def busy(self):
        with self.assertRaises(ProviderError) as e:
            self.p.resume(self.chat(), self.handoff(), IN_TERMINAL)
        self.assertEqual(e.exception.kind, 'busy')
        self.assertTrue(str(e.exception))

    def test_a_live_window_on_the_chat_holds_it(self):
        self.window(4242)
        self.adapter(alive_pids=(4242,))
        self.busy()

    def test_a_window_whose_process_is_gone_or_on_another_chat_does_not(self):
        self.window(4242)
        self.window(4343, session_id=OTHER_ID)
        self.window(4444, raw='{not json')
        self.window(4545, raw='[]')
        self.adapter(alive_pids=(4343, 4444, 4545))
        self.assertIsInstance(self.p.resume(self.chat(), self.handoff(), IN_TERMINAL), Launch)

    def test_a_window_file_too_big_or_of_another_name_is_not_read(self):
        self.window(4242, raw=json.dumps({'sessionId': CHAT_ID, 'pad': 'x' * (claude_facts.WINDOW_MAX + 10)}))
        with open(os.path.join(self.sessions, '0.json'), 'w') as f:
            json.dump({'sessionId': CHAT_ID}, f)
        with open(os.path.join(self.sessions, 'x4242.json'), 'w') as f:
            json.dump({'sessionId': CHAT_ID}, f)
        self.adapter(alive_pids=(4242, 0))
        self.assertIsInstance(self.p.resume(self.chat(), self.handoff(), IN_TERMINAL), Launch)

    def test_a_dead_window_is_never_opened_nor_its_key(self):
        self.window(4242)
        with open(os.path.join(self.sessions, '4242.0a0b0c0d.key'), 'w') as f:
            f.write('made-up')
        SEEN.update(on=True, opens=[], runs=[])
        try:
            self.p.resume(self.chat(), self.handoff(), IN_TERMINAL)
        finally:
            SEEN['on'] = False
        opened = [os.path.basename(p) for p, _ in SEEN['opens']]
        self.assertNotIn('4242.json', opened)               # its process is not alive: nothing to read
        self.assertFalse([n for n in opened if n.endswith('.key')])

    def test_a_chat_written_in_the_last_minute_is_still_being_written(self):
        chat = self.chat(written=NOW - 10)
        with self.assertRaises(ProviderError) as e:
            self.p.resume(chat, self.handoff(), IN_TERMINAL)
        self.assertEqual(e.exception.kind, 'busy')
        chat = self.chat(written=NOW - 61)
        self.assertIsInstance(self.p.resume(chat, self.handoff(), IN_TERMINAL), Launch)

    def test_the_account_codes_own_check_through_the_bridge_holds_it_too(self):
        self.bridge.in_use = True
        self.busy()
        self.bridge.in_use = False
        self.assertIsInstance(self.p.resume(self.chat(), self.handoff(), IN_TERMINAL), Launch)

    def test_claudes_folder_is_the_reachs_claude_config_dir_when_set(self):
        other = os.path.join(self.root, 'profile')
        os.makedirs(os.path.join(other, 'sessions'))
        with open(os.path.join(other, 'sessions', '4242.json'), 'w') as f:
            json.dump({'sessionId': CHAT_ID}, f)
        self.adapter(env={'HOME': self.home, 'CLAUDE_CONFIG_DIR': other}, alive_pids=(4242,))
        self.busy()


# ---------- what it does not do, and what it hands to others ----------

class TheRest(Mac):
    def test_its_names_and_what_it_can_do(self):
        self.assertEqual((self.p.id, self.p.name, self.p.vendor), ('claude', 'Claude', 'Anthropic'))
        self.assertEqual(self.p.can, frozenset((contract.DETECT, contract.SEATS, contract.LIMITS, contract.CHATS,
                                                contract.READ, contract.START, contract.RESUME)))
        self.assertTrue(self.p.can <= contract.CAPABILITIES)

    def test_every_capability_outside_can_answers_unsupported_through_the_method_a_caller_uses(self):
        """The contract's rule, as lane D's contract test holds every adapter to it: a method outside `can` answers
        Unsupported and never raises; limits(ask=True) is the ask method, start(..., IN_APP) the app start."""
        self.versions('2.1.330')
        h, chat = self.handoff(), self.chat()
        out = os.path.join(self.root, 'a.txt')
        calls = {
            contract.DETECT: lambda: self.p.detect(),
            contract.SEATS: lambda: self.p.seats(),
            contract.LIMITS: lambda: self.p.limits(),
            contract.ASK: lambda: self.p.limits(ask=True),
            contract.CHATS: lambda: self.p.chats(self.folder),
            contract.READ: lambda: self.p.read(chat),
            contract.START: lambda: self.p.start(self.folder, h, IN_TERMINAL),
            contract.START_APP: lambda: self.p.start(self.folder, h, IN_APP),
            contract.RESUME: lambda: self.p.resume(chat, h, IN_TERMINAL),
            contract.JOB: lambda: self.p.job(self.folder, 'made-up job', out, 5.0),
            contract.NOTE: lambda: self.p.note(chat),
        }
        self.assertEqual(set(calls), set(contract.CAPABILITIES))        # a new capability needs its line here
        outside = sorted(contract.CAPABILITIES - self.p.can)
        self.assertEqual(outside, sorted((contract.ASK, contract.START_APP, contract.JOB, contract.NOTE)))
        for broken in ((), ('accounts', 'readings', 'auto_settings')):  # also when the account code fails
            self.accounts.fail = set(broken)
            for what in outside:
                with self.subTest(what=what, broken=broken):
                    got = calls[what]()
                    self.assertIsInstance(got, Unsupported)
                    self.assertEqual((got.provider, got.what), ('claude', what))
                    self.assertTrue(got.why)
        self.assertEqual(self.accounts.calls, [])
        self.assertEqual((self.calls.runs, self.calls.execs, self.calls.links), ([], [], []))

    def test_a_job_and_the_apps_own_note_are_not_in_this_version(self):
        got = self.p.job(self.folder, 'made-up job', os.path.join(self.root, 'a.txt'))
        self.assertIsInstance(got, Unsupported)
        self.assertEqual((got.what, got.why), (contract.JOB, words.NO_CLAUDE_JOB))
        self.assertIsInstance(self.p.note(self.chat()), Unsupported)
        self.assertEqual(self.calls.runs, [])

    def test_chats_and_reading_go_to_the_chat_reader(self):
        stand_in = StandInChats()
        self.p.chatfiles = stand_in
        got = self.p.chats(self.folder)
        self.assertEqual([c.id for c in got], [CHAT_ID])
        self.assertEqual(self.p.read(got[0]), 'a transcript')
        self.assertEqual(stand_in.calls, [('chats', self.folder, NOW), ('read', CHAT_ID)])

    def test_the_registry_loads_it(self):
        from switchboard import providers
        got = providers.load('claude', self.reach)
        self.assertIsInstance(got, claude.ClaudeProvider)
        self.assertIs(got.reach, self.reach)

    def test_its_facts_say_the_versions_they_were_checked_on(self):
        self.assertEqual(claude_facts.FACTS['checked'], ('2.1.309', '2.1.330'))
        self.assertTrue(claude_facts.VERSION_RE.match('2.1.330 (Claude Code)'))
        self.assertEqual(run.version_key(claude_facts.VERSION_RE.match('2.1.309 (Claude Code)').group(1)),
                         (2, 1, 309, 1, 0, 0))



class TheTestGuard(unittest.TestCase):
    """What the adapter promises never to open (Claude Code's settings, a login of the account code's, its recovery
    copies and its runs' files), the test guard (tests/_isolate.py) refuses to read on this Mac too. The guard is asked
    directly: no real file is opened here, whether the guard is there or not."""

    PLACES = (_isolate.REAL_SETTINGS, os.path.join(_isolate.REAL_DATA, 'logins', 'made-up.json'),
              os.path.join(_isolate.REAL_DATA, 'recovery', 'made-up'), os.path.join(_isolate.REAL_DATA, 'run', 'x'),
              os.path.join(_isolate.REAL_DATA, 'logins'))

    def test_a_read_of_each_place_is_refused(self):
        for path in self.PLACES:
            with self.subTest(path=path):
                self.assertTrue(_isolate._never_read(path))
                before = list(_isolate.VIOLATIONS)
                try:
                    with self.assertRaises(PermissionError):
                        _isolate._hook('open', (path, 'r', os.O_RDONLY))
                finally:
                    del _isolate.VIOLATIONS[len(before):]          # this test's own, on purpose

    def test_the_rest_of_the_data_folder_and_a_temp_file_are_not(self):
        for path in (os.path.join(_isolate.REAL_DATA, 'state.json'), os.path.join(_isolate.REAL_DATA, 'runs'),
                     os.path.join(tempfile.gettempdir(), 'settings.json')):
            self.assertFalse(_isolate._never_read(path), path)


if __name__ == '__main__':
    unittest.main()
