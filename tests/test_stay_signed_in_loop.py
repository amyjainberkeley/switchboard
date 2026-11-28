import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Keeping Claude Code signed in, through the automatic loop on its stand-ins (fakes_autoswitch) with a fake clock:
# (a) Claude Code loses its sign-in and the put-back can't bring it back: the pass moves it to the other account, with
#     one History line (the switch's own) and one notification, also with automatic switching off;
# (b) the sign-in in use is on its last access token: one move, between 10 and 5 minutes before it would stop;
# (c) the put-back is waiting, or has just put the login back: no move. Every answer of the put-back is the real
#     putback.decide's, so the words the loop reads are the ones it writes. Made-up *.example accounts; no
#     notification is shown and no Keychain is read.
import dataclasses
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import core  # noqa: E402
from switchboard.accounts import api, autoloop, policy, putback  # noqa: E402
from fakes_autoswitch import FakeClock, FakeEngine, FakeNet, FakeTokens, Home, T0, account, make_loop, \
    set_policy, usage_body  # noqa: E402

H, D = 3600.0, 86400.0
A, B = 'a1@acct.example', 'a2@acct.example'


def ok(five, week):
    return (200, {}, usage_body(five=five, week=week, five_reset=T0 + 4 * H, week_reset=T0 + 4 * D))


def seen(**kw):
    """What the put-back's look found when Claude Code lost its sign-in and its settings still name a1."""
    base = dict(live=api.ABSENT, config=api.PRESENT, named='a1', saved='ok')
    base.update(kw)
    return putback.Seen(**base)


def answer(s, done=(), before=None, now=T0):
    """The put-back's answer for a look, as look() hands it to the loop (its words are putback.decide's own)."""
    v = putback.decide(s, before, list(done), now)
    return api.PutBack(v.action, account=A, reason=v.reason, watch=v.watch)


class Engine(FakeEngine):
    """The stand-in account engine; a switch brings Claude Code's login back (it writes the target's login)."""

    def switch(self, target, kind='manual', source='cli', reason=''):
        res = super().switch(target, kind=kind, source=source, reason=reason)
        self.live_state = api.PRESENT
        return res


class Case(Home):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.engine = Engine([account('a1', 1, status='needs_login'), account('a2', 2)], 'a1', self.clock)
        self.net = FakeNet(self.clock)
        self.net.set('tok-a1', ok(10.0, 20.0))
        self.net.set('tok-a2', ok(10.0, 20.0))
        self.told = []
        self.looks = []
        self.answer = None
        set_policy(enabled=False)

    def put_back(self):
        self.looks.append(self.clock.wall())
        return self.answer

    def loop(self):
        loop = make_loop(self.engine, self.clock, self.net, FakeTokens(), put_back=self.put_back)
        loop.d.notify_move = self.told.append
        loop.d.signin_times = lambda aid, live=None: (None, None)
        return loop

    def lose(self, s=None):
        self.engine.live_state = api.ABSENT
        self.answer = answer(s or seen(refused=True))

    def log_text(self):
        try:
            with open(core.data_path(autoloop.LOG)) as f:
                return f.read()
        except OSError:
            return ''


class ALostSignIn(Case):
    def test_refused_saved_login_moves_it_to_the_other_account_once_and_says_so_once(self):
        self.lose()
        loop = self.loop()
        res = loop.run_pass()
        self.assertIsNotNone(res.switched)
        self.assertEqual([(s['to'], s['kind'], s['source']) for s in self.engine.switches], [('a2', 'forced', 'auto')])
        self.assertEqual(self.engine.switches[0]['reason'],
                         'a1@acct.example lost its sign-in and its saved login was refused by Anthropic. Switchboard '
                         'moved Claude Code to a2@acct.example, although automatic switching is off. Sign in to '
                         'a1@acct.example again when you like.')
        self.assertEqual(len(self.told), 1)
        note = self.told[0]
        self.assertEqual((note.key, note.title), ('switch:%s>%s' % (A, B), 'Switched to %s' % B))
        self.assertIn('although automatic switching is off', note.body)
        self.assertLessEqual(len(note.body), 240)
        self.assertEqual(loop.notes, [])                         # the switch's own History line is the one line
        for _ in range(3):
            self.clock.advance(60.0)
            loop.run_pass()
        self.assertEqual(len(self.engine.switches), 1)
        self.assertEqual(len(self.told), 1)
        st = autoloop.load_state()
        self.assertEqual(st[autoloop.STAY_LEFT], {'a1': 'f' * 16})
        self.assertIn(autoloop.STAY_KEY + 'a1', st['need'])
        self.assertEqual([(r['kind'], r['to']) for r in st['switches']], [('forced', 'a2')])
        self.assertNotIn('@', self.log_text())
        self.assertIn('moved Claude Code from account 1 to account 2 to keep it signed in (its sign-in was lost)',
                      self.log_text())

    def test_with_two_40_character_emails_the_notification_fits_and_keeps_both_whole(self):
        long_a, long_b = 'a' * 27 + '@work.example', 'b' * 27 + '@home.example'
        self.engine.accts = [account('a1', 1, status='needs_login', email=long_a), account('a2', 2, email=long_b)]
        self.engine.live_state = api.ABSENT
        v = putback.decide(seen(refused=True), None, [], T0)
        self.answer = api.PutBack(v.action, account=long_a, reason=v.reason, watch=v.watch)
        self.loop().run_pass()
        self.assertEqual([s['to'] for s in self.engine.switches], ['a2'])
        self.assertIn('%s lost its sign-in and its saved login was refused by Anthropic.' % long_a,
                      self.engine.switches[0]['reason'])                 # the history's sentence says it all
        body = self.told[0].body
        self.assertLessEqual(len(body), 240, body)
        self.assertEqual(body, 'Claude Code lost its sign-in. Switchboard moved Claude Code to %s, although automatic '
                               'switching is off. Sign in to %s again when you like.' % (long_b, long_a))

    def test_with_automatic_switching_on_it_moves_and_says_nothing_of_it(self):
        set_policy(enabled=True)
        self.lose()
        self.loop().run_pass()
        self.assertEqual(len(self.engine.switches), 1)
        self.assertNotIn('automatic switching is off', self.engine.switches[0]['reason'])

    def test_each_mark_of_the_saved_login_moves_it(self):
        marks = {'missing': dict(saved='missing'), 'incomplete': dict(saved='incomplete'),
                 'refused': dict(refused=True), 'ended': dict(ended=True), 'conflict': dict(conflict=True),
                 'unproven': dict(unproven=True), 'claimed': dict(claimed=True)}
        self.assertEqual(set(marks) | {'unreadable'}, set(autoloop.LOST_MARKS))
        for key, kw in marks.items():
            with self.subTest(key=key):
                self.tearDown()                                 # each mark in a fresh data folder
                self.setUp()
                self.lose(seen(**kw))
                self.loop().run_pass()
                self.assertEqual([s['to'] for s in self.engine.switches], ['a2'], key)
                self.assertIn('its saved login %s.' % dict(putback.MARKS)[key], self.engine.switches[0]['reason'])

    def test_the_put_back_limit_moves_it(self):
        self.lose()
        self.answer = answer(seen(), done=[T0 - 50, T0 - 40, T0 - 30, T0 - 20])
        self.assertEqual(self.answer.action, 'limit')
        self.loop().run_pass()
        self.assertEqual(len(self.engine.switches), 1)
        self.assertIn('its saved login was put back 4 times within an hour and was lost again each time',
                      self.engine.switches[0]['reason'])

    def test_a_saved_login_that_cant_be_read_moves_it_only_after_two_minutes(self):
        self.lose(seen(saved='unreadable'))
        loop = self.loop()
        loop.run_pass()
        self.clock.advance(60.0)
        loop.run_pass()
        self.clock.advance(59.0)
        loop.run_pass()
        self.assertEqual(self.engine.switches, [])
        self.clock.advance(2.0)
        loop.run_pass()
        self.assertEqual([s['to'] for s in self.engine.switches], ['a2'])
        self.assertIn("its saved login can't be read from the Keychain right now", self.engine.switches[0]['reason'])

    def test_an_unreadable_saved_login_that_reads_again_starts_the_two_minutes_over(self):
        self.lose(seen(saved='unreadable'))
        loop = self.loop()
        loop.run_pass()
        self.clock.advance(100.0)
        self.answer = answer(seen())                           # read fine: a wait
        loop.run_pass()
        self.answer = answer(seen(saved='unreadable'))
        self.clock.advance(60.0)
        loop.run_pass()
        self.assertEqual(self.engine.switches, [])

    def test_the_setting_off_nothing_moves(self):
        autoloop.save_settings(stay_signed_in=False)
        self.lose()
        loop = self.loop()
        loop.run_pass()
        self.clock.advance(300.0)
        loop.run_pass()
        self.assertEqual(self.engine.switches, [])
        self.assertEqual(self.told, [])


class AnswersThatNeverMoveIt(Case):
    """The put-back, or the person, comes first. Automatic switching is on, so any move would show."""

    def setUp(self):
        super().setUp()
        set_policy(enabled=True)

    def check(self, res):
        self.engine.live_state = api.ABSENT
        self.answer = res
        loop = self.loop()
        for _ in range(4):
            loop.run_pass()
            self.clock.advance(61.0)
        self.assertEqual(self.engine.switches, [], res)
        self.assertEqual(self.told, [])

    def test_waiting_to_put_it_back(self):
        self.check(answer(seen()))
        self.assertEqual(answer(seen()).action, 'wait')

    def test_a_put_back_that_failed(self):
        self.check(api.PutBack('failed', account=A, reason='Keychain: busy', watch=True))

    def test_a_switch_part_way_a_sign_in_under_way_the_settings_naming_nobody(self):
        for s in (seen(part_way=True), seen(signing_in=True), seen(named=None)):
            with self.subTest(s=s):
                self.tearDown()
                self.setUp()
                self.check(answer(s))

    def test_a_live_login_that_cant_be_read_a_renewal_and_a_login_file(self):
        for s in (seen(live=api.UNREADABLE), seen(degraded=True, live=api.PRESENT, login='no_login'),
                  seen(renewing=True), seen(file_login=True), seen(config=api.UNREADABLE)):
            with self.subTest(s=s):
                self.tearDown()
                self.setUp()
                self.check(answer(s))

    def test_just_put_back(self):
        def put_back():
            self.engine.live_state = api.PRESENT
            return api.PutBack('put_back', account=A, reason='put back', watch=True, at=self.clock.wall())
        self.engine.accts[0] = account('a1', 1)
        self.engine.live_state = api.ABSENT
        loop = self.loop()
        loop.d.put_back = put_back
        loop.run_pass()
        self.assertEqual(self.engine.switches, [])


class ASignInThatEnds(Case):
    """a1's sign-in ends 50 minutes from now, before its access token's renewal moment an hour from now."""

    def setUp(self):
        super().setUp()
        self.engine.accts = [account('a1', 1), account('a2', 2)]
        self.expires, self.end = T0 + H, T0 + 50 * 60
        self.stop = self.expires - 300

    def times(self, aid, live=None):
        return (self.expires, self.end) if aid == 'a1' else (T0 + 8 * H, T0 + 20 * D)

    def run_until(self, until, loop):
        held = []
        while self.clock.wall() <= until:
            loop.run_pass()
            held.append((self.clock.wall(), self.engine.active))
            self.clock.advance(60.0)
        return held

    def test_one_move_between_ten_and_five_minutes_before_it_stops_and_never_on_a_stopped_login(self):
        loop = self.loop()
        loop.d.signin_times = self.times
        held = self.run_until(self.stop + 600, loop)
        self.assertEqual(len(self.engine.switches), 1)
        at = self.engine.switches[0]['at']
        self.assertTrue(self.stop - 600 <= at <= self.stop - 300, (at - self.stop))
        self.assertTrue(all(who == 'a2' for t, who in held if t >= self.stop))
        self.assertTrue(self.engine.switches[0]['reason'].startswith("a1@acct.example's sign-in ends in 10 minutes. "
                                                                     "Switchboard moved Claude Code to a2"))
        self.assertNotIn('keep working', self.engine.switches[0]['reason'])
        self.assertEqual(len(self.told), 1)
        self.assertNotIn(autoloop.STAY_LEFT, {k: v for k, v in autoloop.load_state().items() if v})

    def test_it_renews_as_usual_when_the_end_is_far(self):
        self.end = T0 + 20 * D
        loop = self.loop()
        loop.d.signin_times = self.times
        self.run_until(self.stop + 600, loop)
        self.assertEqual(self.engine.switches, [])

    def test_the_other_account_ending_within_the_hour_too_it_stays(self):
        self.engine.accts = [account('a1', 1), dataclasses.replace(account('a2', 2), refresh_expires_at=T0 + H)]
        loop = self.loop()
        loop.d.signin_times = self.times
        res = None
        while self.clock.wall() <= self.stop:
            res = loop.run_pass()
            self.clock.advance(60.0)
        self.assertEqual(self.engine.switches, [])
        self.assertIn("a2@acct.example's sign-in ends in", res.decision.reason)


class TheOneNotification(Case):
    """The page's own "Switched to" note for the same switch (server.tell) and the loop's note of the move are one
    notification with the move's short body, whichever is sent first and however late the page notices the switch.
    Every note goes through notify.send into one notify.json; nothing is shown (both runs are stand-ins)."""

    LINES = {'weekly_line': 95.0, 'session_line': 90.0, 'landing_room': 5.0}
    BODY = ('Claude Code lost its sign-in. Switchboard moved Claude Code to a2@acct.example, although automatic '
            'switching is off. Sign in to a1@acct.example again when you like.')

    def setUp(self):
        super().setUp()
        from switchboard import server
        self.server = server
        self.shown = []
        for target, name, value in ((server, 'NOTIFY_RUN', self.osascript), (core, 'DEMO', False)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('SWITCHBOARD_NOTIFY_STATE', None)
        core.set_setting('desktop_note_said', True)      # (the first switch's once-only line is another test's)

    def osascript(self, cmd):
        self.shown.append((cmd[-2], cmd[-1]))           # (title, body), as osascript would have been given them
        return 0

    def loop(self):
        from switchboard import notify
        loop = super().loop()
        loop.d.notify_move = lambda note: notify.send(note, run=self.osascript, now=self.clock.wall())
        return loop

    def page_tells(self, at=0.0):
        """The page's tick noticing the switch from a1 to a2 (server.tell between its two views)."""
        before = {'active': A, 'accounts': {A: {}, B: {}}}
        self.server.tell(before, dict(before, active=B), self.LINES, self.clock.wall() + at)

    def test_the_loop_first_then_the_page(self):
        self.lose()
        self.loop().run_pass()
        self.assertEqual(self.shown, [('Switched to %s' % B, self.BODY)])
        self.page_tells(at=5.0)
        self.page_tells(at=200.0)                       # a busy page reads every 4 minutes: still not a second one
        self.assertEqual(self.shown, [('Switched to %s' % B, self.BODY)])

    def test_the_page_first_before_the_switch_is_in_the_history(self):
        test = self

        class Noticed(Engine):
            def switch(self, target, kind='manual', source='cli', reason=''):
                res = super().switch(target, kind=kind, source=source, reason=reason)
                test.page_tells()                      # the page's tick lands right after the switch's write
                return res
        self.engine = Noticed(self.engine.accts, 'a1', self.clock)
        self.lose()
        self.loop().run_pass()
        self.assertEqual(len(self.engine.switches), 1)
        self.assertEqual(self.shown, [('Switched to %s' % B, self.BODY)])

    def test_the_page_first_with_the_switch_in_the_history(self):
        from switchboard import history
        test = self

        class Recorded(Engine):
            def switch(self, target, kind='manual', source='cli', reason=''):
                res = super().switch(target, kind=kind, source=source, reason=reason)
                history.record('switch', 'Switched Claude Code to %s' % B, t=test.clock.wall(), to=B, account=B,
                               by='auto', reason=reason)
                test.page_tells()
                return res
        self.engine = Recorded(self.engine.accts, 'a1', self.clock)
        self.lose()
        self.loop().run_pass()
        self.assertEqual(self.shown, [('Switched to %s' % B, self.BODY)])

    def test_a_switch_someone_else_made_or_one_long_after_keeps_the_pages_own_words(self):
        from switchboard import history
        self.lose()
        self.loop().run_pass()
        self.shown.clear()
        self.clock.advance(autoloop.MOVE_NOTE_FRESH + 1)
        self.page_tells(at=0.0)                          # long after: the page's own words
        self.assertEqual(self.shown, [('Switched to %s' % B, 'From %s.' % A)])
        self.tearDown()
        self.setUp()
        self.lose()
        self.loop().run_pass()
        self.shown.clear()
        history.record('switch', 'Switched Claude Code to %s' % B, t=self.clock.wall() + 30, to=B, account=B,
                       by='you')                       # a terminal's switch after the move
        self.page_tells(at=90.0)
        self.assertEqual(self.shown, [('Switched to %s' % B, 'From %s.' % A)])

    def test_a_move_that_did_not_go_through_leaves_no_words_for_the_page(self):
        self.engine.fail_switch = 'the Keychain was slow'
        self.lose()
        self.loop().run_pass()
        self.assertEqual(self.engine.switches, [])
        self.assertIsNone(autoloop.move_body(A, B, self.clock.wall()))
        self.page_tells()
        self.assertEqual(self.shown, [('Switched to %s' % B, 'From %s.' % A)])


class ASwitchThatWaits(Case):
    """A switch that finds a login being saved (AccountsError 'claude_busy': nothing was written) is tried again at the
    next pass; the loop's log says so once, in its own words, and never tells its reader to try again."""

    def waits(self, words):
        class Busy(Engine):
            def switch(self, target, kind='manual', source='cli', reason=''):
                raise api.AccountsError('claude_busy', words % {'email': B})
        self.engine = Busy(self.engine.accts, 'a1', self.clock)
        self.lose()
        loop = self.loop()
        for _ in range(3):
            loop.run_pass()
            self.clock.advance(61.0)
        self.assertEqual(self.told, [])
        return [x.split(' ', 2)[-1] for x in self.log_text().splitlines() if 'switch waits' in x]

    def test_claude_code_saving_its_login(self):
        from switchboard.accounts import switcher
        for words in (switcher.SAVING_WORDS,
                      'Claude Code is renewing its login right now; nothing was changed. Try again in a few seconds.',
                      'Claude Code took its login lock back (the Mac may have slept); nothing was changed. Try again.'):
            with self.subTest(words=words):
                self.tearDown()
                self.setUp()
                self.assertEqual(self.waits(words), ['switch waits: Claude Code was saving its login; nothing was '
                                                     'changed. The next pass tries again.'])

    def test_the_saved_login_being_renewed(self):
        self.assertEqual(self.waits("%(email)s's saved login is being renewed right now; try again in a few seconds. "
                                    'Nothing was changed.'),
                         ['switch waits: the saved login it was switching to was being renewed; nothing was changed. '
                          'The next pass tries again.'])


class TheSettingIsStored(Home):
    def test_beside_automatic_switching_a_missing_key_means_on(self):
        self.assertTrue(autoloop.load_settings().stay_signed_in)
        autoloop.save_settings(stay_signed_in=False)
        self.assertFalse(autoloop.load_settings().stay_signed_in)
        self.assertFalse(core.settings()[autoloop.POLICY_KEY]['stay_signed_in'])
        autoloop.save_settings(enabled=True, weekly_at=90.0)
        self.assertFalse(autoloop.load_settings().stay_signed_in)          # another change keeps it
        autoloop.save_settings(stay_signed_in=True)
        self.assertTrue(autoloop.load_settings().stay_signed_in)

    def test_only_on_or_off(self):
        with self.assertRaises(api.AccountsError):
            autoloop.save_settings(stay_signed_in='off')

    def test_the_page_settings_dict_is_unchanged(self):
        self.assertEqual(set(autoloop.settings_dict()),
                         {'enabled', 'session_at', 'weekly_at', 'model_at', 'model', 'strategy'})


class TheTimesComeFromTheAccountList(Home):
    def test_the_kept_login_of_the_account_and_no_keychain_read(self):
        from switchboard.accounts import context, vault
        ctx = context.current()
        doc = vault.blank()
        doc['accounts']['a1'] = {'id': 'a1', 'uuid': 'a1', 'email': A, 'expires_ms': int(T0 * 1000) + 3600000,
                                 'rx_ms': int(T0 * 1000) + 3000000}       # every list Switchboard writes has 'uuid'
        doc['order'] = ['a1']
        with vault.account_lock(ctx, timeout=5.0):
            vault.save(ctx, doc)
        self.assertEqual(autoloop.signin_times('a1'), (T0 + 3600, T0 + 3000))
        self.assertEqual(autoloop.signin_times('zz'), (None, None))
        self.assertEqual(policy.ends_at(*autoloop.signin_times('a1')), T0 + 3300)


class TheAnswersKeyAndAccountId(Case):
    """An answer that carries the put-back's key and the account's id (api.PutBack.key, .account_id) is read by them:
    the saved login's marks and the put-back's limit (with or without evidence of the fault) move Claude Code, no other
    key or action does. The words are read only for an older answer without either (the cases above)."""

    def keyed(self, action, key, aid='a1', seen=None):
        return api.PutBack(action, account=A, reason='made-up words the loop never matches', account_id=aid, key=key,
                           seen=seen)

    def moves(self, res):
        self.engine.live_state = api.ABSENT
        self.answer = res
        self.loop().run_pass()
        return [s['to'] for s in self.engine.switches]

    def test_each_mark_of_the_saved_login_and_the_limit_move_it(self):
        for action, key in [('leave', k) for k in autoloop.LOST_MARKS if k != 'unreadable'] + [('limit', 'limit')]:
            with self.subTest(key=key):
                self.tearDown()
                self.setUp()
                self.assertEqual(self.moves(self.keyed(action, key)), ['a2'], key)
                if key == 'limit':
                    self.assertIn('was put back %d times within an hour' % putback.PUT_BACK_MAX,
                                  self.engine.switches[0]['reason'])
                else:
                    self.assertIn('its saved login %s.' % dict(putback.MARKS)[key], self.engine.switches[0]['reason'])

    def test_the_limit_reached_with_evidence_of_the_fault_moves_it_in_words_that_fit(self):
        self.assertEqual(self.moves(self.keyed('limit', 'limit', seen={'fault': True, 'action': 'limit'})), ['a2'])
        reason = self.engine.switches[0]['reason']
        self.assertIn('was put back again and again within minutes and was lost again each time', reason)
        self.assertNotIn('within an hour', reason)

    def test_no_other_key_or_action_moves_it(self):
        set_policy(enabled=True)                                # any move would show
        never = [('leave', k) for k in ('file_limit', 'file_owner', 'file', 'record', 'sealed', 'split', 'switched',
                                        'renewing', None)] + \
            [(a, None) for a in ('wait', 'failed', 'put_back', 'promote', 'kept')]
        for action, key in never:
            with self.subTest(action=action, key=key):
                self.tearDown()
                self.setUp()
                set_policy(enabled=True)
                self.engine.live_state = api.ABSENT
                self.answer = self.keyed(action, key)
                loop = self.loop()
                for _ in range(3):
                    loop.run_pass()
                    self.clock.advance(61.0)
                self.assertEqual(self.engine.switches, [], (action, key))
                self.assertEqual(self.told, [])

    def test_the_account_is_found_by_its_id_when_one_email_is_saved_twice(self):
        self.engine.accts = [account('a1', 1, status='needs_login'), account('a2', 2),
                             account('a3', 3, email=A)]             # a1's email again, for another organization
        refused = self.keyed('leave', 'refused')
        self.assertEqual(self.moves(dataclasses.replace(refused, account_id=None, key=None,
                                                        reason='its saved login %s' % dict(putback.MARKS)['refused'])),
                         [])                                    # by the email alone: two accounts fit, so no move
        self.assertEqual(self.moves(refused), ['a2'])


if __name__ == '__main__':
    unittest.main()
