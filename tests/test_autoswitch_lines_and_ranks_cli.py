import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# An account's own switch lines, its rank, the third order, several models and the best switch by hand, where they are
# set and where they show outside the policy: the page's API (/api/state gives each account's own lines and rank; POST
# /api/auto changes them), the plan the page marks Next from, `switchboard auto line|rank`, `switchboard auto`,
# `switchboard switch --best`, the bars in the terminal and the menu bar, and `switchboard status --json`. Made-up
# accounts only; the engine is a stand-in (tests/fakes_accounts_api.py) and the running page a stand-in too.
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import __main__ as cli
from switchboard import autorules, core, engine, menubar, report, server, term
from test_term import VIEW, Page, Sandbox, hold, iso_in

A, B, C = 'ana@northwind.example', 'ana@fernhill.example', 'ana@harbor.example'
HERE = os.path.dirname(os.path.abspath(__file__))
UNDER = ' ' * 28           # where a help line's words go on the lines under its command
NEW_HELP = ('switchboard auto line NAME [--five-hour N] [--week N]\n' +
            UNDER + 'that account switches at lines of its own, from 50 to 99.9; alone, its lines;\n' +
            UNDER + 'switchboard auto line NAME off puts it back on the shared ones\n'
            'switchboard auto rank NAME N|off\n' +
            UNDER + 'auto-switch lands on rank 1 first, then 2, and on an account with no rank after\n' +
            UNDER + 'every ranked one; N is 1 to 99; off takes its rank away\n')


class Three(Sandbox):
    def setUp(self):
        super().setUp()
        self.ids = {A: hold(self.fake, A, active=True, five=(20, iso_in(3600)), week=(30, iso_in(86400))),
                    B: hold(self.fake, B, five=(10, iso_in(3600)), week=(10, iso_in(2 * 86400))),
                    C: hold(self.fake, C, five=(10, iso_in(3600)), week=(50, iso_in(3 * 86400)))}
        self.fake.auto['enabled'] = True

    def page_view(self):
        return cli.local_view()

    def run_both(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()


class TheApi(Three):
    def test_the_state_gives_each_accounts_own_lines_and_rank(self):
        v = self.page_view()
        self.assertEqual((v['auto']['own_lines'], v['auto']['ranks']), ({}, {}))
        self.fake.auto['own_lines'] = {self.ids[B]: {'weekly_at': 80.0}, 'removed-account': {'weekly_at': 70.0}}
        self.fake.auto['ranks'] = {self.ids[C]: 1, 'removed-account': 2, self.ids[A]: 0}
        v = self.page_view()
        self.assertEqual(v['auto']['own_lines'], {self.ids[B]: {'weekly_at': 80.0}})
        self.assertEqual(v['auto']['ranks'], {self.ids[C]: 1})
        for k in ('rules', 'session_at', 'weekly_at', 'strategy', 'models_seen'):          # what was there stays
            self.assertIn(k, v['auto'])

    def test_the_post_changes_one_accounts_own_lines_and_rank_and_history_says_so(self):
        server.act('/api/auto', {'account': B, 'own_lines': {'session_at': 85}})
        server.act('/api/auto', {'account': '2', 'own_lines': {'weekly_at': 90.5}})
        self.assertEqual(self.fake.auto['own_lines'], {self.ids[B]: {'session_at': 85, 'weekly_at': 90.5}})
        server.act('/api/auto', {'account': B, 'own_lines': {'session_at': None}})
        self.assertEqual(self.fake.auto['own_lines'], {self.ids[B]: {'weekly_at': 90.5}})
        server.act('/api/auto', {'account': C, 'rank': 1})
        self.assertEqual(self.fake.auto['ranks'], {self.ids[C]: 1})
        server.act('/api/auto', {'account': C, 'rank': None})
        server.act('/api/auto', {'account': B, 'own_lines': None})
        self.assertNotIn('ranks', self.fake.auto)
        self.assertNotIn('own_lines', self.fake.auto)
        said = [e['text'] for e in core.load()['events']]
        self.assertIn('%s now has its own line: 85%% 5-hour.' % B, said)
        self.assertIn('%s now has its own lines: 85%% 5-hour · 90.5%% weekly.' % B, said)
        self.assertIn('%s is rank 1 now: auto-switch lands there before any account with a later rank.' % C, said)
        self.assertIn('%s has no rank now: auto-switch uses it after every ranked account.' % C, said)
        self.assertIn('%s is back on the shared lines.' % B, said)
        self.assertFalse(any(t.startswith('Automatic switching on') for t in said))     # only the account changed

    def test_wrong_values_are_refused_in_one_plain_sentence(self):
        for body, words in (({'account': B, 'own_lines': {'session_at': 120}}, 'Pick a number from 50 to 99.9.'),
                            ({'account': B, 'own_lines': {'session_at': '85'}}, 'Pick a number from 50 to 99.9.'),
                            ({'account': B, 'own_lines': {'model_at': 85}},
                             'Give an account its own 5-hour or weekly line.'),
                            ({'account': B, 'own_lines': 85}, 'Give an account its own 5-hour or weekly line.'),
                            ({'account': B, 'rank': 0}, 'Pick a whole number from 1 to 99.'),
                            ({'account': B, 'rank': False}, 'Pick a whole number from 1 to 99.'),
                            ({'account': B, 'rank': '1'}, 'Pick a whole number from 1 to 99.'),
                            ({'rank': 1}, 'Name an account: its number, email, short name or id.'),
                            ({'account': 'nobody@made-up.example', 'rank': 1},
                             'No account called nobody@made-up.example. Run switchboard to see them.'),
                            ({'model': 7}, 'That is not a model name.'),
                            ({'model': ['Fable', '<b>']}, 'That is not a model name.')):
            with self.subTest(body=body), self.assertRaises(core.SwitchError) as e:
                server.act('/api/auto', body)
            self.assertEqual(str(e.exception), words)
        self.assertEqual([c for c in self.fake.calls if c[0] == 'set_auto'], [])

    def test_several_models_and_the_third_order_through_the_post(self):
        server.act('/api/auto', {'model': ['Fable', 'Opus 5']})
        self.assertEqual(engine.auto_settings()['models'], ['Fable', 'Opus 5'])
        server.act('/api/auto', {'model': 'Fable'})
        self.assertEqual(engine.auto_settings()['models'], ['Fable'])
        server.act('/api/auto', {'model': 'Fable, Opus 5'})
        self.assertEqual(engine.auto_settings()['models'], ['Fable', 'Opus 5'])
        server.act('/api/auto', {'model': 'Fable'})
        self.assertEqual(engine.auto_settings()['models'], ['Fable'])
        server.act('/api/auto', {'strategy': 'stay-then-refills'})
        self.assertEqual(self.page_view()['auto']['strategy'], 'stay-then-refills')

    def test_next_follows_ranks_and_own_lines(self):
        self.assertEqual(self.page_view()['plan']['next'], B)                  # B refills first and has room
        self.fake.auto['ranks'] = {self.ids[C]: 1}
        self.assertEqual(self.page_view()['plan']['next'], C)
        self.fake.auto['ranks'] = {self.ids[C]: 2, self.ids[B]: 2}
        self.assertEqual(self.page_view()['plan']['next'], B)                  # one rank: the order decides
        self.fake.auto.pop('ranks')
        self.fake.auto['own_lines'] = {self.ids[B]: {'weekly_at': 52}}         # 10% used, line 52: room
        self.assertEqual(self.page_view()['plan']['next'], B)
        self.fake.auto['own_lines'] = {self.ids[B]: {'session_at': 50}}        # 10% used: 40 points of room
        self.assertEqual(self.page_view()['plan']['next'], B)
        self.fake.reading(self.ids[B], five=(47, core.parse_iso(iso_in(3600))),
                          week=(10, core.parse_iso(iso_in(2 * 86400))))
        self.assertEqual(self.page_view()['plan']['next'], C)                  # 3 points under its own 50


class TheCommand(Three):
    def test_line_gives_merges_says_and_takes_away(self):
        for args, said, own in (
                (('line', B, '--five-hour', '85'), '%s now has its own line: 85%% 5-hour.\n' % B,
                 {'session_at': 85.0}),
                (('line', '2', '--week', '90.5'), '%s now has its own lines: 85%% 5-hour · 90.5%% weekly.\n' % B,
                 {'session_at': 85.0, 'weekly_at': 90.5}),
                (('line', B, '--week', '91', '--five-hour', '80'),
                 '%s now has its own lines: 80%% 5-hour · 91%% weekly.\n' % B,
                 {'session_at': 80.0, 'weekly_at': 91.0})):
            with self.subTest(args=args):
                self.assertEqual(self.run_cli('auto', *args), (0, said))
                self.assertEqual(self.fake.auto['own_lines'], {self.ids[B]: own})
        self.assertEqual(self.run_cli('auto', 'line', B),
                         (0, '%s switches at 80%% 5-hour (its own) · 91%% weekly (its own).\n' % B))
        self.assertEqual(self.run_cli('auto', 'line', C), (0, '%s switches at the shared lines: 90%% 5-hour · 95%% '
                                                              'weekly.\n' % C))
        self.assertEqual(self.run_cli('auto', 'line', B, 'off'), (0, '%s is back on the shared lines.\n' % B))
        self.assertNotIn('own_lines', self.fake.auto)
        said = [e['text'] for e in core.load()['events']]                    # no page: the history says it here
        self.assertIn('%s is back on the shared lines.' % B, said)

    def test_rank_gives_and_takes_away(self):
        self.assertEqual(self.run_cli('auto', 'rank', C, '1'),
                         (0, '%s is rank 1 now: auto-switch lands there before any account with a later rank.\n' % C))
        self.assertEqual(self.fake.auto['ranks'], {self.ids[C]: 1})
        self.assertEqual(self.run_cli('auto', 'rank', '3', 'off'),
                         (0, '%s has no rank now: auto-switch uses it after every ranked account.\n' % C))
        self.assertNotIn('ranks', self.fake.auto)

    def test_wrong_numbers_are_refused_and_nothing_changes(self):
        for args, words in ((('line', B, '--five-hour', '120'), '--five-hour takes a number from 50 to 99.9.'),
                            (('line', B, '--week', '49.9'), '--week takes a number from 50 to 99.9.'),
                            (('line', B, '--week', 'high'), '--week takes a number from 50 to 99.9.'),
                            (('line', B, '--week', 'nan'), '--week takes a number from 50 to 99.9.'),
                            (('rank', B, '0'), 'Pick a whole number from 1 to 99.'),
                            (('rank', B, '100'), 'Pick a whole number from 1 to 99.'),
                            (('rank', B, '1.5'), 'Pick a whole number from 1 to 99.'),
                            (('rank', B, 'first'), 'Pick a whole number from 1 to 99.'),
                            (('rank', 'nobody@made-up.example', '1'),
                             'No account called nobody@made-up.example. Run switchboard to see them.'),
                            (('line', 'nobody@made-up.example', 'off'),
                             'No account called nobody@made-up.example. Run switchboard to see them.')):
            with self.subTest(args=args), self.assertRaises(core.SwitchError) as e:
                self.run_cli('auto', *args)
            self.assertEqual(str(e.exception), words)
        self.assertEqual([c for c in self.fake.calls if c[0] == 'set_auto'], [])

    def test_a_wrong_form_exits_2_with_its_forms(self):
        line = 'switchboard auto line NAME [--five-hour N] [--week N]\nswitchboard auto line NAME off\n'
        for args, forms in ((('line',), line), (('line', '--week', '90'), line), (('line', B, '--week'), line),
                            (('line', B, '--week', '90', '--week', '91'), line), (('line', B, '--5h', '90'), line),
                            (('line', B, 'on'), line), (('line', B, 'off', 'now'), line), (('line', 'off'), line),
                            (('rank',), 'switchboard auto rank NAME N|off\n'),
                            (('rank', B), 'switchboard auto rank NAME N|off\n'),
                            (('rank', B, '1', '2'), 'switchboard auto rank NAME N|off\n')):
            with self.subTest(args=args):
                self.assertEqual(self.run_cli('auto', *args), (2, forms))
        self.assertEqual([c for c in self.fake.calls if c[0] == 'set_auto'], [])
        code, out = self.run_cli('auto', 'maybe')
        self.assertEqual(code, 2)
        self.assertTrue(out.endswith('switchboard auto line NAME [--five-hour N] [--week N]\n'
                                     'switchboard auto line NAME off\nswitchboard auto rank NAME N|off\n'), out)

    def test_both_go_through_the_running_page(self):
        page = Page(VIEW)
        self.addCleanup(page.stop)
        os.environ['SWITCHBOARD_PORT'] = str(page.server_address[1])
        self.assertEqual(self.run_cli('auto', 'line', B, '--week', '80')[0], 0)
        self.assertEqual(self.run_cli('auto', 'rank', B, '2')[0], 0)
        self.assertEqual(self.run_cli('auto', 'line', B, 'off')[0], 0)
        self.assertEqual(page.posts, [('/api/auto', {'account': self.ids[B], 'own_lines': {'weekly_at': 80.0}}, '1'),
                                      ('/api/auto', {'account': self.ids[B], 'rank': 2}, '1'),
                                      ('/api/auto', {'account': self.ids[B], 'own_lines': None}, '1')])
        self.assertEqual([c for c in self.fake.calls if c[0] == 'set_auto'], [])      # the page changed it, not here

    def test_auto_alone_says_each_accounts_rule_own_lines_and_rank(self):
        agents = os.path.join(self.home, 'Library', 'LaunchAgents')
        os.makedirs(agents, exist_ok=True)
        open(os.path.join(agents, cli.AGENT + '.plist'), 'w').close()
        self.fake.auto['rules'] = {self.ids[C]: 'last'}
        self.fake.auto['own_lines'] = {self.ids[B]: {'session_at': 85.0}, self.ids[C]: {'weekly_at': 80.0,
                                                                                      'session_at': 70.0}}
        self.fake.auto['ranks'] = {self.ids[B]: 1, self.ids[C]: 2}
        out = self.run_cli('auto')[1].splitlines()
        self.assertEqual(out[1:], ['%s: rank 1, its own line 85%% 5-hour.' % B,
                                   '%s is used last, rank 2, its own lines 70%% 5-hour · 80%% weekly.' % C])
        self.fake.auto['own_lines'], self.fake.auto['ranks'] = {}, {}
        self.assertEqual(self.run_cli('auto')[1].splitlines()[1:], ['%s is used last.' % C])

    def test_the_third_order_in_auto_alone(self):
        self.fake.auto['strategy'] = 'stay-then-refills'
        out = self.run_cli('auto')[1].splitlines()[0]
        self.assertTrue(out.endswith(', staying on the account in use until a line, then using what refills first.'),
                        out)

    def test_the_demo_refuses_them_here(self):
        core.DEMO = True
        for args in (('line', 'maya@ivy.example', '--week', '80'), ('rank', 'maya@ivy.example', '1')):
            with self.subTest(args=args), self.assertRaises(core.SwitchError) as e:
                self.run_cli('auto', *args)
            self.assertIn('The demo has made-up accounts', str(e.exception))

    def test_the_help_lines(self):
        got = cli.command_help('auto') + '\n'
        self.assertTrue(got.endswith(NEW_HELP), got)
        self.assertIn('switchboard switch --best   move Claude Code now to the account auto-switch would pick, and '
                      'say why', cli.command_help('switch'))
        doc = cli.__doc__
        self.assertLess(doc.index('switchboard auto rank NAME'), doc.index('switchboard log'))
        for line in doc.splitlines():
            self.assertLessEqual(len(line), 120)


class TheBestSwitch(Three):
    def test_it_moves_where_auto_switch_would_land_and_says_why(self):
        code, out, err = self.run_both('switch', '--best')
        self.assertEqual((code, err), (0, ''))
        self.assertTrue(out.startswith('Claude Code is on %s now: it has room (80 points before its nearest line, '
                                       'weekly refills ' % B), out)
        self.assertEqual(self.fake.live().account_id, self.ids[B])

    def test_ranks_rules_and_lines_decide_it(self):
        self.fake.reading(self.ids[B], five=(10, core.parse_iso(iso_in(3600))),
                          week=(47, core.parse_iso(iso_in(2 * 86400))))
        for auto, want in (({'ranks': {'C': 1}}, C), ({'rules': {'B': 'out'}}, C), ({'rules': {'B': 'last'}}, C),
                           ({'own_lines': {'B': {'weekly_at': 50}}}, C), ({'strategy': 'most-room'}, B)):
            with self.subTest(auto=auto):
                self.fake.sign_in(A, 'org-' + A.replace('@', '-'))
                for k, v in auto.items():
                    self.fake.auto[k] = dict((self.ids[{'B': B, 'C': C}[x]], y) for x, y in v.items()) \
                        if isinstance(v, dict) else v
                self.assertEqual(self.run_both('switch', '--best')[0], 0)
                self.assertEqual(self.fake.live().account_id, self.ids[want])
                for k in auto:
                    self.fake.auto.pop(k)
                self.fake.auto['strategy'] = 'refills-first'

    def test_it_works_with_auto_switch_off(self):
        self.fake.auto['enabled'] = False
        self.assertEqual(self.run_both('switch', '--best')[0], 0)
        self.assertEqual(self.fake.live().account_id, self.ids[B])

    def test_with_none_it_says_when_the_first_refills_and_exits_1(self):
        for who in (B, C):
            hold(self.fake, who)
            self.fake.reading(self.ids[who], five=(100, core.parse_iso(iso_in(2 * 3600))),
                              week=(10, core.parse_iso(iso_in(2 * 86400))))
        with self.assertRaises(core.SwitchError) as e:
            self.run_both('switch', '--best')
        self.assertTrue(str(e.exception).startswith('No other account has room. First refill: '), str(e.exception))
        self.assertEqual(self.fake.live().account_id, self.ids[A])
        self.assertEqual([c for c in self.fake.calls if c[0] == 'switch'], [])

    def test_it_goes_through_the_running_page(self):
        view = cli.local_view()
        page = Page(view, answers={'/api/switch': {'ok': True}})
        self.addCleanup(page.stop)
        os.environ['SWITCHBOARD_PORT'] = str(page.server_address[1])
        code, out, _ = self.run_both('switch', '--best')
        self.assertEqual(code, 0, out)
        self.assertEqual([p[:2] for p in page.posts], [('/api/switch', {'email': B})])

    def test_any_other_word_is_still_refused(self):
        self.assertEqual(self.run_cli('switch', '--best', B), (2, 'switchboard switch [NAME|--best] [--json]\n'))


class TheBareSwitch(Three):
    def test_it_passes_over_an_account_at_its_line_when_another_has_room(self):
        self.fake.reading(self.ids[B], five=(10, core.parse_iso(iso_in(3600))),
                          week=(97, core.parse_iso(iso_in(2 * 86400))))
        self.assertEqual(self.run_cli('switch')[0], 0)
        self.assertEqual(self.fake.live().account_id, self.ids[C])

    def test_its_own_line_counts(self):
        self.fake.auto['own_lines'] = {self.ids[B]: {'session_at': 50.0}}
        self.fake.reading(self.ids[B], five=(55, core.parse_iso(iso_in(3600))),
                          week=(10, core.parse_iso(iso_in(2 * 86400))))
        self.assertEqual(self.run_cli('switch')[0], 0)
        self.assertEqual(self.fake.live().account_id, self.ids[C])

    def test_with_every_other_account_at_a_line_it_goes_to_the_next_as_before(self):
        for who in (B, C):
            self.fake.reading(self.ids[who], five=(95, core.parse_iso(iso_in(3600))),
                              week=(10, core.parse_iso(iso_in(2 * 86400))))
        self.assertEqual(self.run_cli('switch')[0], 0)
        self.assertEqual(self.fake.live().account_id, self.ids[B])


class TheViews(unittest.TestCase):
    def view(self, own=None, strategy=None):
        ids = dict((a['email'], 'id-%d' % a['slot']) for a in VIEW['accounts'])
        auto = dict(VIEW['auto'], own_lines=dict((ids[e], v) for e, v in (own or {}).items()))
        if strategy:
            auto['strategy'] = strategy
        return dict(VIEW, auto=auto, accounts=[dict(a, id=ids[a['email']]) for a in VIEW['accounts']])

    def test_the_terminal_reds_a_bar_at_the_accounts_own_line(self):
        fern = 'ana@fernhill.example'                                          # weekly at 94%
        row = lambda v: next(a for a in term.read(v)['accounts'] if a['email'] == fern)   # noqa: E731
        week = lambda v: next(x for x in row(v)['limits'] if x['label'] == 'weekly')     # noqa: E731
        self.assertFalse(week(self.view())['over'])
        self.assertTrue(week(self.view({fern: {'weekly_at': 90}}))['over'])
        self.assertFalse(week(self.view({fern: {'session_at': 60}}))['over'])
        self.assertEqual(term.read(self.view({}))['accounts'], term.read(VIEW)['accounts'])

    def test_the_menu_bar_does_too(self):
        fern = 'ana@fernhill.example'
        v = self.view({fern: {'weekly_at': 90}})
        a = next(x for x in v['accounts'] if x['email'] == fern)
        self.assertEqual(menubar.lines_of(a, v['auto']), (90.0, 90))
        self.assertTrue(next(x for x in menubar.limits(a, v['auto']) if x['kind'] == 'week')['over'])
        self.assertEqual(menubar.lines_of(a, VIEW['auto']), (90.0, 95.0))

    def test_status_json_gives_the_own_lines_only_when_set(self):
        fern, lab = 'ana@fernhill.example', 'ana@lab.example'
        doc = report.document(self.view({fern: {'weekly_at': 90}, lab: {'session_at': 80, 'weekly_at': 85}},
                                        strategy='stay-then-refills'), '0.0.0')
        by = dict((a['email'], a) for a in doc['accounts'])
        self.assertEqual((by[fern]['week_line'], 'five_hour_line' in by[fern]), (90, False))
        self.assertEqual((by[lab]['five_hour_line'], by[lab]['week_line']), (80, 85))
        for a in doc['accounts']:
            if a['email'] not in (fern, lab):
                self.assertFalse({'five_hour_line', 'week_line'} & set(a), a)
        self.assertEqual((doc['schema'], doc['auto_switch']['order']), (1, 'stay-then-refills'))
        self.assertEqual(report.document(self.view(), '0.0.0'), report.document(VIEW, '0.0.0'))

    def test_the_page_draws_each_account_against_its_own_lines(self):
        with open(os.path.join(HERE, '..', 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            html = f.read()
        self.assertIn("const u = a.trusted === false ? null : a.usage || null, L = accountLines(a);", html)
        self.assertIn("own = ((S.auto || {}).own_lines || {})[a && a.id] || {};", html)
        self.assertIn("['stay-then-refills', 'Stay, then refills first']", html)

    def test_the_docs_name_the_new_commands_and_fields(self):
        with open(os.path.join(HERE, '..', 'docs', 'commands.md'), encoding='utf-8') as f:
            doc = f.read()
        for words in ('`switchboard auto line NAME`', '`switchboard auto rank NAME N`', '`switchboard switch --best`',
                      '`five_hour_line`', '`stay-then-refills`'):
            self.assertIn(words, doc)
        self.assertLess(doc.index('`switchboard auto last NAME`'), doc.index('`switchboard auto line NAME`'))
        self.assertLess(doc.index('`switchboard auto line NAME`'), doc.index('`switchboard models`'))

    def test_the_sentences(self):
        self.assertEqual(autorules.line_said('maya@ivy.example', {}), 'maya@ivy.example is back on the shared lines.')
        self.assertEqual(autorules.account_words({}, None), '')
        self.assertEqual(json.loads(json.dumps(autorules.own_lines_of({'own_lines': {'x': {'weekly_at': 80}}}, 'x'))),
                         {'weekly_at': 80})


if __name__ == '__main__':
    unittest.main()
