import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The page on the engine. The tick reads the engine's rows; a switch the engine recorded is
# said once; Claude Code's own numbers reach the engine (api.feed_status_line); the page's actions go through the
# engine; a cut-off switch is finished at start; logins are kept (never one the person removed); the guard against
# another switcher; and the order automatic switching will use, in our own form (plan.py). Made-up accounts only
# (tests/fakes_accounts_api.py).
import json
import os
import plistlib
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import artifacts, core, engine, history, keep, plan, server, statusline
from switchboard.accounts import api
from fakes_accounts_api import install

A, B, C = 'ana@work.example', 'ana@home.example', 'bo@lab.example'
H, D = 3600, 86400


class Page(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        home = os.path.join(self.tmp, 'home')
        os.makedirs(os.path.join(home, '.claude', 'projects'))
        env = mock.patch.dict(os.environ, {'HOME': home, 'SWITCHBOARD_HOME': os.path.join(self.tmp, 'data'),
                                           'CLAUDE_CONFIG_DIR': os.path.join(home, '.claude')})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        self.fake = install(self)
        self.shown = []
        for target, name, value in ((server, 'NOTIFY_RUN', lambda cmd: self.shown.append(cmd) or 0),
                                    (artifacts, 'refresh', lambda: None),
                                    (statusline, 'precompile', lambda *a, **k: None)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        for d, values in ((server.FED, {}), (server.GUARD, {'at': -1e9, 'label': None})):
            p = mock.patch.dict(d, values, clear=True)
            p.start()
            self.addCleanup(p.stop)
        keep.reset()
        self.addCleanup(keep.reset)

    def accounts(self):
        a = self.fake.hold(A, 'org-w', org_name='Work')
        b = self.fake.hold(B)
        self.fake.sign_in(A, 'org-w')
        return a, b

    def events(self):
        return [e['text'] for e in core.load()['events']]

    def history(self):
        return history.read()


class Tick(Page):
    def test_the_tick_puts_the_engines_accounts_in_the_state(self):
        a, b = self.accounts()
        now = time.time()
        self.fake.reading(b, five=(10, now + H), week=(30, now + 2 * D), at=now - 20)
        looked = server.tick()
        self.assertEqual([r['email'] for r in looked['rows']], [A, B])
        st = core.load()
        self.assertEqual(st['active'], A)
        self.assertEqual((st['accounts'][A]['id'], st['accounts'][A]['slot'], st['accounts'][A]['org']), (a, 1, 'Work'))
        self.assertEqual(st['accounts'][B]['usage']['weekly']['pct'], 30)
        self.assertIsNone(st['error'])

    def test_an_engine_that_cant_be_asked_is_the_pages_one_line(self):
        self.accounts()
        self.fake.fail['accounts'] = api.AccountsError('keychain_locked', "macOS's Keychain is locked: unlock the Mac.")
        server.tick()
        self.assertEqual(core.load()['error'], "macOS's Keychain is locked: unlock the Mac.")

    def test_a_switch_the_engine_recorded_is_said_once(self):
        self.accounts()
        server.tick()
        with core.locked() as st:                       # the engine's own record of the switch it made
            core.event(st, 'Switched Claude Code to %s' % B, 'switch', h='switch', to=B, account=B, by='auto',
                       reason='5-hour at 91%')
        self.fake.sign_in(B)
        server.tick()
        self.assertEqual(core.load()['active'], B)
        self.assertNotIn('Claude Code is now on %s' % B, self.events())
        self.assertEqual([r['by'] for r in self.history() if r['kind'] == 'switch'], ['auto'])

    def test_one_the_engine_did_not_record_is_claude_codes(self):
        self.accounts()
        server.tick()
        self.fake.sign_in(B)                            # a /login in Claude Code
        server.tick()
        self.assertIn('Claude Code is now on %s' % B, self.events())
        self.assertEqual(self.history()[-1]['by'], 'claude-code')

    def test_label_and_login_disagreeing_reaches_the_page(self):
        a, _ = self.accounts()
        self.fake.live_state = api.Live(state=api.PRESENT, account_id=a, email=A, proof='fingerprint', disagree=True)
        server.tick()
        self.assertTrue(core.load()['disagree'])
        self.assertTrue(server.render(core.load(), dict(server.facts(), connecting=[]))['disagree'])

    def test_state_that_nothing_reads_is_dropped(self):
        self.accounts()
        with core.locked() as st:
            st['repair'] = {'kind': 'waiting'}
            st['kept_notice'] = {'ids': ['x']}
        server.tick()
        st = core.load()
        self.assertNotIn('repair', st)
        self.assertNotIn('kept_notice', st)


class LiveNumbers(Page):
    def live(self, account, five, week, at, session='s1'):
        folder = statusline.live_dir()
        os.makedirs(folder, exist_ok=True)
        rec = {'v': 1, 'session_id': session, 'at': at, 'account': account,
               'rate_limits': {'five_hour': {'used_percentage': five[0], 'resets_at': five[1]},
                               'seven_day': {'used_percentage': week[0], 'resets_at': week[1]}}}
        with open(os.path.join(folder, session + '.json'), 'w') as f:
            json.dump(rec, f)
        os.utime(os.path.join(folder, session + '.json'), (at, at))
        return rec

    def test_claude_codes_own_numbers_reach_the_engine_once(self):
        a, _ = self.accounts()
        server.tick()
        with core.locked() as st:
            st['timeline'] = [[A, time.time() - 3600]]
        now = time.time()
        rec = self.live(A, (55, now + 2 * H), (40, now + 3 * D), now - 5)
        server.tick()
        self.assertEqual(len(self.fake.fed), 1)
        account_id, raw, at = self.fake.fed[0]
        self.assertEqual((account_id, raw, at), (a, rec['rate_limits'], rec['at']))
        server.tick()
        self.assertEqual(len(self.fake.fed), 1)         # the same record is handed over once
        self.assertEqual(core.load()['accounts'][A]['usage']['session']['pct'], 55)

    def test_numbers_from_another_account_are_never_fed(self):
        self.accounts()
        server.tick()
        with core.locked() as st:
            st['timeline'] = [[A, time.time() - 3600]]
        now = time.time()
        self.live(B, (55, now + 2 * H), (40, now + 3 * D), now - 5)
        server.tick()
        self.assertEqual(self.fake.fed, [])

    def test_an_engine_not_built_yet_never_stops_the_tick(self):
        self.accounts()
        server.tick()
        with core.locked() as st:
            st['timeline'] = [[A, time.time() - 3600]]
        now = time.time()
        self.live(A, (55, now + 2 * H), (40, now + 3 * D), now - 5)
        with mock.patch.object(api, 'feed_status_line', side_effect=NotImplementedError('B2')):
            server.tick()
        self.assertEqual(core.load()['accounts'][A]['usage']['session']['pct'], 55)


class Actions(Page):
    def test_a_switch_from_the_page_goes_through_the_engine_and_is_said_once(self):
        a, b = self.accounts()
        server.tick()
        server.act('/api/switch', {'email': B})
        self.assertIn(('switch', b, 'manual', 'page', 'Picked on the page.'), self.fake.calls)
        st = core.load()
        self.assertEqual(st['active'], B)
        self.assertEqual(self.events().count('Switched Claude Code to %s' % B), 1)

    def test_a_switch_the_engine_recorded_itself_is_not_said_again(self):
        a, b = self.accounts()
        server.tick()
        real = self.fake.switch

        def recording(target, kind='manual', source='cli', reason=''):
            r = real(target, kind=kind, source=source, reason=reason)
            with core.locked() as st:
                core.event(st, 'Switched Claude Code to %s' % B, 'switch', h='switch', to=B, account=B, by='you')
            return r
        with mock.patch.object(api, 'switch', recording):
            server.act('/api/switch', {'email': B})
        self.assertEqual(self.events().count('Switched Claude Code to %s' % B), 1)

    def test_a_refused_switch_changes_nothing_and_says_the_engines_words(self):
        self.accounts()
        server.tick()
        self.fake.fail['switch'] = api.AccountsError('claude_busy', 'Claude Code is busy with its login. Try again.')
        with self.assertRaises(engine.Refused) as cm:
            server.act('/api/switch', {'email': B})
        self.assertEqual(str(cm.exception), 'Claude Code is busy with its login. Try again.')
        self.assertEqual(core.load()['active'], A)

    def test_auto_takes_our_orders_and_lines_and_marks_the_choice_as_yours(self):
        self.accounts()
        with mock.patch.object(engine.HOST, 'ensure'):
            server.act('/api/auto', {'enabled': True, 'strategy': 'most-room', 'session_at': 85, 'model_at': 92,
                                     'model': 'Fable'})
        self.assertEqual(self.fake.auto['strategy'], 'most-room')
        self.assertEqual((self.fake.auto['session_at'], self.fake.auto['model_at'], self.fake.auto['model']),
                         (85, 92, 'Fable'))
        self.assertTrue(core.settings().get('auto_set_by_you'))
        for bad in ({'strategy': 'best'}, {'strategy': 'any-refills-first'}, {'session_at': 101},
                    {'model': 7}):
            with self.assertRaises(core.SwitchError):
                server.act('/api/auto', bad)

    def test_remove_asks_first_then_lets_the_account_go(self):
        a, b = self.accounts()
        server.tick()
        said = server.act('/api/remove', {'email': B})
        self.assertEqual(said['state'], 'confirm')
        self.assertIn('Remove %s from Switchboard?' % B, said['line'])
        self.assertFalse([c for c in self.fake.calls if c[0] == 'remove'])
        said = server.act('/api/remove', {'email': A, 'confirm': True})     # a yes without the question: asked again
        self.assertEqual(said['state'], 'confirm')
        self.assertFalse([c for c in self.fake.calls if c[0] == 'remove'])
        server.act('/api/remove', {'email': B, 'confirm': True})
        self.assertIn(('remove', b), self.fake.calls)
        self.assertTrue(keep.is_let_go((B, '')))
        self.assertIn('Removed %s from Switchboard' % B, self.events())

    def test_move_puts_an_account_at_a_place(self):
        a, b = self.accounts()
        c = self.fake.hold(C)
        server.tick()
        server.act('/api/move', {'email': C, 'to': 1})
        self.assertIn(('reorder', [c, a, b]), self.fake.calls)
        with self.assertRaises(core.SwitchError):
            server.act('/api/move', {'email': C, 'to': 9})

    def test_alias_goes_to_the_engine(self):
        a, _ = self.accounts()
        server.tick()
        server.act('/api/alias', {'email': A, 'alias': 'work'})
        self.assertIn(('set_alias', a, 'work'), self.fake.calls)

    def test_a_retired_request_is_refused_plainly(self):
        for path in server.RETIRED:
            with self.assertRaises(core.SwitchError) as cm:
                server.act(path, {'email': A})
            self.assertIn('does not do that any more', str(cm.exception))

    def test_add_keeps_the_login_claude_code_is_on(self):
        self.fake.sign_in(C, 'org-ivy')
        server.act('/api/engine', {'add': True})
        self.assertIn(('add_current',), self.fake.calls)
        self.assertEqual(engine.saved_identities(), {(C, 'org-ivy')})
        self.assertTrue(any(r['kind'] == 'kept' and r.get('account') == C for r in self.history()))

    def test_purge_removes_every_account_and_lets_them_go(self):
        self.accounts()
        server.tick()
        with self.assertRaises(core.SwitchError):
            server.act('/api/purge', {'typed': 'remove'})
        with mock.patch.object(engine.HOST, 'ensure'):
            server.act('/api/purge', {'typed': 'remove everything'})
        self.assertEqual(engine.saved_identities(), set())
        self.assertTrue(keep.is_let_go((A, 'org-w')) and keep.is_let_go((B, '')))

    def test_more_says_the_engines_state(self):
        self.accounts()
        self.fake.health_extra = {'keychain': {'ok': False, 'line': "macOS's Keychain is locked: unlock the Mac."}}
        v = server.more_view()
        self.assertEqual(v['status'][0], 'Switchboard %s keeps 2 accounts.' % core.VERSION)
        self.assertIn("macOS's Keychain is locked: unlock the Mac.", v['status'])


class Start(Page):
    def test_a_cut_off_switch_is_finished_at_start_and_said(self):
        self.fake.journal_note = 'A switch to ana@home.example was cut off; it was finished.'
        self.assertEqual(server.resolve_journal(), 'A switch to ana@home.example was cut off; it was finished.')
        self.assertIn('A switch to ana@home.example was cut off; it was finished.', self.events())
        self.assertEqual(self.history()[-1]['kind'], 'repair')

    def test_nothing_to_finish_says_nothing(self):
        self.assertIsNone(server.resolve_journal())
        self.assertEqual(self.events(), [])


class Keeping(Page):
    def test_a_new_login_is_kept_on_the_second_look(self):
        self.fake.hold(A, 'org-w')
        self.fake.sign_in(C, 'org-ivy')
        rows = engine.rows()
        now = time.time()
        self.assertIsNone(keep.look(rows, now=now))
        self.assertIsNone(keep.look(rows, now=now + 2))         # too soon: Claude Code may still be writing it
        self.assertEqual(keep.look(rows, now=now + 6), C)
        self.assertIn((C, 'org-ivy'), engine.saved_identities())
        self.assertIn('Kept %s · 2 accounts' % C, self.events())

    def test_a_held_login_gets_the_engines_keeping_pass(self):
        self.accounts()
        self.fake.keep_note = 'Kept the renewed login of ana@work.example.'
        keep.look(engine.rows(), now=time.time())
        self.assertIn(('keep_pass',), self.fake.calls)
        self.assertFalse([c for c in self.fake.calls if c[0] == 'add_current'])

    def test_a_removed_account_is_never_kept_again_by_itself_not_even_renewed(self):
        self.fake.hold(A, 'org-w')
        self.fake.sign_in(B)
        keep.let_go((B, ''))
        rows = engine.rows()
        now = time.time()
        for t in (0, 6, 12):
            self.assertIsNone(keep.look(rows, now=now + t))
        self.assertFalse([c for c in self.fake.calls if c[0] in ('add_current', 'keep_pass')])

    def test_a_slow_keychain_is_tried_again_sooner(self):
        self.fake.hold(A, 'org-w')
        self.fake.sign_in(C)
        self.fake.fail['add_current'] = api.AccountsError('keychain_slow', "macOS's Keychain is slow to answer.")
        rows = engine.rows()
        now = time.time()
        keep.look(rows, now=now)
        self.assertIsNone(keep.look(rows, now=now + 6))
        self.assertIn('Could not keep %s: macOS\'s Keychain is slow to answer. Trying again in a minute.' % C,
                      self.events())
        del self.fake.fail['add_current']
        self.assertIsNone(keep.look(rows, now=now + 30))        # not yet
        self.assertEqual(keep.look(rows, now=now + 70), C)

    def test_auto_switch_does_not_turn_on_by_itself_while_another_program_switches(self):
        self.accounts()
        folder = os.path.join(core.home(), 'Library', 'LaunchAgents')
        os.makedirs(folder)
        with open(os.path.join(folder, 'com.example.claude-switch.plist'), 'wb') as f:
            plistlib.dump({'Label': 'com.example.claude-switch', 'ProgramArguments': ['/opt/switch']}, f)
        self.assertFalse(keep.auto_by_itself(2, turn_on=lambda: self.fail('turned on')))
        os.remove(os.path.join(folder, 'com.example.claude-switch.plist'))
        turned = []
        self.assertTrue(keep.auto_by_itself(2, turn_on=lambda: turned.append(1)))
        self.assertEqual(turned, [1])

    def test_another_switcher_is_named_on_the_page(self):
        folder = os.path.join(core.home(), 'Library', 'LaunchAgents')
        os.makedirs(folder)
        with open(os.path.join(folder, 'com.example.claude-switch.plist'), 'wb') as f:
            plistlib.dump({'Label': 'com.example.claude-switch', 'ProgramArguments': ['/opt/switch']}, f)
        self.accounts()
        server.tick()
        v = server.render(core.load(), dict(server.facts(), connecting=[]))
        self.assertEqual(v['guard'], "Another program is also switching Claude Code's login "
                                     "(com.example.claude-switch). Turn it off first.")


class Order(unittest.TestCase):
    """plan.py, our own rule: each window against its own line, a 5-point landing margin, refills first or most
    room, nothing landed on over a line."""
    NOW = 1.8e9

    def acct(self, name, week, week_in, five=None, models=None, active=False, **more):
        return dict({'name': name, 'weekly_used_pct': week, 'weekly_resets_at': self.NOW + week_in,
                     'five_hour_used_pct': five, 'five_hour_resets_at': self.NOW + 2 * H if five is not None else None,
                     'scoped': [{'name': n, 'used_pct': p, 'resets_at': self.NOW + r} for n, (p, r) in (models or {}).items()],
                     'active': active, 'trusted': True}, **more)

    def lines(self, **kw):
        return dict({'session_line': 90, 'weekly_line': 95, 'model_line': 95, 'models': [], 'strategy': 'refills-first',
                     'margin': 5}, **kw)

    def names(self, accounts, **kw):
        return [j['name'] for j in plan.order(accounts, self.lines(**kw), self.NOW)]

    def test_refills_first_takes_the_soonest_weekly_reset_then_the_most_room(self):
        accts = [self.acct('a', 50, 3 * D, active=True), self.acct('b', 40, 2 * D), self.acct('c', 10, 1 * D),
                 self.acct('d', 20, 1 * D)]
        self.assertEqual(self.names(accts), ['a', 'c', 'd', 'b'])
        self.assertEqual(plan.next_account(accts, self.lines(), self.NOW)['account']['name'], 'c')

    def test_most_room_takes_the_most_room(self):
        accts = [self.acct('a', 50, 3 * D, active=True), self.acct('b', 40, 2 * D), self.acct('c', 10, 1 * D)]
        self.assertEqual(self.names(accts, strategy='most-room'), ['a', 'c', 'b'])

    def test_nothing_lands_within_the_margin_of_a_line(self):
        accts = [self.acct('a', 96, 3 * D, active=True), self.acct('b', 91, 1 * D), self.acct('c', 20, 2 * D, five=86)]
        ranked = plan.order(accts, self.lines(), self.NOW)
        self.assertEqual([(j['name'], j['usable'], j['reason']) for j in ranked],      # soonest back first
                         [('c', False, 'not enough room'), ('b', False, 'not enough room'), ('a', False, 'over its line')])
        self.assertEqual([j['blocked_by'] for j in ranked], [['session'], ['weekly'], ['weekly']])
        nxt = plan.next_account(accts, self.lines(), self.NOW)
        self.assertIsNone(nxt['account'])
        self.assertEqual(nxt['reason'], 'no other account has room')
        self.assertEqual(nxt['back_at'], self.NOW + 2 * H)      # c's 5-hour window refills first

    def test_the_account_in_use_stays_first_while_under_its_lines(self):
        accts = [self.acct('a', 92, 3 * D, active=True), self.acct('b', 10, 1 * D)]
        ranked = plan.order(accts, self.lines(), self.NOW)
        self.assertEqual([(j['name'], j['usable']) for j in ranked], [('a', True), ('b', True)])

    def test_a_counted_model_week_holds_an_account_back_and_an_uncounted_one_never(self):
        accts = [self.acct('a', 50, 3 * D, active=True), self.acct('b', 10, 1 * D, models={'Fable': (93, 1 * D)})]
        self.assertEqual(plan.next_account(accts, self.lines(), self.NOW)['account']['name'], 'b')
        nxt = plan.next_account(accts, self.lines(models=['Fable']), self.NOW)
        self.assertIsNone(nxt['account'])
        self.assertEqual(plan.order(accts, self.lines(models=['fable']), self.NOW)[1]['blocked_by'], ['Fable'])

    def test_unknown_and_untrusted_readings_are_never_landed_on(self):
        accts = [self.acct('a', 50, 3 * D, active=True), self.acct('b', 10, 1 * D, trusted=False),
                 dict(self.acct('c', None, 1 * D), weekly_used_pct=None), self.acct('d', 10, 1 * D, needs_login=True),
                 self.acct('e', 10, 1 * D, disabled=True)]
        reasons = dict((j['name'], j['reason']) for j in plan.order(accts, self.lines(), self.NOW))
        self.assertEqual(reasons, {'a': None, 'b': 'no trusted reading', 'c': 'no reading yet', 'd': 'needs sign-in',
                                   'e': 'not kept here'})

    def test_the_plan_and_the_engine_take_the_same_two_orders(self):
        self.assertEqual(plan.STRATEGIES, ('refills-first', 'most-room', 'stay-then-refills'))
        self.assertEqual(engine.STRATEGIES, plan.STRATEGIES)


class Trust(unittest.TestCase):
    def test_a_reading_is_shown_an_hour_and_planned_on_fifteen_minutes(self):
        now = 1.8e9
        a = {'usage': {'weekly': {'pct': 10}}}
        self.assertTrue(server.reading_trusted(dict(a, usage_at=now - 3000), now))
        self.assertFalse(server.reading_trusted(dict(a, usage_at=now - 3700), now))
        self.assertFalse(server.reading_trusted(dict(a, usage_at=now - 1000), now, limit=server.PLANNED_FOR))
        self.assertFalse(server.reading_trusted(dict(a, usage_at=now + 60), now))      # from the future: not known
        self.assertFalse(server.reading_trusted({'usage_at': now}, now))


if __name__ == '__main__':
    unittest.main()
