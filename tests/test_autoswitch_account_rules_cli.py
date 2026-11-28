import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# An account's rule in auto-switch, where it is set and where it shows outside the page: the page's API (/api/state
# gives each account's rule where it gives the auto settings; POST /api/auto changes one account's rule), the plan the
# page, the status line and the terminal mark Next from, `switchboard auto last|out|in NAME`, `switchboard auto`, the
# account's line in `switchboard` and `switchboard status`, and the menu bar. Made-up accounts only; the engine is a
# stand-in (tests/fakes_accounts_api.py) and the running page a stand-in too (test_term.Page).
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import __main__ as cli
from switchboard import core, engine, menubar, server, term
from switchboard.accounts import policy
from test_autoswitch_policy import reading, snap
from test_term import VIEW, Page, Sandbox, hold, iso_in, lines

A, B, C = 'ana@northwind.example', 'ana@fernhill.example', 'ana@harbor.example'
HELP = ('switchboard auto [on|off]   whether auto-switch is on; on or off changes it, and it stays as you set it\n'
        'switchboard auto check [--dry-run] [--json] [--five-hour N] [--week N] [--order WORD]\n'
        '                            one pass of auto-switch now, then exit, also with auto-switch off, for a scheduled\n'
        '                            job on a Mac where the page is not running: 0 moved, 1 failed or could not look,\n'
        "                            2 wrong words, 3 held back, 4 nothing to do, 5 the page's loop runs (so it moved\n"
        '                            nothing); --dry-run decides and says why, and changes nothing; --five-hour, --week\n'
        '                            and --order are for this check only; --json prints it as JSON\n'
        'switchboard auto why [--json] [--five-hour N] [--week N] [--order WORD]\n'
        '                            what auto-switch would do now, and why (the same as auto check --dry-run, with its\n'
        '                            codes: 0 it would move, 1 it could not look, 3 it would be held back, 4 it stays)\n'
        'switchboard auto last NAME  auto-switch goes to that account only when no other account has room\n'
        'switchboard auto out NAME   keep that account out of auto-switch: it never moves Claude Code there, or away '
        'from it\n'
        'switchboard auto in NAME    auto-switch uses that account as usual\n'
        'switchboard auto line NAME [--five-hour N] [--week N]\n'
        '                            that account switches at lines of its own, from 50 to 99.9; alone, its lines;\n'
        '                            switchboard auto line NAME off puts it back on the shared ones\n'
        'switchboard auto rank NAME N|off\n'
        '                            auto-switch lands on rank 1 first, then 2, and on an account with no rank after\n'
        '                            every ranked one; N is 1 to 99; off takes its rank away\n'
        'switchboard auto signed-in [on|off]\n'
        "                            keep Claude Code signed in: when its sign-in is lost and can't be put back, or is\n"
        '                            about to end, move it to another signed-in account, also with auto-switch off (on\n'
        '                            until you turn it off)\n')


class Three(Sandbox):
    def setUp(self):
        super().setUp()
        self.ids = {A: hold(self.fake, A, active=True, five=(20, iso_in(3600)), week=(30, iso_in(86400))),
                    B: hold(self.fake, B, five=(10, iso_in(3600)), week=(10, iso_in(2 * 86400))),
                    C: hold(self.fake, C, five=(10, iso_in(3600)), week=(50, iso_in(3 * 86400)))}
        self.fake.auto['enabled'] = True

    def page_view(self):
        return cli.local_view()                 # the page's own state, worked out the page's way (server.render)


class TheApi(Three):
    def test_the_state_gives_each_accounts_rule_where_it_gives_the_auto_settings(self):
        v = self.page_view()
        self.assertEqual(v['auto']['rules'], dict((i, 'in') for i in self.ids.values()))
        for k in ('enabled', 'session_at', 'weekly_at', 'strategy', 'models_seen'):          # what was there stays
            self.assertIn(k, v['auto'])
        self.fake.auto['rules'] = {self.ids[B]: 'last', self.ids[C]: 'out', 'removed-account': 'out'}
        v = self.page_view()
        self.assertEqual(v['auto']['rules'], {self.ids[A]: 'in', self.ids[B]: 'last', self.ids[C]: 'out'})

    def test_the_post_changes_one_accounts_rule(self):
        for name, rule, want in ((self.ids[B], 'last', {self.ids[B]: 'last'}), (C, 'out', {self.ids[B]: 'last',
                                                                                          self.ids[C]: 'out'}),
                                 ('2', 'in', {self.ids[C]: 'out'})):
            with self.subTest(name=name, rule=rule):
                server.act('/api/auto', {'rule': rule, 'account': name})
                self.assertEqual(self.fake.auto.get('rules'), want)
        sets = [c[1] for c in self.fake.calls if c[0] == 'set_auto']
        self.assertEqual(sets, [{'rule': (self.ids[B], 'last')}, {'rule': (self.ids[C], 'out')},
                                {'rule': (self.ids[B], 'in')}])
        said = [e['text'] for e in core.load()['events']]
        self.assertIn('Auto-switch now uses %s last: only when no other account has room.' % B, said)
        self.assertIn('Auto-switch now leaves %s alone: it never moves Claude Code there, or away from it.' % C, said)
        self.assertIn('Auto-switch uses %s as usual.' % B, said)
        self.assertFalse(any(t.startswith('Automatic switching on') for t in said))     # only the rule was changed

    def test_an_unknown_account_or_word_is_refused_in_one_plain_sentence(self):
        for body, words in (({'rule': 'last', 'account': 'nobody@made-up.example'},
                             'No account called nobody@made-up.example. Run switchboard to see them.'),
                            ({'rule': 'never', 'account': B}, 'Pick in, last or out for an account.'),
                            ({'rule': 7, 'account': B}, 'Pick in, last or out for an account.'),
                            ({'rule': 'last'}, 'Name an account: its number, email, short name or id.'),
                            ({'rule': 'last', 'account': ''}, 'Name an account: its number, email, short name or id.')):
            with self.subTest(body=body), self.assertRaises(core.SwitchError) as e:
                server.act('/api/auto', body)
            self.assertEqual(str(e.exception), words)
        self.assertEqual([c for c in self.fake.calls if c[0] == 'set_auto'], [])
        self.assertNotIn('rules', self.fake.auto)

    def test_next_never_names_a_kept_out_account_and_names_none_on_one(self):
        v = self.page_view()
        self.assertEqual(v['plan']['next'], B)                       # B refills first and has room
        self.fake.auto['rules'] = {self.ids[B]: 'out'}
        self.assertEqual(self.page_view()['plan']['next'], C)
        self.fake.auto['rules'] = {self.ids[B]: 'last'}
        self.assertEqual(self.page_view()['plan']['next'], C)        # used last: after every other
        self.fake.auto['rules'] = {self.ids[A]: 'out'}
        v = self.page_view()
        self.assertEqual((v['plan']['next'], v['plan']['why']), (None, 'kept out of auto-switch'))
        self.assertIsNone(v['next'])


class TheCommand(Three):
    def test_last_out_and_in_say_what_happens_now(self):
        for args, said, rules in ((('last', B), 'Auto-switch now uses %s last: only when no other account has room.\n'
                                   % B, {self.ids[B]: 'last'}),
                                  (('out', '3'), 'Auto-switch now leaves %s alone: it never moves Claude Code there, '
                                   'or away from it.\n' % C, {self.ids[B]: 'last', self.ids[C]: 'out'}),
                                  (('in', B), 'Auto-switch uses %s as usual.\n' % B, {self.ids[C]: 'out'})):
            with self.subTest(args=args):
                self.assertEqual(self.run_cli('auto', *args), (0, said))
                self.assertEqual(self.fake.auto.get('rules'), rules)
        said = [e['text'] for e in core.load()['events']]            # no page: the history says it here
        self.assertIn('Auto-switch uses %s as usual.' % B, said)

    def test_it_goes_through_the_running_page(self):
        page = Page(VIEW)
        self.addCleanup(page.stop)
        os.environ['SWITCHBOARD_PORT'] = str(page.server_address[1])
        self.assertEqual(self.run_cli('auto', 'last', B)[0], 0)
        self.assertEqual(page.posts, [('/api/auto', {'rule': 'last', 'account': self.ids[B]}, '1')])
        self.assertEqual([c for c in self.fake.calls if c[0] == 'set_auto'], [])      # the page changed it, not here

    def test_anything_else_after_auto_exits_2_with_the_forms(self):
        forms = ('switchboard auto [on|off]\n'
                 'switchboard auto check [--dry-run] [--json] [--five-hour N] [--week N] [--order WORD]\n'
                 'switchboard auto why [--json] [--five-hour N] [--week N] [--order WORD]\n'
                 'switchboard auto last NAME\n'
                 'switchboard auto out NAME\nswitchboard auto in NAME\n'
                 'switchboard auto line NAME [--five-hour N] [--week N]\n'
                 'switchboard auto line NAME off\nswitchboard auto rank NAME N|off\n'
                 'switchboard auto signed-in [on|off]\n')
        for args in (('maybe',), ('last',), ('last', B, C), ('first', B), ('on', B), ('LAST', B)):
            with self.subTest(args=args):
                self.assertEqual(self.run_cli('auto', *args), (2, forms))
        self.assertNotIn('rules', self.fake.auto)

    def test_an_unknown_name_is_refused_as_everywhere(self):
        with self.assertRaises(core.SwitchError) as e:
            self.run_cli('auto', 'out', 'nobody@made-up.example')
        self.assertEqual(str(e.exception), 'No account called nobody@made-up.example. Run switchboard to see them.')

    def test_the_demo_refuses_it_here(self):
        core.DEMO = True
        with self.assertRaises(core.SwitchError) as e:
            self.run_cli('auto', 'last', 'maya@ivy.example')
        self.assertIn('The demo has made-up accounts', str(e.exception))

    def test_auto_alone_adds_one_line_for_each_account_with_a_rule(self):
        agents = os.path.join(self.home, 'Library', 'LaunchAgents')      # the page runs under its background job
        os.makedirs(agents, exist_ok=True)
        open(os.path.join(agents, cli.AGENT + '.plist'), 'w').close()
        out = self.run_cli('auto')[1].splitlines()
        self.assertEqual(len(out), 1)
        self.fake.auto['rules'] = {self.ids[C]: 'out', self.ids[B]: 'last', 'removed-account': 'last'}
        out = self.run_cli('auto')[1].splitlines()
        self.assertTrue(out[0].startswith('Auto-switch is on: at 90% 5-hour'), out)
        self.assertEqual(out[1:], ['%s is used last.' % B, '%s is kept out of auto-switch.' % C])

    def test_a_kept_out_account_is_still_moved_to_and_away_from_by_hand(self):
        self.fake.auto['rules'] = {self.ids[C]: 'out', self.ids[A]: 'out'}
        for args, who in ((('switch', C), C), (('2',), B)):             # onto one kept out, then away from it
            with self.subTest(args=args):
                code, out = self.run_cli(*args)
                self.assertEqual(code, 0, out)
                self.assertEqual(self.fake.live().account_id, self.ids[who])

    def test_three_help_lines_under_auto(self):
        self.assertEqual(cli.command_help('auto') + '\n', HELP)
        for line in HELP.splitlines():
            self.assertLessEqual(len(line) + 2, 120)


class TheAccountsLine(unittest.TestCase):
    def view(self, rules):
        ids = dict((a['email'], 'id-%d' % a['slot']) for a in VIEW['accounts'])
        return dict(VIEW, auto=dict(VIEW['auto'], rules=dict((ids[e], r) for e, r in rules.items())),
                    accounts=[dict(a, id=ids[a['email']]) for a in VIEW['accounts']])

    def test_the_two_notes_where_the_line_has_its_small_notes(self):
        got = '\n'.join(lines(self.view({'ana@fernhill.example': 'last', 'ana@mail.example': 'out',
                                         'ana@harbor.example': 'in'})))
        self.assertIn('2  ana@fernhill.example   Max 20x · Personal  used last\n', got)
        self.assertIn('5  ana@mail.example       Max 20x · Personal  in use · kept out of auto-switch\n', got)
        self.assertIn('3  ana@harbor.example     Team · Harbor\n', got)
        self.assertEqual(lines(self.view({})), lines(VIEW))                       # no rule: the view as before

    def test_the_menu_bar_rows_carry_no_note_for_a_rule(self):
        # an email there is never cut short to make room for a note: a rule changes nothing on a menu row
        plain = menubar.rows(self.view({}))
        self.assertEqual(menubar.rows(self.view({'ana@fernhill.example': 'last', 'ana@mail.example': 'out',
                                                 'ana@lab.example': 'out'})), plain)
        self.assertEqual(plain, menubar.rows(dict(VIEW, accounts=[dict(a, id='id-%d' % a['slot'])
                                                                    for a in VIEW['accounts']])))

    def test_the_sentences(self):
        self.assertEqual(term.rule_said('maya@ivy.example', 'last'),
                         'Auto-switch now uses maya@ivy.example last: only when no other account has room.')
        self.assertEqual(term.rule_said('maya@ivy.example', 'out'),
                         'Auto-switch now leaves maya@ivy.example alone: it never moves Claude Code there, '
                         'or away from it.')
        self.assertEqual(term.rule_said('maya@ivy.example', 'in'), 'Auto-switch uses maya@ivy.example as usual.')


class ThePagesLine(unittest.TestCase):
    def test_it_says_why_claude_code_stays_on_a_kept_out_account_as_it_says_no_room(self):
        r = {'a': reading('a', five=92.0, week=20.0), 'b': reading('b', five=5.0, week=5.0)}
        out = policy.decide(snap(r, settings=policy.Settings(enabled=True, rules=(('a', 'out'),))))
        room = policy.decide(snap(dict(r, b=reading('b', five=95.0, week=5.0))))
        on = {'enabled': True, 'on': True}
        for d in (out, room):
            with self.subTest(hold=d.hold), mock.patch.dict(server.DECIDE, {'decision': d, 'at': 100.0}):
                said = d.reason if d.reason.endswith('.') else d.reason + '.'
                self.assertEqual(server.auto_line(on, now=101.0), said)
        self.assertEqual(out.reason, 'a@acct.example 5-hour at 92% (line 90%). It is kept out of auto-switch, so '
                                     'Claude Code stays on it. Move by hand when you want to.')
        self.assertEqual((out.kind, room.kind), ('forced', 'forced'))


class TheMenuBar(Three):
    def running(self):
        """The page's state as the running page has it while auto-switch runs (its Next marked)."""
        v = cli.local_view()
        v['auto'] = dict(v['auto'], on=True)
        v['next'] = server.marked_next(v['accounts'], v['plan'], v['auto'])
        return v

    def test_a_kept_out_account_is_never_next_there_and_use_still_goes_there(self):
        self.assertEqual(menubar.next_email(self.running()), B)
        self.fake.auto['rules'] = {self.ids[B]: 'out'}
        rows = dict((r['email'], r) for r in menubar.rows(self.running()))
        self.assertEqual(menubar.next_email(self.running()), C)
        self.assertFalse(rows[B]['next'])
        self.assertNotIn(('Next', 'next'), rows[B]['marks'])
        self.assertEqual(rows[B]['action'], 'use')
        self.assertEqual(menubar.click(rows[B]), ('/api/switch', {'email': B}))      # Use: by hand, as on any row


class TheEngineSide(Three):
    def test_rules_from_the_engine_are_kept_to_the_two_words(self):
        self.fake.auto['rules'] = {self.ids[B]: 'last', self.ids[C]: 'never', 7: 'out'}
        self.assertEqual(engine.auto_settings()['rules'], {self.ids[B]: 'last'})
        self.fake.auto.pop('rules')
        self.assertEqual(engine.auto_settings()['rules'], {})

    def test_a_removed_account_takes_its_rule_with_it(self):
        server.act('/api/auto', {'rule': 'out', 'account': C})
        engine.remove(self.ids[C])
        self.assertNotIn('rules', self.fake.auto)


if __name__ == '__main__':
    unittest.main()
