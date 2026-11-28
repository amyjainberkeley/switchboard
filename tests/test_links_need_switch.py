import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""A chat that needs another account's artifact gets that account, when nothing stands against it.

An item tool (Artifact, its comments, Claude Docs) said "not found", and the owner map names an owner that is not
the account Claude Code is on. While automatic switching is on, the hook asks for a need switch to that owner, at
most once in 10 minutes per owner, and the engine's gate decides (session_hooks._item_need_in_process): no switch
part-way, policy.need (room under every line on fresh numbers, the account's rules), and the waits of every optional
switch (policy._hold: a switch from any source in the last 45 minutes, the person's own included; an open obligation;
a very busy Mac), without the wait for Claude Code to be idle. The chat is told plainly what happened. A stand-in
answers for the engine: no real switch, no Keychain, no process of the engine is started here."""
import dataclasses
import fcntl
import io
import json
import os
import random
import shutil
import tempfile
import time
import types
import unittest
from unittest import mock

from fakes_autoswitch import account
from fakes_links import A, B, G, ID1, ID2, ID3, LINK, FakeChrome, Home
from test_autoswitch_policy import NOW as PNOW
from test_autoswitch_policy import reading
from test_hooks_session import FORBIDDEN, NOW, HookSandbox

from switchboard import artifacts, core, history, session_hooks
from switchboard.accounts import api, autoloop, context, keychain, policy, switcher, usage

H = 3600.0
MOVING = ('This artifact belongs to %s. Switchboard is moving Claude Code there; if it still exists, try again in a '
          'moment.' % A)


class Asks(object):
    """A stand-in for the engine's answer: records each ask, answers as told."""

    def __init__(self, went=None, why=None):
        self.calls, self.went, self.why = [], went, why

    def __call__(self, owner, account_id, why, budget):
        self.calls.append((owner, account_id, why, budget))
        return self.went, self.why


class ItemNeed(HookSandbox):
    def setUp(self):
        super().setUp()
        self.never = Asks()         # a stand-in that must not be asked: hook() swallows what a stand-in raises
        self.addCleanup(lambda: self.assertEqual(self.never.calls, [], 'a need switch was asked for'))

    def say(self, event, now=NOW, ask_item=None, env=None):
        out = io.StringIO()
        code = session_hooks.hook(stdin=io.BytesIO(json.dumps(event).encode()), env=env or {}, now=now, out=out,
                                  ask_item=ask_item)
        self.assertEqual(code, 0)
        text = out.getvalue().strip()
        if not text:
            return None
        said = json.loads(text)['hookSpecificOutput']['additionalContext']
        for word in FORBIDDEN:
            self.assertNotIn(word, said.lower())
        return said

    def not_found(self, aid=ID1, sid='s-1', tool='Artifact'):
        return self.event('PostToolUseFailure', sid=sid, tool_name=tool,
                          tool_input={'action': 'update', 'url': LINK % aid},
                          error='artifact not found — it may have been deleted, or it has not been shared with you')

    def mine(self, auto=True):
        return self.owners(active=B, auto=auto, artifacts_={ID1: {'o': A, 'p': 'P1', 's': 'seen'},
                                                            ID3: {'o': A, 'p': 'P1', 's': 'published'},
                                                            ID2: {'o': B, 'p': 'P2', 's': 'published'}})

    def asked(self):
        with open(session_hooks._item_need_file()) as f:
            return json.load(f)['owners']


class WhenItMoves(ItemNeed):
    def test_another_accounts_artifact_asks_for_its_owner_and_says_claude_code_is_moving(self):
        self.mine()
        ask = Asks(went=A)
        said = self.say(self.not_found(), ask_item=ask)
        self.assertTrue(said.startswith(MOVING), said)
        self.assertIn('do not run /login or /logout', said)
        self.assertNotIn('not deleted', said)           # it may have been: only its owner's login can tell
        self.assertEqual(ask.calls, [(A, 'acct-a', 'a chat asked for an artifact of %s' % A,
                                      session_hooks.ITEM_NEED_BUDGET)])
        self.assertEqual(self.asked()[A], {'at': NOW, 'went': True})
        # Another chat, a moment later, on another of A's pages: not asked again, told the same.
        said = self.say(self.not_found(ID3, sid='s-2'), now=NOW + 40, ask_item=self.never)
        self.assertTrue(said.startswith(MOVING), said)

    def test_a_claude_doc_says_so(self):
        self.mine()
        said = self.say(self.event('PostToolUseFailure', tool_name='mcp__claude_ai_Claude_Docs__read',
                                   tool_input={'container': {'kind': 'project', 'id': ID1}},
                                   error='Refused: this artifact is in another organization (other_org).'),
                        ask_item=Asks(went=A))
        self.assertTrue(said.startswith('This Claude Doc belongs to %s. Switchboard is moving Claude Code there' % A))

    def test_an_answer_marked_as_an_error_can_move_it(self):
        self.mine()
        ask = Asks(went=A)
        error = {'isError': True, 'content': [{'type': 'text', 'text': 'artifact not found'}]}
        said = self.say(self.event('PostToolUse', tool_name='Artifact',
                                   tool_input={'action': 'read', 'url': LINK % ID1}, tool_response=error), ask_item=ask)
        self.assertEqual(len(ask.calls), 1)
        self.assertTrue(said.startswith(MOVING), said)

    def test_a_link_in_a_comment_or_a_data_row_is_not_the_one_asked_for(self):
        self.mine()
        ask = Asks(went=A)
        said = self.say(self.event('PostToolUseFailure', tool_name='ArtifactComments',
                                   tool_input={'body': 'the old one: %s' % (LINK % ID1), 'url': LINK % ('u' * 20)},
                                   error='artifact not found'), ask_item=ask)
        self.assertEqual(ask.calls, [])                 # the call's own url names a page nobody owns in the map
        self.assertNotIn('Switchboard is moving', said)
        said = self.say(self.event('PostToolUseFailure', sid='s-2', tool_name='Artifact',
                                   tool_input={'action': 'copy', 'from_url': LINK % ID1,
                                               'title': 'see %s' % (LINK % ID2)},
                                   error='artifact not found'), ask_item=ask)
        self.assertEqual([c[0] for c in ask.calls], [A])     # from_url is the call's own


class OnlyFromAFailedCall(ItemNeed):
    def test_an_answer_that_only_reads_as_not_found_is_told_but_never_moves(self):
        self.mine()
        said = self.say(self.event('PostToolUse', tool_name='Artifact',
                                   tool_input={'action': 'read', 'url': LINK % ID1},
                                   tool_response='Page not found: the board we keep for the launch, kept here.'),
                        ask_item=self.never)
        self.assertIn('belongs to %s' % A, said)        # the words stay as they are
        self.assertNotIn('Switchboard is moving', said)
        self.assertFalse(os.path.exists(session_hooks._item_need_file()))

    def test_a_guessed_owner_or_one_with_no_source_never_moves_the_mac(self):
        for s in ('guess', None, 'odd'):
            self.owners(active=B, artifacts_={ID1: {'o': A, 'p': 'P1', 's': s}})
            said = self.say(self.not_found(sid='s-%s' % s), ask_item=self.never)
            self.assertIn('belongs to %s, and Claude Code is on %s now' % (A, B), said)     # the words of today
            self.assertNotIn('did not move', said)
            self.assertFalse(os.path.exists(session_hooks._item_need_file()))


class WhenItDoesNot(ItemNeed):
    def test_a_refusal_says_why_in_one_sentence_beside_the_run_command(self):
        self.mine()
        why = 'it waits: the last switch was 12 min ago; it stays at least 45 minutes, until 3:40 PM'
        said = self.say(self.not_found(), ask_item=Asks(why=why))
        self.assertIn('belongs to %s, and Claude Code is on %s now' % (A, B), said)
        self.assertIn('`switchboard run %s -- -p "<what to do with %s>"`' % (A, LINK % ID1), said)
        self.assertTrue(said.endswith(' Switchboard did not move Claude Code there: %s.' % why), said)
        self.assertEqual(self.asked()[A], {'at': NOW, 'went': False})

    def test_at_most_once_in_ten_minutes_per_owner(self):
        self.mine()
        self.say(self.not_found(), ask_item=Asks(why='B has no room (weekly 97% of its 95% line).'))
        said = self.say(self.not_found(ID3, sid='s-2'), now=NOW + 300, ask_item=self.never)
        self.assertTrue(said.endswith('Switchboard did not move Claude Code there: it asked 5 minutes ago and asks '
                                      'at most once in 10 minutes for one account.'), said)
        self.assertIn('`switchboard run %s' % A, said)
        again = Asks(went=A)
        said = self.say(self.not_found(ID3, sid='s-3'), now=NOW + session_hooks.ITEM_NEED_EVERY + 1, ask_item=again)
        self.assertEqual(len(again.calls), 1)
        self.assertTrue(said.startswith(MOVING))

    def test_no_answer_in_time_is_kept_as_unknown_never_as_did_not_move(self):
        self.mine()
        said = self.say(self.not_found(), ask_item=Asks())
        self.assertTrue(said.endswith('Switchboard asked to move Claude Code there and has no answer yet: if it moves, '
                                      'the next try works.'), said)
        self.assertEqual(self.asked()[A], {'at': NOW, 'went': None})
        said = self.say(self.not_found(ID3, sid='s-2'), now=NOW + 120, ask_item=self.never)
        self.assertTrue(said.endswith('Switchboard asked to move Claude Code there 2 minutes ago and has no answer '
                                      'yet; it asks at most once in 10 minutes for one account.'), said)
        self.assertNotIn('did not move', said)

    def test_no_answer_in_time_writes_nothing_more_so_the_hook_never_waits_on_the_switch(self):
        self.mine()
        noted = []
        self.patch(session_hooks, '_note_item_need', lambda *a: noted.append(a))
        self.say(self.not_found(), ask_item=Asks())
        self.assertEqual(noted, [])                     # it stays noted as not known; the switch may hold the lock
        later = NOW + session_hooks.ITEM_NEED_EVERY + 1
        self.say(self.not_found(ID3, sid='s-2'), now=later, ask_item=Asks(went=A))
        self.assertEqual(noted, [(A, later, True)])

    def test_moving_is_said_only_for_a_minute_after_the_move(self):
        self.mine()
        self.say(self.not_found(), ask_item=Asks(went=A))
        said = self.say(self.not_found(ID3, sid='s-2'), now=NOW + 90, ask_item=self.never)
        self.assertFalse(said.startswith(MOVING), said)         # it may have been moved away since
        self.assertTrue(said.endswith('Switchboard moved Claude Code there 1 minute ago and asks at most once in 10 '
                                      'minutes for one account.'), said)

    def test_automatic_switching_off_keeps_the_words_of_today(self):
        self.mine(auto=False)
        said = self.say(self.not_found(), ask_item=self.never)
        self.assertTrue(said.startswith('Switchboard: this artifact belongs to %s, and Claude Code is on %s now'
                                        % (A, B)), said)
        self.assertIn('`switchboard run %s -- -p' % A, said)
        self.assertNotIn('did not move', said)
        self.assertFalse(os.path.exists(session_hooks._item_need_file()))

    def test_never_for_a_chat_on_its_own_login_an_unknown_owner_or_the_account_in_use(self):
        self.mine()
        env = {'CLAUDE_CONFIG_DIR': os.path.join(self.tmp, 'own-profile')}
        self.assertIn('runs on its own login', self.say(self.not_found(), env=env, ask_item=self.never))
        said = self.say(self.not_found('u' * 20, sid='s-2'), ask_item=self.never)     # not in the map
        self.assertIn('`switchboard owner %s`' % (LINK % ('u' * 20)), said)
        self.assertIsNone(self.say(self.not_found(ID2, sid='s-3'), ask_item=self.never))     # B's own page, on B
        self.assertFalse(os.path.exists(session_hooks._item_need_file()))

    def test_said_once_per_link_per_move_so_asked_once_too(self):
        self.mine()
        ask = Asks(why='B has no room (weekly 97% of its 95% line).')
        self.say(self.not_found(), ask_item=ask)
        self.assertIsNone(self.say(self.not_found(), now=NOW + 900, ask_item=self.never))
        self.assertEqual(len(ask.calls), 1)

    def test_a_test_run_never_starts_the_engine_without_a_stand_in(self):
        self.mine()
        self.assertEqual(os.environ.get(keychain.TEST_RUN_VAR), '1')
        with mock.patch('subprocess.Popen', side_effect=AssertionError('no process from a hook in a test run')):
            said = self.say(self.not_found())
        self.assertNotIn('did not move', said)
        self.assertNotIn('is moving', said)


class OneMoveAtATime(ItemNeed):
    def two(self):
        return self.owners(active=B, artifacts_={ID1: {'o': A, 'p': 'P1', 's': 'seen'},
                                                 ID3: {'o': G, 'p': None, 's': 'published'}})

    def test_two_chats_needing_two_owners_never_move_it_back_to_back(self):
        self.two()
        self.assertTrue(self.say(self.not_found(), ask_item=Asks(went=A)).startswith(MOVING))
        said = self.say(self.not_found(ID3, sid='s-2'), now=NOW + 30, ask_item=self.never)
        self.assertTrue(said.endswith('Switchboard did not move Claude Code there: it asked to move it for another '
                                      'chat 1 minute ago and does that at most once in 10 minutes.'), said)
        again = Asks(why='G has no room (weekly 99% of its 95% line).')
        self.say(self.not_found(ID3, sid='s-3'), now=NOW + session_hooks.ITEM_NEED_EVERY + 1, ask_item=again)
        self.assertEqual([c[0] for c in again.calls], [G])

    def test_the_record_changes_only_under_its_lock(self):
        self.two()
        held = session_hooks._lock(session_hooks.ITEM_NEED_LOCK)
        self.assertIsNotNone(held)
        try:
            said = self.say(self.not_found(), ask_item=self.never)
        finally:
            session_hooks._unlock(held)
        self.assertTrue(said.endswith("Switchboard did not move Claude Code there: another chat's move is under way."),
                        said)
        self.assertFalse(os.path.exists(session_hooks._item_need_file()))


class ThroughTheRealOwnerMap(ItemNeed):
    def test_the_map_the_page_writes_lets_it_ask_only_while_automatic_switching_is_on(self):
        FakeChrome(self, {'P1': 'Work', 'P2': 'Home'}, logins={'P1': A, 'P2': B})
        self.state(active=B, **{A: {'profile': 'P1', 'profile_src': 'you'}, B: {'profile': 'P2', 'profile_src': 'you'}})
        self.page(ID1, owner=A, src='seen', profile='P1')
        self.page(ID3, owner=A, src='published')
        now = time.time()
        core.set_setting('auto', False)
        artifacts.write_owner_map()
        self.assertIs(artifacts.read_owner_map()['auto'], False)
        said = self.say(self.not_found(ID3), now=now, ask_item=self.never)
        self.assertIn('`switchboard run %s -- -p' % A, said)
        core.set_setting('auto', True)
        artifacts.write_owner_map()
        self.assertIs(artifacts.read_owner_map()['auto'], True)
        ask = Asks(went=A)
        said = self.say(self.not_found(sid='s-2'), now=now, ask_item=ask)
        self.assertEqual([c[0] for c in ask.calls], [A])
        self.assertTrue(said.startswith(MOVING), said)


class TheProcessAndItsCommand(unittest.TestCase):
    def test_the_command_runs_the_gate_isolated_with_no_secret(self):
        cmd = session_hooks.item_need_command('/opt/sb', A, 'acct-a', 'a chat asked for an artifact of %s' % A)
        self.assertEqual(cmd[1:5], ['-I', '-S', '-B', '-c'])
        self.assertIn('_item_need_in_process', cmd[5])
        self.assertEqual(json.loads(cmd[-1]), {'owner': A, 'id': 'acct-a', 'why': 'a chat asked for an artifact of %s'
                                                                                   % A})

    def test_its_answer_is_read_from_its_last_line(self):
        self.assertEqual(session_hooks._ask_item_need(A, 'acct-a', 'x', budget=30,
                                                      command=['/bin/echo', json.dumps({'went': A, 'why': None})]),
                         (A, None))
        no = json.dumps({'went': None, 'why': 'no room'})
        self.assertEqual(session_hooks._ask_item_need(A, 'acct-a', 'x', budget=30, command=['/bin/echo', no]),
                         (None, 'no room'))
        self.assertEqual(session_hooks._ask_item_need(A, 'acct-a', 'x', budget=30, command=['/bin/echo', 'junk']),
                         (None, None))
        self.assertEqual(session_hooks._ask_item_need(A, 'acct-a', 'x', budget=0.2, command=['/bin/sleep', '5']),
                         (None, None))
        self.assertEqual(session_hooks._ask_item_need(A, 'acct-a', 'x', command=['/nonexistent/program']),
                         (None, None))


# ---------- the gate, in the need switch's own process ----------

ACCTS = (account('a', 1), account('b', 2), account('c', 3))
OWNER = 'b@acct.example'


def snap(target=None, active='a', settings=None, **kw):
    base = dict(busy=False, idle_for_s=1800.0, since_switch_s=4 * H, optional_recent=0, load_per_core=0.5,
                battery_pct=90.0, on_ac=True)
    base.update(kw)
    readings = {'a': reading('a', five=50.0, week=50.0), 'b': target or reading('b', five=10.0, week=10.0)}
    return policy.Snapshot(now=PNOW, accounts=ACCTS, readings=readings,
                           settings=settings or policy.Settings(enabled=True), active=active, **base)


class GateCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.mkdtemp(prefix='switchboard-gate-')
        self.addCleanup(shutil.rmtree, folder, True)
        self.journal = os.path.join(folder, 'switch-journal.json')
        self.switched = []
        for target, name, value in ((context, 'current', lambda: object()),
                                    (switcher, 'journal_path', lambda ctx: self.journal),
                                    (api, 'request_need_switch', self.request),
                                    (api, 'find', lambda e: types.SimpleNamespace(id={OWNER: 'b'}[e]))):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def request(self, aid, why):
        self.switched.append((aid, why))
        return types.SimpleNamespace(ok=True)

    def patch_request(self, fn):
        p = mock.patch.object(api, 'request_need_switch', fn)
        p.start()
        self.addCleanup(p.stop)

    def gate(self, s, account_id='b'):
        return session_hooks._item_need_in_process(OWNER, account_id, 'a chat asked for an artifact of ' + OWNER,
                                                   gather=lambda: s)


class Gate(GateCase):
    def test_the_check_and_the_switch_hold_one_lock_and_the_snapshot_is_read_inside_it(self):
        seen = []

        def locked_now():
            fd = os.open(os.path.join(session_hooks.hooks_dir(), session_hooks.ITEM_NEED_LOCK), os.O_WRONLY)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return False
            except OSError:
                return True
            finally:
                os.close(fd)

        def gather():
            seen.append(('gather', locked_now()))
            return snap()
        self.patch_request(lambda aid, why: seen.append(('switch', locked_now())) or types.SimpleNamespace(ok=True))
        self.assertEqual(session_hooks._item_need_in_process(OWNER, 'b', 'x', gather=gather), (OWNER, None))
        self.assertEqual(seen, [('gather', True), ('switch', True)])
        self.assertFalse(locked_now())                  # let go once it has answered

    def test_another_chats_move_under_way_waits_its_turn_then_gives_up(self):
        held = session_hooks._lock(session_hooks.ITEM_NEED_LOCK)
        self.addCleanup(session_hooks._unlock, held)
        with mock.patch.object(session_hooks, 'ITEM_NEED_LOCK_WAIT', 0.05):
            self.assertEqual(self.gate(snap()), (None, "another chat's move is under way"))
        self.assertEqual(self.switched, [])

    def test_all_clear_it_moves_through_the_engine(self):
        self.assertEqual(self.gate(snap()), (OWNER, None))
        self.assertEqual(self.switched, [('b', 'a chat asked for an artifact of ' + OWNER)])

    def test_the_account_id_comes_from_the_engine_when_the_map_had_none(self):
        self.assertEqual(self.gate(snap(), account_id=None), (OWNER, None))
        self.assertEqual(self.switched[0][0], 'b')

    def test_never_while_a_switch_is_part_way(self):
        with open(self.journal, 'w') as f:
            f.write('{}')
        self.assertEqual(self.gate(snap()), (None, 'a switch is part-way'))
        self.assertEqual(self.switched, [])

    def test_never_against_a_persons_own_switch_of_minutes_ago(self):
        went, why = self.gate(snap(since_switch_s=12 * 60.0))
        self.assertIsNone(went)
        self.assertTrue(why.startswith('it waits: the last switch was 12 min ago; it stays at least 45 minutes'), why)
        self.assertEqual(self.switched, [])

    def test_never_while_another_chat_has_work_open_under_the_account_in_use(self):
        ob = {'kind': 'artifact', 'url': LINK % ID2, 'until': PNOW + 600, 'at': PNOW - 300, 'account_id': 'a'}
        went, why = self.gate(snap(obligations=(ob,)))
        self.assertIsNone(went)
        self.assertTrue(why.startswith('it waits: an artifact was published as'), why)
        self.assertEqual(self.switched, [])

    def test_only_with_room_under_every_line_and_automatic_switching_on(self):
        went, why = self.gate(snap(target=reading('b', five=10.0, week=93.0)))
        self.assertEqual((went, self.switched), (None, []))
        self.assertIn('has no room', why)
        went, why = self.gate(snap(settings=policy.Settings(enabled=False)))
        self.assertEqual((went, why, self.switched), (None, 'Automatic switching is off.', []))

    def test_the_chat_asking_is_mid_turn_so_the_idle_wait_is_left_out(self):
        self.assertEqual(self.gate(snap(busy=True, idle_for_s=5.0)), (OWNER, None))

    def test_an_engine_that_cannot_be_asked_is_said_plainly(self):
        def broken():
            raise OSError('no store')
        self.assertEqual(session_hooks._item_need_in_process(OWNER, 'b', 'x', gather=broken),
                         (None, "Switchboard's engine could not be asked"))


class APersonsOwnSwitchThroughTheRealGather(Home):
    """The 45-minute wait read the way the loop reads it: autoloop.gather over this test's data folder, with the
    switch in History. Only the engine's accounts, its live login, the load and the readings are stand-ins."""

    def setUp(self):
        super().setUp()
        core.set_setting('auto', True)
        self.switched = []
        now = time.time()
        rd = dict((aid, api.Reading(aid, (api.Window('5-hour', pct, now + 3 * H, now, 'endpoint'),
                                          api.Window('weekly', pct, now + 96 * H, now, 'endpoint')),
                                    now, 'endpoint', False, None)) for aid, pct in (('a', 50.0), ('b', 10.0)))
        for target, name, value in ((context, 'current', lambda: object()),
                                    (switcher, 'journal_path', lambda ctx: os.path.join(self.tmp, 'no-journal')),
                                    (api, 'request_need_switch',
                                     lambda aid, why: self.switched.append(aid) or types.SimpleNamespace(ok=True)),
                                    (usage, 'readings', lambda now=None, path=None: rd)):
            self.patch(target, name, value)
        live = api.Live(state=api.PRESENT, account_id='a', email='a@acct.example', proof='fingerprint')
        self.deps = autoloop.Deps(accounts=lambda: ACCTS, live=lambda: live, load=lambda: 0.5)

    def switch_line(self, ago):
        try:
            os.unlink(history.path())
        except FileNotFoundError:
            pass
        history.record('switch', 'Switched Claude Code to a@acct.example', t=time.time() - ago, to='a@acct.example',
                       account='a@acct.example', by='you')

    def gate(self):
        return session_hooks._item_need_in_process(OWNER, 'b', 'a chat asked for an artifact of ' + OWNER,
                                                   gather=lambda: autoloop.gather(deps=self.deps))

    def test_a_persons_switch_five_minutes_ago_holds_it_and_fifty_minutes_ago_does_not(self):
        self.switch_line(300)
        went, why = self.gate()
        self.assertIsNone(went)
        self.assertTrue(why.startswith('it waits: the last switch was 5 min ago; it stays at least 45 minutes'), why)
        self.assertEqual(self.switched, [])
        self.switch_line(50 * 60)
        self.assertEqual(self.gate(), (OWNER, None))
        self.assertEqual(self.switched, ['b'])


class GateNeverBreaksItsRules(GateCase):
    def test_many_made_up_cases(self):
        rnd = random.Random(4139)
        went_n = refused_n = 0
        for i in range(3000):
            journal = rnd.random() < 0.1
            if journal:
                with open(self.journal, 'w') as f:
                    f.write('{}')
            elif os.path.exists(self.journal):
                os.unlink(self.journal)
            rules = {'b': 'out'} if rnd.random() < 0.08 else {}
            settings = policy.Settings(enabled=rnd.random() > 0.1, rules=tuple(sorted(rules.items())))
            five, week = rnd.choice([None, rnd.uniform(0, 100)]), rnd.uniform(0, 100)
            age = rnd.choice([0.0, 300.0, 700.0, 1200.0])
            obs = ({'kind': rnd.choice(['artifact', 'doc', 'watch']), 'url': LINK % ID2, 'until': PNOW + 600,
                    'at': PNOW - 60, 'account_id': 'a'},) if rnd.random() < 0.15 else ()
            s = snap(target=reading('b', five=five, week=week, age=age), settings=settings,
                     since_switch_s=rnd.choice([None, 60.0, 30 * 60.0, 44 * 60.0, 46 * 60.0, 3 * H]),
                     obligations=obs, busy=rnd.random() < 0.5, idle_for_s=rnd.choice([None, 5.0, 900.0]),
                     load_per_core=rnd.choice([0.5, 3.0, 5.0]), optional_recent=rnd.choice([0, 1, 2]))
            before = len(self.switched)
            went, why = self.gate(s)
            moved = len(self.switched) > before
            self.assertEqual(bool(went), moved, (i, went, why))
            if not moved:
                refused_n += 1
                self.assertTrue(isinstance(why, str) and why, i)
                continue
            went_n += 1
            v = policy._view(s, ACCTS[1])
            calm = dataclasses.replace(s, busy=False, idle_for_s=None)
            self.assertFalse(journal, i)
            self.assertTrue(settings.enabled, i)
            self.assertNotEqual(rules.get('b'), 'out', i)
            self.assertTrue(s.since_switch_s is None or s.since_switch_s >= policy.DWELL, i)
            self.assertEqual(obs, (), i)
            self.assertTrue(v.known and v.room >= policy.LAND_MARGIN and v.age <= policy.OPTIONAL_MAX_AGE, i)
            self.assertLessEqual(s.load_per_core, policy.LOAD_HIGH, i)
            self.assertLess(s.optional_recent, policy.OPTIONAL_CAP, i)
            self.assertIsNone(policy._hold(calm, policy._view(calm, ACCTS[0]), v), i)
        self.assertGreater(went_n, 100)
        self.assertGreater(refused_n, 100)


if __name__ == '__main__':
    unittest.main()
