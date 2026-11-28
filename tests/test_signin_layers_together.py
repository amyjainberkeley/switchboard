import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The layers that put Claude Code's sign-in back, working together on the account engine's stand-ins
# (fakes_accounts, fakes_autoswitch, signin_watch_fakes): the loop's own look and the engine's newer answers, a move
# that meets Claude Code's save lock, who calls the engine's look (the sign-in watch, the page), doctor's lines, one
# notification for each run of put-backs, and the words the sign-in watch's record keeps. Made-up *.example accounts;
# no notification is shown, and no Keychain, launchd or network is reached.
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from fakes_autoswitch import FakeClock, FakeEngine, FakeNet, FakeTokens, Home, account, make_loop, set_policy
from fakes_accounts import Rig, identity_block
from switchboard.accounts import api, autoloop, ccfiles, context, live, putback, roster, sessions, switcher, tokens

MCP = {'mcpOAuth': {'docs-server': {'accessToken': 'mcp-made-up-1', 'expiresAt': 1}}}
NOTIFY_PUT_BACK = autoloop.notify_put_back          # the loop's own, before a test stands it in


class RigBase(unittest.TestCase):
    """The real account engine on its stand-ins, Claude Code on B, and the automatic loop over it."""

    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='signin-layers-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.tmp, 'data')
        os.makedirs(os.environ['SWITCHBOARD_HOME'])
        sessions._reset_for_tests()
        self.clock = FakeClock(wall=time.time(), mono=time.monotonic())
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono, sleep=self.clock.sleep)
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        putback.reset()
        putback._KEPT.clear()
        roster.forget_live()
        self.rig.keep('uuid-a', 'ana@work.example', 'org-a')
        self.clock.advance(1.0)                 # two sign-ins never share an end stamp to the millisecond
        self.b_text = self.rig.keep('uuid-b', 'ana@home.example', 'org-b', siblings=MCP)   # Claude Code is on B
        self.told = []
        self.notes = []
        self.moved = []                         # the notification of a move that keeps Claude Code signed in
        patcher = mock.patch.object(autoloop, 'notify_put_back', self.told.append)   # a put-back's notification
        patcher.start()
        self.addCleanup(patcher.stop)
        deps = autoloop.Deps(net=FakeNet(self.clock), net_ready=lambda: True, load=lambda: 0.5,
                             power=lambda: (True, 100), procs=lambda: 1, disk_free=lambda: 10 ** 12,
                             history_note=lambda text, kind='info', **f: self.notes.append(text),
                             notify=self.told.append, notify_move=self.moved.append)
        self.loop = autoloop.Loop(deps=deps, clock=self.clock, rand=lambda: 0.5)

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        putback.reset()
        putback._KEPT.clear()
        roster.forget_live()
        self.rig.close()
        sessions._reset_for_tests()
        if self._old_home is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    @property
    def cred(self):
        return os.path.join(self.rig.home, '.claude', '.credentials.json')

    def fallback(self):
        """Claude Code's fallback after a refused save: its whole object to the plaintext file, then the item
        deleted."""
        text = self.rig.live_text()
        with open(self.cred, 'w') as f:
            f.write(text)
        os.chmod(self.cred, 0o600)
        self.rig.sec.drop(self.rig.service, self.rig.account)
        roster.forget_live()
        return text

    def lose_login(self):
        """What many processes saving at once can leave: the item deleted, then saved again without the login."""
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.rig.set_live(json.dumps(dict(MCP, discoveryState={'made-up': 1}), separators=(',', ':')))
        roster.forget_live()

    def has_login(self):
        text = self.rig.live_text()
        return bool(text) and live.parse(text).kind == 'oauth'

    def loop_log(self):
        try:
            with open(os.path.join(os.environ['SWITCHBOARD_HOME'], autoloop.LOG)) as f:
                return f.read()
        except OSError:
            return ''


class TheLimitsWithAndWithoutEvidence(RigBase):
    """Losses with no evidence of the fault follow PUT_BACK_WAITS and PUT_BACK_MAX; with evidence (a put-back undone
    within FAULT_SEEN_S, in an episode that saw Claude Code write its plaintext file) the episode limits decide. Either
    limit is said once, and said again when it is reached again after a later put-back lands."""

    def look(self, after):
        self.clock.advance(after)
        return api.put_back_pass()

    def until_not_waiting(self, step, most=2000):
        for _ in range(most):
            res = self.look(step)
            if res.action != 'wait':
                return res
        return res

    def limit_lines(self):
        return [e for e in self.rig.events if e[0] == putback.LIMIT_WORDS]

    def test_without_evidence_four_an_hour_and_the_limit_said_again_later(self):
        for _ in range(putback.PUT_BACK_MAX):
            self.clock.advance(31.0)                                   # each loss well after the last put-back
            self.lose_login()
            res = self.until_not_waiting(5.0)
            self.assertEqual(res.action, 'put_back', res.reason)
            self.assertFalse(res.seen['fault'])
        self.clock.advance(31.0)
        self.lose_login()
        res = self.until_not_waiting(5.0)
        self.assertEqual((res.action, res.line), ('limit', putback.LIMIT_WORDS))
        res = self.until_not_waiting(30.0)                             # silent until the first leaves the hour
        while res.action == 'limit':
            self.assertIsNone(res.line)
            res = self.until_not_waiting(30.0)
        self.assertEqual(res.action, 'put_back', res.reason)
        self.clock.advance(31.0)
        self.lose_login()
        res = self.until_not_waiting(5.0)
        self.assertEqual((res.action, res.line), ('limit', putback.LIMIT_WORDS))
        self.assertEqual(len(self.limit_lines()), 2)

    def test_with_evidence_the_episode_limit_and_said_again_later(self):
        self.lose_login()
        self.clock.advance(1.0)
        api.watch_look(look_hint(login_file='appeared'))               # Claude Code wrote its plaintext file: proof
        self.assertEqual(self.until_not_waiting(1.0).action, 'put_back')
        for n in range(putback.EPISODE_WRITES - 1):
            self.lose_login()                                          # undone at once: evidence
            res = self.until_not_waiting(putback.FAULT_AFTER_S)
            self.assertEqual(res.action, 'put_back', (n, res.reason))
            self.assertTrue(res.seen['fault'])
        self.lose_login()
        res = self.until_not_waiting(putback.FAULT_AFTER_S)
        self.assertEqual((res.action, res.line), ('limit', putback.LIMIT_WORDS))
        res = self.until_not_waiting(30.0)
        while res.action == 'limit':
            self.assertIsNone(res.line)
            res = self.until_not_waiting(30.0)
        self.assertEqual(res.action, 'put_back', res.reason)
        api.watch_look(look_hint(login_file='changed'))                # proof again, in the episode just opened
        for _ in range(putback.EPISODE_WRITES + 1):                    # a new episode, undone at once each time
            self.lose_login()
            res = self.until_not_waiting(putback.FAULT_AFTER_S)
            if res.action == 'limit':
                break
        self.assertEqual((res.action, res.line), ('limit', putback.LIMIT_WORDS))
        self.assertEqual(len(self.limit_lines()), 2)


class ThePromoteRightAfterASwitch(RigBase):
    """Settings that name the account a switch just left get no promote either: a session open during the switch may
    have written that name back, and the login in Claude Code's plaintext file may be that account's (SWITCHED_AWAY_S,
    as for a put-back). Unless the file's login is proven that account's newest: then Claude Code already works from it
    as that account, and the promote only keeps it."""

    def test_the_left_accounts_login_in_the_file_is_not_put_in_the_keychain_by_that_name(self):
        self.clock.advance(600)
        self.rig.set_live(self.rig.server.refresh_for(self.b_text))     # B renewed and kept: the file's is older
        self.assertIn('Saved the newer login', api.keep_pass())
        self.assertTrue(switcher.switch(self.ctx, 'uuid-a', source='auto', kind='forced').ok)
        self.rig.set_config(identity_block('uuid-b', 'ana@home.example', 'org-b'))    # the earlier name, written back
        with open(self.cred, 'w') as f:                                 # a process that still held B's login falls
            f.write(self.b_text)                                        # back to the file, and the item goes
        os.chmod(self.cred, 0o600)
        self.rig.sec.drop(self.rig.service, self.rig.account)
        roster.forget_live()
        writes = self.rig.kc.count('write', self.rig.service)
        hint = {'keychain': True, 'login_file': 'appeared', 'settings': False, 'tick': False}
        res = []
        for _ in range(10):
            self.clock.advance(1.0)
            res.append(api.watch_look(dict(hint)))
        self.assertEqual(set(r.action for r in res), {'leave'}, [r.reason for r in res])
        said = [r.line for r in res if r.line]
        self.assertEqual(len(said), 1, said)
        self.assertIn('ana@home.example', said[0])
        self.clock.advance(putback.SWITCHED_AWAY_S)                     # the same state, however long it lasts
        self.assertEqual(api.watch_look(dict(hint)).action, 'leave')
        self.assertEqual(self.rig.kc.count('write', self.rig.service), writes)
        self.assertTrue(os.path.exists(self.cred))



class TheLoopsStageAndTheEnginesNewAnswers(Home):
    """The loop's put-back stage with the engine's answers 'promote' and 'kept' (stand-in engine): neither is a
    failure or a loss; a promote is a write, so the pass reads the login again and looks often for a while, and it is
    not told (nothing was lost)."""

    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.engine = FakeEngine([account('a1', 1), account('a2', 2)], 'a1', self.clock)
        self.answer = None
        self.told = []

    def put_back(self):
        if self.answer.action == 'promote':
            self.engine.live_state = api.PRESENT                          # the login is in the Keychain again
        return self.answer

    def loop(self):
        return make_loop(self.engine, self.clock, FakeNet(self.clock), FakeTokens(), put_back=self.put_back,
                         notify=self.told.append)

    def test_a_promote_reads_the_login_again_looks_often_and_is_not_told(self):
        set_policy(enabled=True)
        loop = self.loop()
        self.engine.live_state = api.ABSENT
        self.answer = api.PutBack('promote', account='a1@acct.example', reason='its login sat only in its plaintext '
                                  'file', watch=True, at=self.clock.wall())
        before = self.engine.live_calls
        loop.run_pass()
        self.assertEqual(self.engine.live_calls - before, 2)               # read again in the same pass
        self.assertEqual(autoloop.load_state()['live']['id'], 'a1')
        self.assertEqual(self.told, [])
        self.assertFalse(loop.missing_watch)
        self.assertIsNotNone(loop._watch_end())                             # the next looks come every few seconds

    def test_kept_is_neither_a_failure_nor_a_loss(self):
        set_policy(enabled=False)
        loop = self.loop()
        self.engine.live_state = api.ABSENT
        self.answer = api.PutBack('kept', account='a1@acct.example', reason='Saved the newer login', watch=False)
        for _ in range(3):
            loop.run_pass()
            self.clock.advance(61.0)
        self.assertEqual(self.engine.switches, [])
        self.assertEqual(self.told, [])
        self.assertFalse(loop.missing_watch)
        self.assertNotIn('not looked at', self.loop_text())

    def loop_text(self):
        try:
            with open(os.path.join(os.environ['SWITCHBOARD_HOME'], autoloop.LOG)) as f:
                return f.read()
        except OSError:
            return ''


class ThePutBackPassAfterAPromote(RigBase):
    def test_it_forgets_the_cached_live_read_as_after_a_put_back(self):
        self.fallback()
        api.put_back_pass()                                                # the first look: never a promote
        self.clock.advance(putback.PUT_BACK_AFTER_S)
        self.assertEqual(api.live().account_id, 'uuid-b')                  # read once: the page's cache holds it
        with mock.patch.object(roster, 'forget_live', wraps=roster.forget_live) as forget:
            res = api.put_back_pass()
        self.assertEqual(res.action, putback.PROMOTE, res.reason)
        self.assertTrue(forget.called)
        self.assertTrue(self.has_login())



class TheMoveMeetsTheSaveLock(RigBase):
    """switcher.switch raises AccountsError('claude_busy') when Claude Code keeps its save lock past 9 s or saves the
    item at the same instant (nothing was written). The loop's move tries again at the next pass, says so once, and is
    not counted as made."""

    def empty_the_login(self):
        """Anthropic refused the renewal: Claude Code empties its login, so a forced move to A follows."""
        blob = json.loads(self.rig.live_text())
        blob['claudeAiOauth'].update({'accessToken': '', 'refreshToken': ''})
        self.rig.set_live(json.dumps(blob, separators=(',', ':')))
        roster.forget_live()

    def check(self, busy):
        set_policy(enabled=True)
        self.empty_the_login()
        with busy():
            first = self.loop.run_pass()
            self.clock.advance(61.0)
            second = self.loop.run_pass()
        self.assertEqual((first.decision.action, first.decision.target), ('switch', 'uuid-a'), first.decision.reason)
        self.assertIsNone(first.switched)
        self.assertIsNone(second.switched)
        self.assertEqual(self.loop_log().count('Claude Code was saving its login'), 1, self.loop_log())
        self.assertEqual(autoloop.load_state().get('switches') or [], [])
        self.assertEqual(live.parse(self.rig.live_text()).kind, 'wiped')        # nothing was written
        self.clock.advance(61.0)
        third = self.loop.run_pass()
        self.assertIsNotNone(third.switched)
        self.assertEqual(third.switched.to_id, 'uuid-a')
        self.assertEqual(len(autoloop.load_state()['switches']), 1)

    def test_a_save_lock_held_past_its_budget(self):
        def busy():
            return mock.patch.object(live, 'storage_lock', side_effect=ccfiles.LockBusy('held by Claude Code'))
        self.check(busy)

    def test_a_save_made_at_the_same_instant(self):
        class Busy(object):
            def __init__(self, rig):
                self.rig = rig

            def __enter__(self):
                self.rig.kc.rule('write', service=self.rig.service, why='collided', times=-1)

            def __exit__(self, *exc):
                del self.rig.kc.rules[:]
        self.check(lambda: Busy(self.rig))



def look_hint(**kw):
    h = {'keychain': False, 'login_file': None, 'settings': False, 'tick': False}
    h.update(kw)
    return h


class WhoCallsTheEnginesLook(RigBase):
    """The sign-in watch, built as its process builds it, and the page's own look both reach putback.watch_look with
    their hint, through the real api; the answer's next_s paces the watch, its seen reaches the watch's record, and
    its line is said where a put-back's line goes (History, once)."""

    def setUp(self):
        super().setUp()
        self.calls = []
        real = putback.watch_look

        def watch_look(ctx, hint=None):
            self.calls.append(dict(hint) if isinstance(hint, dict) else hint)
            return real(ctx, hint)
        p = mock.patch.object(putback, 'watch_look', watch_look)
        p.start()
        self.addCleanup(p.stop)

    def repairs(self):
        return [e for e in self.rig.events if e[2].get('h') == 'repair']

    def test_the_watch_looks_through_the_engines_watch_look(self):
        from switchboard.accounts import signin_watch as sw
        from signin_watch_fakes import Sim, make
        built = sw.build(ctx=self.ctx, clock=self.clock, launch=lambda *a: 127, port=7953)
        self.addCleanup(built.changes.close)
        self.assertIsNone(built.problems.get('look'))                     # the engine has a look
        self.assertIs(built.look, api.watch_look)
        self.lose_login()
        t0 = self.clock.mono()
        took = 0.3                                                          # each look takes this long
        w, sim, looks, beats, lines, logs = make(sim=Sim(clock=self.clock), answer=built.look, duration=lambda: took)
        w.run(until=t0 + 8.0)
        self.assertEqual(self.calls[0], dict(look_hint(tick=True), login_file_written=False))   # the first, at start
        self.assertEqual(self.calls[1], dict(look_hint(), login_file_written=False))    # the one its answer asked for
        made = [t - t0 for t, _ in looks.calls]
        # the next look comes next_s after the end of the one before: 4 s with no evidence, then 1 s after a write
        self.assertAlmostEqual(made[1] - made[0] - took, putback.PUT_BACK_AFTER_S, places=3)
        self.assertAlmostEqual(made[2] - made[1] - took, putback.AFTER_WRITE_NEXT_S, places=3)
        self.assertTrue(self.has_login())
        records = [json.loads(x) for x in lines]
        done = [r for r in records if r.get('kind') == 'look' and r.get('action') == 'put_back']
        self.assertEqual(len(done), 1, records)
        self.assertEqual(done[0]['seen']['action'], 'put_back')
        self.assertEqual(done[0]['seen']['live'], api.PRESENT)
        self.assertEqual(done[0]['seen']['login'], 'no_login')
        self.assertEqual([r for r in records if r.get('kind') == 'look' and r.get('action') == 'wait'][0]['seen']
                         ['action'], 'wait')
        self.assertEqual([e[0] for e in self.repairs()],
                         ['Claude Code lost its sign-in; Switchboard put ana@home.example back'])

    def test_the_pages_own_look_reaches_watch_look_with_a_regular_looks_hint(self):
        from switchboard import engine, server
        self.lose_login()
        fresh = dict(server.STANDIN, next=None, thread=None, on=False, late_said=False, looked_at=None)
        with mock.patch.dict(server.STANDIN, fresh), \
                mock.patch.object(engine, 'loop_wanted', lambda on: True), \
                mock.patch.object(engine, 'switching_on', lambda: False):
            for n in range(2):
                self.assertEqual(server.standin_tick(now=1000.0 + n * server.STANDIN_EVERY, wall=self.clock.wall()),
                                 'looked')
                server.STANDIN['thread'].join(30)
                self.clock.advance(putback.PUT_BACK_AFTER_S + 1)
            looked_at = server.STANDIN['looked_at']
        self.assertEqual(self.calls, [look_hint(tick=True)] * 2)
        self.assertIsNotNone(looked_at)
        self.assertTrue(self.has_login())
        self.assertEqual(len(self.repairs()), 1)



class DoctorsLines(RigBase):
    """doctor: one line for the last put-back or promote (the engine's health check, with how it was done), no second
    line for it in the sign-in watch's block, and a line for the setting that keeps Claude Code signed in."""

    def health_lines(self):
        from switchboard import engine
        return [t for _, t in engine.health_items(engine.health()) if 'sign-in' in t]

    def watch_block(self):
        from switchboard import __main__ as cli
        said = []
        with mock.patch.object(cli, 'page_state', lambda timeout=5: None), \
                mock.patch.object(cli.api, 'auto_status', lambda: {}):
            cli._doctor_signin_looks(lambda good, text, fix='': said.append(text), now=self.clock.wall())
        return said

    def test_a_put_back_and_a_promote_each_give_one_line_that_says_which(self):
        self.lose_login()
        api.put_back_pass()
        self.clock.advance(putback.PUT_BACK_AFTER_S + 1)
        self.assertEqual(api.put_back_pass().action, 'put_back')
        at = time.strftime('%H:%M on %a %b %d', time.localtime(self.clock.wall()))
        self.assertEqual(self.health_lines(), ["Put back Claude Code's lost sign-in: last at %s, for ana@home.example"
                                               % at])
        self.assertEqual([t for t in self.watch_block() if 'put back' in t.lower()], [])
        self.clock.advance(putback.FAULT_SEEN_S + 60)
        self.fallback()
        api.put_back_pass()
        self.clock.advance(putback.PUT_BACK_AFTER_S)
        self.assertEqual(api.put_back_pass().action, putback.PROMOTE)
        at = time.strftime('%H:%M on %a %b %d', time.localtime(self.clock.wall()))
        self.assertEqual(self.health_lines(), ["Put back Claude Code's lost sign-in: last at %s, for ana@home.example, "
                                               "from Claude Code's plain login file" % at])
        self.assertEqual(api.last_put_back()['how'], putback.PROMOTE)
        self.assertEqual([t for t in self.watch_block() if 'put back' in t.lower()], [])

    def test_the_setting_that_keeps_claude_code_signed_in(self):
        from switchboard import __main__ as cli
        for on, words in ((True, 'Keep Claude Code signed in: on (moves it to a signed-in account when its sign-in is '
                                 'lost or ends, also with automatic switching off)'),
                          (False, 'Keep Claude Code signed in: off')):
            autoloop.save_settings(stay_signed_in=on)
            said = []
            cli._doctor_auto(lambda good, text, fix='': said.append(text), 1)
            self.assertIn(words, said)



class TheTestGuardKnowsTheFilesOfTheLayers(unittest.TestCase):
    """tests/_isolate.py: a running Switchboard rewrites these in the real data folder by itself, so a test run on a
    Mac that runs one is not failed for them (this process is still refused any write there)."""

    def test_every_file_the_layers_write_in_the_data_folder_is_named(self):
        for name in ('put-back.json', 'read-request.json', 'signin-watch.json', 'signin-watch.lease',
                     'signin-watch.log', 'signin-watch.log.1', 'signin-watch.out', 'signin-record.jsonl',
                     'signin-record.jsonl.1', 'signin-hold.json'):
            self.assertIn(name, _isolate.LIVE_REWRITES, name)
        for prefix in ('put-back.json.', '.put-back.json.', 'signin-watch.json.', 'signin-watch.log.',
                       'signin-record.jsonl.', 'signin-hold.json.', '.signin-hold.json.'):
            self.assertIn(prefix, _isolate.LIVE_PREFIXES, prefix)



class OneNotificationPerEpisode(RigBase):
    """A put-back made by the sign-in watch, the loop and the page within one episode makes one notification, keyed
    by the episode's start, in words that promise nothing about open chats, and one History line (the episode's first
    put-back's)."""

    def test_the_watch_the_loop_and_the_page_make_one_notification(self):
        from switchboard import core, notify, server
        from switchboard.accounts import signin_watch as sw
        shown = []
        core.set_setting('notify', True)
        watch, problem = sw.default_look(api)
        page = server._standin_look()
        self.loop.d.notify = NOTIFY_PUT_BACK                               # the loop tells as it does for real
        self.assertIsNone(problem)
        landed = []
        with mock.patch.object(autoloop, 'notify_put_back', NOTIFY_PUT_BACK), \
                mock.patch.object(notify, '_show', lambda title, body, run: shown.append((title, body)) or 0):
            self.lose_login()
            watch(look_hint(login_file='appeared'))                        # the watch saw the fault: evidence
            self.clock.advance(putback.FAULT_AFTER_S)
            landed.append(watch(look_hint()))
            self.clock.advance(5.0)
            self.lose_login()                                              # undone again; the loop's pass finds it
            self.loop.run_pass()
            self.clock.advance(putback.FAULT_AFTER_S)
            self.loop.run_pass()
            landed.append(self.loop.look)
            self.clock.advance(5.0)
            self.lose_login()                                              # and once more; the page looks itself
            page()
            self.clock.advance(putback.FAULT_AFTER_S)
            landed.append(page())
        self.assertEqual([r.action for r in landed], ['put_back'] * 3, [r.reason for r in landed])
        self.assertEqual(len({r.at for r in landed}), 1)                   # one episode
        self.assertEqual(shown, [('Switchboard', 'Claude Code lost its sign-in. Switchboard put ana@home.example '
                                                 'back.')])
        self.assertEqual(len([e for e in self.rig.events if e[2].get('h') == 'repair']), 1)



class TheLoopLooksAtALoginOnlyInTheFile(RigBase):
    """With no sign-in watch, the loop's own pass finds a login Claude Code keeps only in its plaintext file and puts
    it back in the Keychain."""

    def test_the_live_read_says_where_the_login_came_from(self):
        self.assertFalse(api.live().from_file)
        self.fallback()
        got = api.live()
        self.assertEqual((got.state, got.account_id, got.from_file), (api.PRESENT, 'uuid-b', True))

    def test_two_passes_with_no_watch_promote_it(self):
        set_policy(enabled=False)
        text = self.fallback()
        self.loop.run_pass()                                               # the first look: never a promote
        self.clock.advance(putback.PUT_BACK_AFTER_S)
        self.loop.run_pass()
        self.assertEqual(self.rig.live_text(), text)                      # the file's own bytes, in the Keychain
        self.assertTrue(os.path.exists(self.cred))                         # left for now; a later pass takes it away
        self.assertEqual(autoloop.load_state()['live']['id'], 'uuid-b')
        self.assertFalse(api.live().from_file)
        self.assertEqual(self.told, [])                                    # nothing was lost: no notification



class TheWatchRecordsTheEnginesWords(RigBase):
    """Every word api.watch_look can answer, in `action` or in `seen`, is kept by the sign-in watch's record: none is
    turned into 'other' or dropped. A put-back, a promote and a kept login count as something done; 'leave' does not."""

    # every value the engine's look can put in each place, listed from the code: the actions of putback.look and
    # watch_look, the Read kinds, live.parse's kinds, the Keychain read's provenance, the saved copy's states in
    # putback.see
    ACTIONS = ('put_back', putback.PROMOTE, 'kept', 'wait', 'failed', 'limit', 'leave')
    LIVE = (api.PRESENT, api.ABSENT, api.UNREADABLE)
    LOGIN = (None, 'oauth', 'wiped', 'api_key', 'unparseable', 'no_login', 'empty', putback.SEALED)
    SOURCE = (None, 'keychain', 'file', 'cache')
    SAVED = (None, 'ok', 'missing', 'unreadable', 'incomplete')

    def test_each_word_the_engine_can_answer_is_kept(self):
        from switchboard.accounts import signin_watch as sw
        for action in self.ACTIONS:
            self.assertEqual(sw.word(action), action)
        n = 0
        for live_kind in self.LIVE:
            for login in self.LOGIN:
                for source in self.SOURCE:
                    for saved in self.SAVED:
                        for action in self.ACTIONS:
                            seen = {'live': live_kind, 'login': login, 'source': source, 'login_file': True,
                                    'named': False, 'saved': saved, 'action': action, 'fault': True,
                                    'live_fp': '0123456789abcdef', 'saved_fp': None}
                            self.assertEqual(sw.clean(seen), seen)
                            n += 1
        self.assertEqual(n, 3 * 8 * 4 * 5 * 7)

    def test_the_engines_real_answers_go_through_the_record_whole(self):
        from switchboard.accounts import signin_watch as sw
        lines = []
        record = sw.Record(clock=self.clock, sink=lines.append)
        hint = {'keychain': True, 'login_file': None, 'settings': False, 'tick': False}

        def look(after=1.0, **kw):
            self.clock.advance(after)
            return api.watch_look(dict(hint, **kw))
        answers = [look()]                                              # signed in: leave
        self.lose_login()
        answers.append(look())                                          # seen missing: wait
        self.rig.kc.rule('write', service=self.rig.service, why='denied', times=-1)
        answers.append(look(putback.PUT_BACK_AFTER_S + 1))              # the writes refused: failed
        del self.rig.kc.rules[:]
        answers.append(look(putback.PUT_BACK_WAITS[1] + 1))             # put back
        self.clock.advance(putback.AFTER_WRITE_S + putback.EPISODE_QUIET_S)
        self.rig.set_live(self.rig.server.refresh_for(self.b_text))     # Claude Code renewed: kept
        roster.forget_live()
        answers.append(look())
        self.fallback()
        look(login_file='appeared')                                     # the first look at it: wait
        answers.append(look(putback.FAULT_AFTER_S))                     # promote
        self.rig.set_live(json.dumps({'mcpOAuth': {}, 'keychainOverflow': {'slot': 'a', 'key': 'made-up'}}))
        roster.forget_live()
        answers.append(look())                                          # the pointer and no login: a loss, wait
        self.rig.set_live(json.dumps({'mcpOAuth': {}, 'keychainOverflow': {'slot': 'a', 'key': 'made-up',
                                                                          'login': '0123456789abcdef'}}))
        roster.forget_live()
        answers.append(look())                                          # a sealed login: leave
        self.assertEqual([a.action for a in answers],
                         ['leave', 'wait', 'failed', 'put_back', 'kept', putback.PROMOTE, 'wait', 'leave'],
                         [a.reason for a in answers])
        for res in answers:
            answer = sw._answer(res)
            self.assertEqual(answer[0], res.action)
            self.assertEqual(sw.did_something(answer), res.action != 'leave', res.action)
            record.add('look', hint=hint, action=sw.word(answer[0]), seen=answer[3])
            kept = json.loads(lines[-1])
            self.assertEqual(kept['action'], res.action)
            self.assertEqual(kept['seen'], res.seen, res.action)


if __name__ == '__main__':
    unittest.main()
