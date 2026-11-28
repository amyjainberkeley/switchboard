import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# providers.view: every provider on one board, built from files only (DESIGN section 20).
#
# - With the real Claude and Codex adapters on a made-up Mac: Claude has room (one of its two accounts), Codex is out
#   on its saved numbers; Codex's version is the one kept in PROGRAMS_FILE; there is somewhere to carry a chat on.
# - While the board is built nothing starts (no process, even for a program whose version is not kept yet), no
#   connection is made, and no login file is opened (an audit hook watches the whole build).
# - Codex with no readings is "numbers not known"; added and not on this Mac is "not on this Mac"; a damaged readings,
#   programs or session file never raises and leaves its numbers "not known"; an adapter that raises is "not known"
#   while the others show; a board that cannot be built at all is an empty one that says so.
# - The words the terminal, the menu bar and doctor say for it, at a given moment.
import dataclasses
import json
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

import fakes_provider_chats as F  # noqa: E402
from fakes_providers import make_reach, stand_in  # noqa: E402
from switchboard import core  # noqa: E402
from switchboard.providers import choices, cli, contract, view, words  # noqa: E402

DAY = 86400.0
# A stand-in that leaves a mark when anything runs it: the board must never.
MARKS = 'import os, sys\nopen(os.path.join(os.path.dirname(sys.argv[0]), "ran-" + os.path.basename(sys.argv[0])), "w").close()\n'

SEEN = {'on': False, 'runs': [], 'connects': [], 'opens': []}


def _watch(event, args):
    if not SEEN['on']:
        return
    if event in ('subprocess.Popen', 'os.posix_spawn', 'os.exec', 'os.spawn', 'os.system'):
        SEEN['runs'].append(event)
    elif event == 'socket.connect':
        SEEN['connects'].append(event)
    elif event == 'open' and args and isinstance(args[0], (str, bytes, os.PathLike)):
        SEEN['opens'].append(os.fsdecode(args[0]))


sys.addaudithook(_watch)


def account(id_, email, status='ok'):
    return types.SimpleNamespace(id=id_, email=email, org_id=None, org_name=None, plan='max', alias=None, order=1,
                                 status=status, refresh_expires_at=None, added_at=0, fingerprint=None)


def reading(id_, pct, now, resets_in=3600.0):
    w = types.SimpleNamespace(name='5-hour', pct=pct, resets_at=now + resets_in, read_at=None, source=None)
    return types.SimpleNamespace(account_id=id_, windows=(w,), read_at=now - 120, checked_at=now - 120,
                                 source='status_line', frozen=False, error=None)


class StandInAccounts(object):
    """accounts/api.py's three read functions: two accounts, the first with room, the second at 100%."""

    def __init__(self, now):
        self.now = now

    def accounts(self):
        return [account('acct-1', 'ana@work.example'), account('acct-2', 'ana@home.example')]

    def readings(self):
        return {'acct-1': reading('acct-1', 30.0, self.now), 'acct-2': reading('acct-2', 100.0, self.now)}

    def auto_settings(self):
        return {'enabled': True, 'session_at': 90.0, 'weekly_at': 95.0, 'model_at': 95.0}


class Board(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix='sb-view-'))
        self.addCleanup(shutil.rmtree, self.root, True)
        shutil.rmtree(os.path.join(core.data_dir(), contract.DATA_DIR), True)
        self.home = os.path.join(self.root, 'home')
        self.bin = os.path.join(self.root, 'bin')
        os.makedirs(self.bin)
        self.chome = F.codex_home(self.home)
        self.reach, self.calls = make_reach(self.root, env={'HOME': self.home, 'PATH': self.bin},
                                            path_dirs=(self.bin,))
        self.now = time.time()
        self.codex = stand_in(self.bin, 'fake-codex', MARKS)
        self.claude = stand_in(self.bin, 'fake-claude', MARKS)
        choices.add('codex', self.now - DAY)

    def keep_version(self, path, version='0.169.1'):
        """The version Switchboard kept for this copy when it last asked it (PROGRAMS_FILE, Codex's key)."""
        st = os.stat(path)
        cache = os.path.join(core.data_dir(), contract.PROGRAMS_FILE)
        os.makedirs(os.path.dirname(cache), mode=0o700, exist_ok=True)
        with open(cache, 'w') as f:
            json.dump({'v': 1, 'programs': {os.path.realpath(path): {'size': st.st_size, 'mtime_ns': st.st_mtime_ns,
                                                                       'version': version}}}, f)

    def codex_numbers(self, pct, resets_in=2 * DAY, window_minutes=10080):
        c = F.CodexChat(thread='cccccccc-0000-4000-8000-000000000001', cwd='/made-up/site', t0=self.now - 600)
        c.user_item('x')
        c.token_count(primary={'used_percent': pct, 'window_minutes': window_minutes,
                               'resets_at': int(self.now + resets_in)}, at=float(int(self.now) - 300))
        path = c.write(self.chome)
        F.touch(path, self.now - 300)
        return path

    def kit(self, codex_load=None):
        """The real Kit, its adapters pointed at the stand-in program names and Claude at its stand-in accounts."""
        base = cli.default_kit(self.reach)

        def load(pid):
            if pid == 'codex' and codex_load is not None:
                return codex_load(base.load(pid))
            prov = base.load(pid)
            if prov is None:
                return None
            prov.facts = dict(prov.facts, program_names=('fake-' + pid,))
            if pid == 'claude':
                prov.accounts = StandInAccounts(self.now)
            return prov
        return dataclasses.replace(base, load=load, say=lambda *a, **k: None)

    def board(self, **kw):
        SEEN.update(on=True, runs=[], connects=[], opens=[])
        try:
            return view.view(kit=self.kit(**kw))
        finally:
            SEEN['on'] = False

    def entry(self, board, pid):
        return next(p for p in board['providers'] if p['id'] == pid)

    def assert_nothing_started(self):
        self.assertEqual(SEEN['runs'], [])
        self.assertEqual(SEEN['connects'], [])
        self.assertEqual([p for p in os.listdir(self.bin) if p.startswith('ran-')], [])
        logins = [p for p in SEEN['opens'] if os.path.basename(p) in contract.LOGIN_BASENAMES]
        self.assertEqual(logins, [])
        self.assertEqual(self.calls.runs, [])


class ClaudeHasRoomCodexIsOut(Board):
    def test_the_board(self):
        self.keep_version(self.codex)
        self.codex_numbers(100.0)
        with open(os.path.join(self.chome, 'auth.json'), 'w') as f:      # its existence is all that is looked at
            f.write('{"made": "up"}')
        b = self.board()
        self.assert_nothing_started()
        self.assertEqual((b['v'], b['order'], b['carry'], b['add']), (1, ['claude', 'codex'], True, []))
        claude = self.entry(b, 'claude')
        self.assertEqual((claude['state'], claude['account_list'], claude['count'], claude['with_room'],
                          claude['seats']), (contract.ROOM, True, 2, 1, []))
        self.assertEqual(claude['app'], 'Claude Code')
        self.assertIsNone(claude['version'])            # never asked: its copy was never run
        codex = self.entry(b, 'codex')
        self.assertEqual((codex['state'], codex['version'], codex['installed'], codex['signed_in'], codex['plan']),
                         (contract.OUT, '0.169.1', True, True, 'ChatGPT Pro'))
        self.assertAlmostEqual(codex['back_at'], int(self.now + 2 * DAY), delta=1)
        self.assertEqual(codex['seats'][0]['windows'][0]['label'], 'weekly')
        self.assertEqual(codex['seats'][0]['windows'][0]['used_pct'], 100.0)
        self.assertEqual(codex['seats'][0]['windows'][0]['line'], 95.0)
        self.assertTrue(codex['why'].startswith('is out until '))
        json.dumps(b, allow_nan=False)                  # it goes into /api/state as it is

    def test_a_copy_whose_version_was_never_kept_is_not_run(self):
        self.codex_numbers(40.0)
        b = self.board()
        self.assert_nothing_started()
        codex = self.entry(b, 'codex')
        self.assertEqual((codex['installed'], codex['version']), (True, None))
        self.assertEqual(codex['state'], contract.ROOM)                 # its numbers still show

    def test_the_board_from_a_reach_alone_starts_nothing_either(self):
        SEEN.update(on=True, runs=[], connects=[], opens=[])
        try:
            b = view.view(self.reach)
        finally:
            SEEN['on'] = False
        self.assert_nothing_started()
        self.assertEqual([p['id'] for p in b['providers']], ['claude', 'codex'])


class TheFourFields(Board):
    """What a row needs of an entry, from the same files and still with nothing started: the account's email, a
    desktop app among its copies, the newest version that wrote its chats, and why it cannot be continued to (None:
    as far as files can tell, it can)."""

    def sign_in(self):
        with open(os.path.join(self.chome, 'auth.json'), 'w') as f:      # its existence is all that is looked at
            f.write('{"made": "up"}')

    def keep_email(self, email, login=True):
        """The email Codex said when it was last asked, kept beside its numbers (contract.READINGS_FILE). login:
        whether Codex's sign-in file was there then (False: its sign-in is in the Keychain)."""
        path = os.path.join(core.data_dir(), contract.READINGS_FILE)
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        entry = {'seat': 'codex', 'read_at': self.now - 60, 'plan': 'plus', 'reached': None,
                 'windows': [{'label': 'weekly', 'used_pct': 21.0, 'resets_at': self.now + 2 * DAY,
                              'length_min': 10080}],
                 'email': email, 'email_at': time.time(), 'login': login}       # read after the sign-in file was
        with open(path, 'w') as f:
            json.dump({'v': 1, 'codex': entry}, f)

    def test_a_codex_that_can_be_continued_to(self):
        self.keep_version(self.codex)
        self.codex_numbers(40.0)
        self.sign_in()
        self.keep_email('ana@made-up.example')
        b = self.board()
        self.assert_nothing_started()
        codex = self.entry(b, 'codex')
        self.assertEqual((codex['email'], codex['has_app'], codex['newest_seen'], codex['fit_why']),
                         ('ana@made-up.example', False, '0.169.1', None))
        self.assertIsNone(self.entry(b, 'claude')['fit_why'])             # its accounts are the rows above
        self.assertEqual(b['v'], 1)
        json.dumps(b, allow_nan=False)

    def test_signed_in_through_the_keychain_a_kept_email_is_a_sign_in(self):
        # No sign-in file: Codex keeps its sign-in in the Keychain. Once Codex answered an ask with its account, the
        # email kept then (with no sign-in file there) says it is signed in, so it has a row and a number.
        from switchboard.providers import rows
        self.codex_numbers(40.0)
        self.keep_version(self.codex)
        self.keep_email('ana@made-up.example', login=False)
        b = self.board()
        codex = self.entry(b, 'codex')
        self.assertEqual((codex['signed_in'], codex['fit_why'], codex['email']), (True, None, 'ana@made-up.example'))
        self.assertEqual([r['provider'] for r in rows.from_board(b)], ['codex'])
        self.assert_nothing_started()

    def test_sign_in_not_seen_says_the_step(self):
        # Nothing says it is signed in, and nothing says it is not (the missing file alone never does): the line
        # under the list says how to find out, and how to sign in, in the adapter's own words.
        self.codex_numbers(40.0)
        self.keep_version(self.codex)
        b = self.board()
        codex = self.entry(b, 'codex')
        self.assertEqual((codex['signed_in'], codex['fit_why']), (None, 'sign-in not seen here'))
        self.assertEqual(view.under_list(b, self.now)[0], (
            'provider', 'Codex', 'sign-in not seen here: switchboard providers --ask, or run codex login, or open '
                                 'the ChatGPT app'))
        self.assertEqual(codex['fit_line'], view.under_list(b, self.now)[0][2])
        self.assert_nothing_started()

    def test_a_codex_with_only_refused_copies_has_no_row_and_says_the_copys_reason(self):
        # The only copy is in a folder other users can change: never run, so not "never read". No row, and the
        # line under the list says the copy's own reason.
        from switchboard.providers import rows
        self.codex_numbers(40.0)
        self.sign_in()
        os.chmod(self.bin, 0o777)
        self.addCleanup(os.chmod, self.bin, 0o755)
        b = self.board()
        codex = self.entry(b, 'codex')
        self.assertEqual(codex['fit_why'], 'no copy here can be run')
        self.assertEqual(rows.from_board(b), [])
        self.assertEqual(view.under_list(b, self.now)[0],
                         ('provider', 'Codex', 'no copy here can be run: its folder can be changed by other users'))
        self.assert_nothing_started()

    def test_carry_counts_only_a_provider_a_chat_can_go_to(self):
        # A provider the list gives no row (its fit_why) is nowhere to carry a chat on: no carry line under its own.
        self.codex_numbers(40.0)
        self.keep_version(self.codex)                                     # sign-in not seen here
        b = self.board()
        self.assertFalse(b['carry'])
        self.assertEqual([x[0] for x in view.under_list(b, self.now)], ['provider'])
        self.sign_in()
        self.keep_version(self.codex, '0.150.0')                          # older than the one that wrote its chats
        self.assertFalse(self.board()['carry'])
        self.keep_version(self.codex)
        self.assertTrue(self.board()['carry'])
        self.assert_nothing_started()

    def test_why_it_cannot_be_continued_to_in_the_lists_words(self):
        self.codex_numbers(40.0)                                          # written by 0.169.1
        self.keep_version(self.codex)
        self.assertEqual(self.entry(self.board(), 'codex')['fit_why'], 'sign-in not seen here')
        self.sign_in()
        self.keep_version(self.codex, '0.150.0')
        self.assertEqual(self.entry(self.board(), 'codex')['fit_why'], 'older than the one that wrote your chats')
        os.remove(self.codex)
        self.assertEqual(self.entry(self.board(), 'codex')['fit_why'], 'not on this Mac')
        self.assert_nothing_started()

    def test_a_copy_whose_version_was_never_read_is_fit_and_is_not_run(self):
        self.codex_numbers(40.0)
        self.sign_in()
        codex = self.entry(self.board(), 'codex')
        self.assert_nothing_started()
        self.assertEqual((codex['version'], codex['fit_why'], codex['email']), (None, None, None))

    def test_each_seat_says_its_own_room(self):
        self.keep_version(self.codex)
        self.codex_numbers(100.0)
        self.sign_in()
        (seat,) = self.entry(self.board(), 'codex')['seats']
        self.assertEqual(seat['state'], contract.OUT)
        self.assertAlmostEqual(seat['back_at'], int(self.now + 2 * DAY), delta=1)


class NotKnownAndNotFound(Board):
    def test_codex_with_no_readings(self):
        self.keep_version(self.codex)
        codex = self.entry(self.board(), 'codex')
        self.assertEqual((codex['state'], codex['why']), (contract.UNKNOWN, words.STATE_UNKNOWN))
        self.assertEqual(codex['seats'][0]['windows'], [])

    def test_codex_added_and_not_on_this_mac(self):
        os.remove(self.codex)
        b = self.board()
        codex = self.entry(b, 'codex')
        self.assertEqual((codex['state'], codex['why'], codex['installed']),
                         (contract.NOT_INSTALLED, words.STATE_NOT_INSTALLED, False))
        self.assertFalse(b['carry'])                    # nowhere to carry a chat on

    def test_damaged_files_never_raise_and_say_not_known(self):
        os.makedirs(os.path.join(core.data_dir(), contract.DATA_DIR), exist_ok=True)
        for rel in (contract.READINGS_FILE, contract.PROGRAMS_FILE):
            with open(os.path.join(core.data_dir(), rel), 'w') as f:
                f.write('{not json')
        path = self.codex_numbers(40.0)
        with open(path, 'w') as f:
            f.write('{"type": "event_msg", "payload": {"type": "token_count", "rate_limits": "torn')
        b = self.board()
        codex = self.entry(b, 'codex')
        self.assertEqual((codex['state'], codex['why'], codex['version']), (contract.UNKNOWN, words.STATE_UNKNOWN,
                                                                            None))
        self.assert_nothing_started()

    def test_a_damaged_settings_file_shows_the_first_provider_alone(self):
        with open(os.path.join(core.data_dir(), contract.SETTINGS_FILE), 'w') as f:
            f.write('[1, 2')
        b = self.board()
        self.assertEqual([p['id'] for p in b['providers']], ['claude'])

    def test_an_adapter_that_raises_is_not_known_and_the_others_show(self):
        class Broken(object):
            def __init__(self, real):
                self.__dict__.update(real.__dict__)
                self.id, self.name, self.vendor, self.can = real.id, real.name, real.vendor, real.can

            def detect(self):
                raise RuntimeError('made-up failure')
        b = self.board(codex_load=lambda real: Broken(real))
        codex = self.entry(b, 'codex')
        self.assertEqual((codex['state'], codex['why'], codex['error']),
                         (contract.UNKNOWN, words.STATE_UNKNOWN, 'RuntimeError'))
        self.assertEqual(self.entry(b, 'claude')['state'], contract.ROOM)

    def test_a_board_that_cannot_be_built_is_empty_and_says_so(self):
        kit = self.kit()
        bad = types.SimpleNamespace(**{n: getattr(kit.choices, n) for n in dir(kit.choices) if not n.startswith('_')})
        bad.order = lambda: 1 / 0
        b = view.view(kit=dataclasses.replace(kit, choices=bad))
        self.assertEqual((b['providers'], b['carry'], b['error']), ([], False, 'not known'))


class InWords(unittest.TestCase):
    NOW = 1795600000.0

    def p(self, **kw):
        base = {'id': 'codex', 'name': 'Codex', 'account_list': False, 'state': contract.ROOM, 'read_at': self.NOW - 60,
                'back_at': None, 'plan': 'ChatGPT Pro', 'count': 0, 'can_carry': True,
                'seats': [{'label': 'ChatGPT Pro', 'windows': [{'label': 'weekly', 'used_pct': 42.4,
                                                                'resets_at': self.NOW + 2 * DAY, 'line': 95.0}]}]}
        base.update(kw)
        return base

    def test_state_words(self):
        n = self.NOW
        self.assertEqual(view.state_words(self.p(), n), 'has room')
        self.assertEqual(view.state_words(self.p(read_at=n - 3 * 3600), n), 'had room when last read 3 hours ago')
        self.assertEqual(view.state_words(self.p(state=contract.OUT, back_at=n + 3600), n),
                         'is out until %s' % words.when(n + 3600, n))
        self.assertEqual(view.state_words(self.p(state=contract.OUT, account_list=True, count=5, seats=[],
                                                 back_at=n + 3600), n),
                         'every account is out until %s' % words.when(n + 3600, n))
        self.assertEqual(view.state_words(self.p(state=contract.OUT), n), words.STATE_OUT_NOT_KNOWN)
        self.assertEqual(view.state_words(self.p(state=contract.UNKNOWN), n), 'numbers not known')
        self.assertEqual(view.state_words(self.p(state=contract.SIGNED_OUT), n), 'not signed in')
        self.assertEqual(view.state_words(self.p(state=contract.NOT_INSTALLED), n), 'not on this Mac')

    def test_one_rule_for_how_fresh_room_is(self):
        # "has room" in words and a row's fresh both follow cli.WORDS_FRESH_S, the one constant.
        self.assertFalse(hasattr(view, 'FRESH_WORDS_S'))
        n, p = self.NOW, self.p(read_at=self.NOW - 1000)
        self.assertEqual(view.state_words(p, n), 'has room')
        with mock.patch.object(cli, 'WORDS_FRESH_S', 600.0):
            self.assertEqual(view.state_words(p, n), 'had room when last read 16 min ago')

    def test_summary(self):
        n = self.NOW
        self.assertEqual(view.summary(self.p(), n), 'has room · ChatGPT Pro · weekly 42%%, refills %s'
                         % words.when(n + 2 * DAY, n))
        out = self.p(state=contract.OUT, back_at=n + 2 * DAY)
        self.assertEqual(view.summary(out, n), 'is out until %s · ChatGPT Pro · weekly 42%%' % words.when(n + 2 * DAY, n))
        self.assertEqual(view.summary(self.p(account_list=True, plan=None, seats=[]), n),
                         'has room · the accounts above')

    def test_a_five_hour_window_with_no_number_has_no_words_as_on_the_account_rows(self):
        n = self.NOW
        week = {'label': 'weekly', 'used_pct': 42.4, 'resets_at': n + 2 * DAY, 'line': 95.0}
        refilled = {'label': '5-hour', 'used_pct': 30.0, 'resets_at': n - 60, 'line': 90.0}   # refilled since read
        p = self.p(seats=[{'label': 'ChatGPT Pro', 'windows': [refilled, week]}])
        self.assertEqual(view.summary(p, n), 'has room · ChatGPT Pro · weekly 42%%, refills %s'
                         % words.when(n + 2 * DAY, n))
        known = dict(refilled, resets_at=n + 3600)
        p = self.p(seats=[{'label': 'ChatGPT Pro', 'windows': [known, week]}])
        self.assertIn('5-hour 30%', view.summary(p, n))
        p = self.p(seats=[{'label': 'ChatGPT Pro', 'windows': [dict(week, used_pct=None)]}])
        self.assertIn('weekly not known', view.summary(p, n))           # the weekly window always says itself

    def test_lines(self):
        n = self.NOW
        claude = self.p(id='claude', name='Claude', account_list=True, plan=None, seats=[])
        self.assertEqual(view.lines({'providers': [claude], 'add': [], 'carry': False}, n), [])
        self.assertEqual(view.lines({'providers': [claude], 'carry': False,
                                     'add': [{'id': 'codex', 'name': 'Codex', 'app': 'Codex'}]}, n),
                         [('add', 'Codex', 'Codex is on this Mac too: switchboard add codex adds it.')])
        got = view.lines({'providers': [claude, self.p()], 'add': [], 'carry': True}, n)
        self.assertEqual([(kind, name) for kind, name, _ in got], [('provider', 'Claude'), ('provider', 'Codex'),
                                                                   ('carry', '')])
        self.assertEqual(got[-1][2], 'To carry a chat on in the next one with room, type in its folder: '
                                     'switchboard continue')
        self.assertEqual(view.lines(None, n), [])
        self.assertEqual(view.lines({'providers': 'damaged'}, n), [])
        self.assertEqual(view.lines({'providers': 7, 'add': 3, 'carry': False}, n), [])
        self.assertIsNone(view.out_note({'providers': 7}, n))

    def test_under_the_list_only_what_the_rows_do_not_say(self):
        n = self.NOW
        claude = self.p(id='claude', name='Claude', account_list=True, plan=None, seats=[], fit_why=None)
        fit = self.p(fit_why=None)
        unfit = self.p(fit_why='not signed in')
        offer = {'id': 'codex', 'name': 'Codex', 'app': 'Codex'}
        self.assertEqual(view.under_list({'providers': [claude, fit], 'add': [], 'carry': True}, n,
                                         rows=[{'provider': 'codex'}]), [])
        self.assertEqual(view.under_list({'providers': [claude, unfit], 'add': [], 'carry': False}, n),
                         [('provider', 'Codex', 'not signed in')])
        self.assertEqual(view.under_list({'providers': [claude], 'add': [offer], 'carry': False}, n),
                         [('add', 'Codex', 'Codex is on this Mac too: switchboard add codex adds it.')])
        carry = ('carry', '', 'To carry a chat on in the next one with room, type in its folder: switchboard continue')
        self.assertEqual(view.under_list({'providers': [claude, unfit], 'add': [], 'carry': True}, n),
                         [('provider', 'Codex', 'not signed in'), carry])
        self.assertEqual(view.under_list({'providers': [claude, unfit], 'add': [], 'carry': True}, n,
                                         rows=[{'provider': 'tern'}]), [('provider', 'Codex', 'not signed in')])
        for damaged in (None, 7, {'providers': 'damaged'}, {'providers': 7, 'add': 3, 'carry': False}):
            self.assertEqual(view.under_list(damaged, n), [])
        board = {'providers': [claude, unfit], 'add': [offer], 'carry': True}
        view.under_list(board, n)
        self.assertEqual(board, {'providers': [claude, unfit], 'add': [offer], 'carry': True})    # pure


if __name__ == '__main__':
    unittest.main()
