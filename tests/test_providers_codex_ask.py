import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Codex's numbers (DESIGN 8.2 and 8.3). Saved numbers come from the chat reader (a stand-in here) with no process and
# no network. Asking Codex now starts nothing until the person's one yes (ask_ok); then exactly the four lines of 8.3
# go to a stand-in fake-codex app server of each copied version, every answer fits that version's generated schema,
# the account's email is kept only to name Codex's row in the list (its ids never), a server request is answered
# -32601, and every way an ask can end is one of
# contract.ASK_OUTCOMES: an API-key account, a time-out, an overloaded server, a server that closes, a second ask while
# one is running, an ask too soon after the last, a back-off that doubles to an hour, and a Codex folder that is not
# the one expected.
import fcntl
import json
import os
import sys
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from fakes_codex import RESULTS, Rig, schema, schema_errors
from switchboard import core
from switchboard.providers import codex_facts, contract
from switchboard.providers.codex import CodexProvider
from switchboard.providers.contract import Limits, Window

EMAIL = 'maya@northwind.example'


def four_lines():
    return [
        '{"method":"initialize","id":1,"params":{"clientInfo":{"name":"switchboard","title":"Switchboard",'
        '"version":"%s"},"capabilities":{"experimentalApi":false,"requestAttestation":false,'
        '"optOutNotificationMethods":["item/agentMessage/delta","item/reasoning/summaryTextDelta",'
        '"item/reasoning/summaryPartAdded","item/reasoning/textDelta"]}}}' % core.VERSION,
        '{"method":"initialized"}',
        '{"method":"account/read","id":2,"params":{"refreshToken":false}}',
        '{"method":"account/rateLimits/read","id":3,"params":{"excludeResetCreditDetails":true}}',
    ]


def snapshot(used=42, minutes=10080, limit_id='codex', reached=None, resets=1800003600):
    return {'limitId': limit_id, 'limitName': None, 'planType': 'pro', 'rateLimitReachedType': reached,
            'primary': {'usedPercent': used, 'windowDurationMins': minutes, 'resetsAt': resets},
            'secondary': None, 'credits': None}


SAVED = Limits(provider='codex', seat='codex', windows=(Window('weekly', 30.0, 1800090000.0, 10080),),
               read_at=1799990000.0, source='file', plan='pro')


class Base(unittest.TestCase):
    version = '0.169.1'

    def setUp(self):
        self.rig = Rig(self, env={'CODEX_API_KEY': 'made-up-key-0123456789', 'OPENAI_API_KEY': 'made-up-key-9876'})
        self.fake = self.rig.app('ChatGPT.app', self.version)
        self.rig.chatfiles.saved = SAVED
        self.rig.choices.ok = True
        self.p = self.rig.provider

    def ask(self):
        out = self.p.limits(ask=True)
        self.assertEqual(len(out), 1)
        self.assertIn(out[0].ask, contract.ASK_OUTCOMES)
        if out[0].ask == contract.ASK_REFUSED:
            print('\n(the ask was refused: %s)' % out[0].error, file=sys.stderr)    # a busy Mac's time-out shows
        return out[0]

    def ask_state(self):
        with open(os.path.join(self.rig.data, contract.ASK_FILE)) as f:
            return json.load(f)

    def app_server_runs(self):
        return [r for r in self.fake.runs() if r == ['app-server']]


class NothingRunsBeforeTheYes(Base):

    def test_off_until_ask_ok(self):
        self.rig.choices.ok = False
        got = self.ask()
        self.assertEqual(got.ask, contract.ASK_OFF)
        self.assertEqual((got.source, got.plan, got.windows[0].used_pct), ('file', 'pro', 30.0))
        self.assertEqual(self.rig.calls.runs, [])
        self.assertEqual(self.rig.calls.pipes, [])
        self.assertEqual(self.fake.runs(), [])
        self.assertFalse(os.path.exists(os.path.join(self.rig.data, contract.ASK_FILE)))


class SavedNumbers(Base):

    def test_saved_numbers_are_the_chat_readers_with_their_lines(self):
        got = self.p.limits()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].windows, (Window('weekly', 30.0, 1800090000.0, 10080, 95.0),))
        self.assertIsNone(got[0].ask)
        self.assertIn(('saved_limits', self.rig.clock()), self.rig.chatfiles.calls)
        self.assertEqual(self.rig.calls.runs, [])
        self.assertEqual(self.rig.calls.pipes, [])

    def test_a_five_hour_window_gets_its_own_line(self):
        self.rig.chatfiles.saved = Limits(provider='codex', seat='codex', read_at=1.0, source='file',
                                          windows=(Window('5-hour', 10.0, None, 300), Window('3-day', 1.0, None, 4320)))
        self.assertEqual([w.line for w in self.p.limits()[0].windows], [90.0, 95.0])

    def test_no_saved_numbers(self):
        self.rig.chatfiles.saved = None
        got = self.p.limits()[0]
        self.assertEqual((got.windows, got.read_at, got.source), ((), None, 'none'))

    def test_seats(self):
        self.assertEqual(self.p.seats(), (contract.Seat('codex', 'codex', 'ChatGPT Pro', 'pro', True),))
        self.rig.chatfiles.saved = Limits('codex', 'codex', (), 1.0, 'file', plan='ent26')
        self.assertEqual(self.p.seats()[0].label, 'ChatGPT plan')
        self.rig.chatfiles.saved = None
        self.assertEqual(self.p.seats()[0].label, 'Codex')


class TheExchange(Base):

    def check_exchange(self, fake, version):
        self.assertEqual(fake.got(), four_lines())
        self.assertEqual(fake.violations(), [])
        by_id = {1: 'initialize', 2: 'account/read', 3: 'account/rateLimits/read'}
        sent = [json.loads(line) for line in fake.sent()]
        self.assertEqual([m.get('id') for m in sent], [1, 2, 3])
        for m in sent:
            self.assertEqual(schema_errors(schema(version, 'JSONRPCResponse'), m), [])
            self.assertEqual(schema_errors(schema(version, RESULTS[by_id[m['id']]]), m['result']), [])

    def test_the_four_lines_byte_for_byte_and_every_answer_in_the_schema(self):
        got = self.ask()
        self.assertEqual(got.ask, contract.ASK_DONE)
        self.assertEqual((got.source, got.plan, got.read_at, got.reached, got.error),
                         ('ask', 'pro', self.rig.clock(), None, None))
        self.assertEqual(got.windows, (Window('weekly', 42.0, 1800000000.0, 10080, 95.0),))
        self.check_exchange(self.fake, self.version)

    def test_argv_env_and_folder(self):
        self.ask()
        start = [r for r in self.fake.records() if r.get('argv') == ['app-server']][0]
        self.assertEqual(start['cwd'], self.rig.home)
        for name in ('CODEX_API_KEY', 'OPENAI_API_KEY'):
            self.assertNotIn(name, start['env'])
        self.assertIn('HOME', start['env'])
        self.assertEqual(self.fake.forbidden(), [])

    def test_the_email_is_kept_for_the_list_and_nothing_else(self):
        # The account's email names Codex's row in the one list, as a Claude account's email names its row: it is
        # kept in the readings file under 'codex' and nowhere else, and only account/read's answer may hold it. The
        # account's ids are still dropped as each answer is parsed, and nothing of the login is ever read.
        self.fake.spec(account={'account': {'type': 'chatgpt', 'email': EMAIL, 'planType': 'pro',
                                            'userId': 'user-made-up-01', 'accountId': 'acct-made-up-01'},
                                'requiresOpenaiAuth': True})
        answers = []
        p = self.p

        class Watching(CodexProvider):
            def _await(self, pipe, want, deadline, keep=()):
                got = super()._await(pipe, want, deadline, keep)
                answers.append((want, repr(got)))
                return got
        self.p = Watching(p.reach)
        self.p.facts, self.p.chatfiles, self.p.choices, self.p.locks = p.facts, p.chatfiles, p.choices, p.locks
        got = self.ask()
        self.assertEqual(got.ask, contract.ASK_DONE)
        self.assertEqual(len(answers), 3)
        self.assertIn("'planType': 'pro'", answers[1][1])
        for want, text in answers:
            for gone in ('user-made-up-01', 'acct-made-up-01'):
                self.assertNotIn(gone, text)
            if want != 2:
                self.assertNotIn(EMAIL, text)
        self.assertNotIn(EMAIL, repr(got))
        self.assertNotIn(EMAIL, repr(vars(self.p)))
        self.assertEqual(self.p.account_email(), EMAIL)
        readings = os.path.join(self.rig.data, contract.READINGS_FILE)
        for folder, _, names in os.walk(self.rig.data):
            for name in names:
                path = os.path.join(folder, name)
                with open(path, 'rb') as f:
                    held = EMAIL.encode() in f.read()
                self.assertEqual(held, path == readings, name)
        with open(readings) as f:
            self.assertEqual(json.load(f)['codex']['email'], EMAIL)

    def test_only_an_email_is_kept_as_one(self):
        for said in ('not an email', 'a@b', 'x\x1b[31m@made-up.example', 'x' * 250 + '@made-up.example', 42, None):
            with self.subTest(said=said):
                os.makedirs(os.path.join(self.rig.data, 'providers'), exist_ok=True)
                with open(os.path.join(self.rig.data, contract.ASK_FILE), 'w') as f:
                    json.dump({}, f)                # no pacing: each ask runs
                self.fake.spec(account={'account': {'type': 'chatgpt', 'email': said, 'planType': 'pro'},
                                        'requiresOpenaiAuth': True})
                self.assertEqual(self.ask().ask, contract.ASK_DONE)
                self.assertIsNone(self.p.account_email())

    def test_no_email_before_codex_was_asked(self):
        self.assertIsNone(self.p.account_email())
        self.assertEqual(self.p.seats(), (contract.Seat('codex', 'codex', 'ChatGPT Pro', 'pro', True),))


class AKeptEmailThatMayBeOld(Base):
    # A kept email is shown only while Codex's sign-in file is as it was when the email was read: its presence and its
    # change time, looked at with os.stat alone (the file is never opened). Otherwise the row is named "Codex" until the
    # next ask. An ask whose answer has no email keeps one that is still current.
    def login(self):
        return os.path.join(self.rig.home, '.codex', codex_facts.LOGIN_FILE)

    def sign_in(self, at):
        os.makedirs(os.path.dirname(self.login()), exist_ok=True)
        with open(self.login(), 'w') as f:
            f.write('{"made-up": true}')
        os.utime(self.login(), (at, at))

    def ask_with(self, email):
        os.makedirs(os.path.join(self.rig.data, 'providers'), exist_ok=True)
        with open(os.path.join(self.rig.data, contract.ASK_FILE), 'w') as f:
            json.dump({}, f)                    # no pacing: each ask runs
        account = {'type': 'chatgpt', 'planType': 'pro'}
        if email:
            account['email'] = email
        self.fake.spec(account={'account': account, 'requiresOpenaiAuth': True})
        self.assertEqual(self.ask().ask, contract.ASK_DONE)

    def test_shown_while_the_sign_in_file_is_as_it_was(self):
        self.sign_in(self.rig.clock() - 3600)
        self.ask_with(EMAIL)
        self.rig.clock.advance(86400 * 30)
        self.assertEqual(self.p.account_email(), EMAIL)

    def test_a_sign_in_file_changed_since_hides_it(self):
        self.sign_in(self.rig.clock() - 3600)
        self.ask_with(EMAIL)
        self.sign_in(self.rig.clock() + 60)     # Codex was signed in again (perhaps as another account)
        self.assertIsNone(self.p.account_email())
        self.rig.clock.advance(120)
        self.ask_with(EMAIL)                    # the next ask, after that, names it again
        self.assertEqual(self.p.account_email(), EMAIL)

    def test_a_sign_in_file_that_came_or_went_hides_it(self):
        self.ask_with(EMAIL)                    # no file: the sign-in was elsewhere (the Keychain)
        self.assertEqual(self.p.account_email(), EMAIL)
        self.sign_in(self.rig.clock() - 3600)   # a file now, even an older one: not as it was
        self.assertIsNone(self.p.account_email())
        self.ask_with(EMAIL)
        os.remove(self.login())                 # signed out
        self.assertIsNone(self.p.account_email())

    def test_an_ask_with_no_email_keeps_a_current_one_and_drops_an_old_one(self):
        self.sign_in(self.rig.clock() - 3600)
        self.ask_with(EMAIL)
        self.ask_with(None)
        self.assertEqual(self.p.account_email(), EMAIL)
        self.sign_in(self.rig.clock() + 60)
        self.ask_with(None)
        self.assertIsNone(self.p.account_email())
        self.sign_in(self.rig.clock() - 3600)   # even back to an older time, what was dropped stays dropped
        self.assertIsNone(self.p.account_email())

    def test_the_sign_in_file_is_never_opened(self):
        self.sign_in(self.rig.clock() - 3600)
        self.ask_with(EMAIL)
        opened = []
        real_open = open

        def watching(path, *a, **k):
            opened.append(str(path))
            return real_open(path, *a, **k)
        with mock.patch('builtins.open', watching):
            self.assertEqual(self.p.account_email(), EMAIL)
        self.assertFalse([p for p in opened if p.endswith(codex_facts.LOGIN_FILE)])

    def test_a_server_request_is_answered_unsupported(self):
        self.fake.spec(server_request_first=True, notify_first=True)
        got = self.ask()
        self.assertEqual(got.ask, contract.ASK_DONE)
        answers = [r['answer'] for r in self.fake.records() if 'answer' in r]
        self.assertEqual(answers, [{'id': 900, 'error': {'code': -32601, 'message': 'Unsupported server request'}}])
        self.assertEqual(self.fake.violations(), [])

    def test_lots_on_standard_error_first(self):
        self.fake.spec(stderr_bytes=1 << 20)
        self.assertEqual(self.ask().ask, contract.ASK_DONE)

    def test_the_sender_refuses_any_other_method(self):
        for method in ('thread/start', 'turn/start', 'account/logout', 'account/login/start',
                       'account/chatgptAuthTokens/refresh', 'account/rateLimitResetCredit/consume'):
            with self.assertRaises(ValueError):
                CodexProvider.message(method, 9, {})
        self.assertEqual(codex_facts.ASK_METHODS,
                         ('initialize', 'initialized', 'account/read', 'account/rateLimits/read'))


class TheOlderVersion(TheExchange):
    version = '0.155.1'


class HowAnAskEnds(Base):

    def test_an_api_key_account(self):
        self.fake.spec(account={'account': {'type': 'apiKey'}, 'requiresOpenaiAuth': True})
        got = self.ask()
        self.assertEqual((got.ask, got.error, got.windows), (contract.ASK_API_KEY, codex_facts.ASK_API_KEY, ()))
        self.assertEqual(len(self.fake.got()), 3)               # the limits were never asked for
        self.assertTrue(self.ask_state()['ok'])

    def test_not_signed_in(self):
        self.fake.spec(account={'account': None, 'requiresOpenaiAuth': True})
        got = self.ask()
        self.assertEqual((got.ask, got.error), (contract.ASK_REFUSED, codex_facts.ASK_SIGNED_OUT))
        self.assertEqual(got.windows[0].used_pct, 30.0)         # the saved numbers
        self.assertIsNone(self.ask_state()['backoff_until'])

    def test_a_time_out(self):
        self.rig.facts['ask_seconds'] = 1.5
        self.fake.spec(stall_at='account/rateLimits/read')
        t = time.monotonic()
        got = self.ask()
        self.assertLess(time.monotonic() - t, 6.0)
        self.assertEqual((got.ask, got.error, got.source), (contract.ASK_REFUSED, codex_facts.ASK_TIMED_OUT, 'file'))
        state = self.ask_state()
        self.assertEqual((state['ok'], state['backoff'], state['backoff_until']),
                         (False, 600.0, self.rig.clock() + 600.0))

    def test_overloaded(self):
        self.fake.spec(errors={'account/read': [-32001, 'Server overloaded; retry later.']})
        got = self.ask()
        self.assertEqual((got.ask, got.error), (contract.ASK_REFUSED, codex_facts.ASK_OVERLOADED))
        self.assertEqual(self.ask_state()['backoff'], 600.0)

    def test_it_closes_early(self):
        self.fake.spec(close_at='account/read')
        got = self.ask()
        self.assertEqual((got.ask, got.error), (contract.ASK_REFUSED, codex_facts.ASK_CLOSED))

    def test_another_codex_folder(self):
        self.fake.spec(codex_home='/Users/maya/another-codex')
        got = self.ask()
        self.assertEqual((got.ask, got.error), (contract.ASK_REFUSED, codex_facts.ASK_WRONG_HOME))
        self.assertEqual(len(self.fake.got()), 1)               # nothing after initialize
        self.assertFalse(self.ask_state()['ok'])

    def test_codex_home_from_codex_home(self):
        import dataclasses
        mine = os.path.join(self.rig.root, 'my-codex')
        self.rig.reach = dataclasses.replace(self.rig.reach, env=dict(self.rig.reach.env, CODEX_HOME=mine))
        p = self.rig.make()
        self.assertEqual(p.limits(ask=True)[0].ask, contract.ASK_DONE)
        start = [r for r in self.fake.records() if r.get('argv') == ['app-server']][0]
        self.assertIn('CODEX_HOME', start['env'])

    def test_too_old_runs_nothing(self):
        self.rig.chatfiles.newest = '0.170.0'
        with self.assertRaises(contract.ProviderError) as e:
            self.ask()
        self.assertEqual(e.exception.kind, 'too_old')
        self.assertEqual(self.app_server_runs(), [])

    def test_busy_while_another_ask_holds_the_lock(self):
        os.makedirs(self.rig.data, exist_ok=True)
        fd = os.open(os.path.join(self.rig.data, contract.ASK_LOCK_NAME), os.O_WRONLY | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        got = self.ask()
        self.assertEqual((got.ask, got.windows[0].used_pct), (contract.ASK_BUSY, 30.0))
        self.assertEqual(self.app_server_runs(), [])
        fcntl.flock(fd, fcntl.LOCK_UN)
        self.assertEqual(self.ask().ask, contract.ASK_DONE)


class Pace(Base):

    def test_300_seconds_after_an_ask(self):
        self.assertEqual(self.ask().ask, contract.ASK_DONE)
        self.rig.clock.advance(299)
        self.assertEqual(self.ask().ask, contract.ASK_PACED)
        self.assertEqual(len(self.app_server_runs()), 1)
        self.rig.clock.advance(2)
        self.assertEqual(self.ask().ask, contract.ASK_DONE)
        self.assertEqual(len(self.app_server_runs()), 2)

    def test_back_off_doubles_to_an_hour(self):
        self.fake.spec(errors={'initialize': [-32001, 'Server overloaded; retry later.']})
        waits = []
        for _ in range(6):
            self.assertEqual(self.ask().ask, contract.ASK_REFUSED)
            state = self.ask_state()
            waits.append(state['backoff'])
            self.rig.clock.advance(state['backoff'] - 1)
            self.assertEqual(self.ask().ask, contract.ASK_PACED)
            self.rig.clock.advance(1)
        self.assertEqual(waits, [600.0, 1200.0, 2400.0, 3600.0, 3600.0, 3600.0])
        self.assertEqual(len(self.app_server_runs()), 6)
        self.fake.spec(errors={})
        self.assertEqual(self.ask().ask, contract.ASK_DONE)
        self.assertEqual((self.ask_state()['backoff'], self.ask_state()['backoff_until']), (0, None))

    def test_the_ask_file_is_only_the_persons(self):
        self.ask()
        path = os.path.join(self.rig.data, contract.ASK_FILE)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(os.path.dirname(path)).st_mode & 0o777, 0o700)
        self.assertEqual(sorted(self.ask_state()), ['at', 'backoff', 'backoff_until', 'ok', 'v'])


class KeptReadings(Base):
    """What Codex said when it was asked is kept (contract.READINGS_FILE), so the next command without an ask shows
    it rather than the older numbers in Codex's own files; the newer of the two is shown."""

    def readings(self):
        with open(os.path.join(self.rig.data, contract.READINGS_FILE)) as f:
            return json.load(f)

    def asked(self, used=42, resets=1800090000):
        self.fake.spec(rate_limits={'rateLimits': snapshot(used, resets=resets)})
        got = self.ask()
        self.assertEqual(got.ask, contract.ASK_DONE)
        return got

    def test_after_an_ask_the_next_read_is_the_asked_one(self):
        path = os.path.join(self.rig.data, contract.READINGS_FILE)
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        with open(path, 'w') as f:
            json.dump({'v': 1, 'claude': {'made-up': 1}}, f)
        self.asked(42)
        self.rig.clock.advance(60)
        for p in (self.p, self.rig.make()):
            got = p.limits()[0]
            self.assertEqual((got.source, got.read_at, got.ask, got.plan), ('ask', 1800000000.0, None, 'pro'))
            self.assertEqual(got.windows, (Window('weekly', 42.0, 1800090000.0, 10080, 95.0),))
        self.assertEqual(self.readings()['claude'], {'made-up': 1})
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        # the email names Codex's row in the list (kept under 'codex' beside the numbers), with when it was read and
        # whether Codex's sign-in file was there then; nothing else of the account
        self.assertEqual(sorted(self.readings()['codex']),
                         ['email', 'email_at', 'login', 'plan', 'reached', 'read_at', 'seat', 'windows'])
        self.assertEqual(self.readings()['codex']['email'], EMAIL)
        self.assertEqual((self.readings()['codex']['email_at'], self.readings()['codex']['login']),
                         (1800000000.0, False))

    def test_its_seat_takes_the_plan_from_the_numbers_already_read(self):
        # One board build reads Codex's saved numbers once: the seat's plan comes from that same reading.
        self.rig.chatfiles.calls.clear()
        p = self.rig.make()
        lim = p.limits()[0]
        (seat,) = p.seats()
        self.assertEqual(seat.plan, lim.plan)
        self.assertEqual(len([c for c in self.rig.chatfiles.calls if c[0] == 'saved_limits']), 1)
        self.assertEqual(self.rig.make().seats()[0].plan, lim.plan)     # alone, it reads them itself

    def test_a_kept_email_says_codex_is_signed_in(self):
        # Only Codex's own answer to an ask can have kept an account's email: with its sign-in in the Keychain (no
        # sign-in file), that is the proof it is signed in. The missing file alone never says it is not.
        self.assertIsNone(self.p.signed_in())
        self.asked(42)
        self.assertIs(self.p.signed_in(), True)
        self.assertIs(self.rig.make().detect().signed_in, True)

    def test_a_newer_reading_in_codexs_own_files_wins(self):
        self.asked(42)
        self.rig.clock.advance(600)
        self.rig.chatfiles.saved = Limits(provider='codex', seat='codex', read_at=self.rig.clock() - 10,
                                          source='file', plan='pro',
                                          windows=(Window('weekly', 50.0, 1800090000.0, 10080),))
        got = self.p.limits()[0]
        self.assertEqual((got.source, got.windows[0].used_pct), ('file', 50.0))

    def test_a_kept_reading_whose_window_has_refilled_says_so(self):
        self.asked(97, resets=1800000060)
        self.rig.clock.advance(120)
        got = self.p.limits()[0]
        self.assertEqual(got.source, 'ask')
        self.assertEqual((got.windows[0].used_pct, got.windows[0].resets_at), (None, 1800000060.0))

    def test_a_kept_reached_word_is_dropped_once_its_window_has_refilled(self):
        self.fake.spec(rate_limits={'rateLimits': snapshot(100, reached='rate_limit_reached', resets=1800000060)})
        self.assertEqual(self.ask().reached, 'rate_limit_reached')
        self.rig.clock.advance(30)
        self.assertEqual(self.p.limits()[0].reached, 'rate_limit_reached')
        self.rig.clock.advance(90)
        got = self.p.limits()[0]
        self.assertEqual((got.source, got.reached, got.windows[0].used_pct), ('ask', None, None))

    def test_a_reading_older_than_seven_days_is_not_used(self):
        self.asked(42)
        self.rig.clock.advance(7 * 86400 + 1)
        self.assertEqual(self.p.limits()[0].source, 'file')

    def test_an_ask_that_did_not_end_done_keeps_nothing(self):
        self.fake.spec(errors={'initialize': [-32001, 'Server overloaded; retry later.']})
        self.assertEqual(self.ask().ask, contract.ASK_REFUSED)
        self.assertFalse(os.path.exists(os.path.join(self.rig.data, contract.READINGS_FILE)))
        self.assertEqual(self.p.limits()[0].source, 'file')


class TheLimitIdRule(Base):

    def test_codex_bucket_first(self):
        self.fake.spec(rate_limits={'rateLimits': snapshot(99, limit_id='other'),
                                    'rateLimitsByLimitId': {'codex': snapshot(10),
                                                            'other': snapshot(99, limit_id='other')},
                                    'ordinaryUsageAllowed': True})
        got = self.ask()
        self.assertEqual([w.used_pct for w in got.windows], [10.0])

    def test_the_single_view_when_it_is_codex_or_unnamed(self):
        for limit_id in ('codex', None):
            self.rig.clock.advance(400)
            self.fake.spec(rate_limits={'rateLimits': snapshot(17, minutes=300, limit_id=limit_id)})
            got = self.ask()
            self.assertEqual([(w.label, w.used_pct, w.line) for w in got.windows], [('5-hour', 17.0, 90.0)])

    def test_another_bucket_is_never_used_for_room(self):
        self.fake.spec(rate_limits={'rateLimits': snapshot(5, limit_id='other'), 'rateLimitsByLimitId': None})
        got = self.ask()
        self.assertEqual((got.ask, got.windows, got.error), (contract.ASK_DONE, (), codex_facts.ASK_NO_BUCKET))

    def test_reached(self):
        self.fake.spec(rate_limits={'rateLimits': snapshot(100, reached='rate_limit_reached')})
        self.assertEqual(self.ask().reached, 'rate_limit_reached')
        self.rig.clock.advance(400)
        self.fake.spec(rate_limits={'rateLimits': snapshot(50), 'ordinaryUsageAllowed': False})
        self.assertEqual(self.ask().reached, 'usage_not_allowed')


class Labels(unittest.TestCase):

    def test_window_names_from_their_length(self):
        cases = {300: '5-hour', 10080: 'weekly', 60: '1-hour', 4320: '3-day', 45: '45-minute', None: None, 0: None}
        for minutes, label in cases.items():
            self.assertEqual(codex_facts.label_of(minutes), label, minutes)


if __name__ == '__main__':
    unittest.main()
