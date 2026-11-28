import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# GET /api/keep's pace and its fallbacks in the server (server.keep_view, server.read_keep): a pass that fails as a
# whole is remembered like a good one (the section keeps what it had, or shows one plain line, and is read again at the
# usual pace, not at every look); only a part that failed or was not reached yet brings the next pass forward; a press in
# the section makes a new answer at once; `follows` says whether switchboard.room can be imported, and the page's picker
# words follow it. No Chrome.
# Run with:
#   SWITCHBOARD_HOME="$(mktemp -d)/data" taskpolicy -b python3 -m unittest discover -s tests -p 'test_keeprun_server.py'
import os
import sys
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)

from switchboard import core, keeprun, server  # noqa: E402
import json  # noqa: E402

from test_keeprun import NOW, Fixture, Runs, prompt, reply, result, stop, title  # noqa: E402


class Pace(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        saved = dict(server.KEEP)
        self.addCleanup(lambda: (server.KEEP.clear(), server.KEEP.update(saved)))
        server.KEEP.update(page=None, at=0, running=False, retry=False)
        self.clock = [1795776600.0]
        clock = mock.patch.object(server.time, 'time', lambda: self.clock[0])
        clock.start()
        self.addCleanup(clock.stop)
        self.passes = []

    def look(self, **kw):
        """One GET /api/keep; a pass it starts is counted, not run."""
        with mock.patch.object(server, 'read_keep', lambda: self.passes.append(self.clock[0])):
            got = server.keep_view(**kw)
        server.KEEP['running'] = False                     # the counted pass is over
        return got

    def failed_pass(self):
        with mock.patch.object(server.engine, 'auto_settings', lambda: {'enabled': True}), \
                mock.patch.object(keeprun, 'build', side_effect=TypeError('made up')):
            server.read_keep()

    def test_a_failed_pass_shows_one_line_and_waits_the_usual_time(self):
        self.failed_pass()
        got = self.look()
        self.assertEqual(got['state'], 'ready')
        self.assertEqual(got['problems'], [server.KEEP_FAILED])
        self.assertFalse(got['now']['switch'])
        self.assertEqual((got['steps'], got['causes'], got['runs']), ([], [], []))
        self.clock[0] += server.KEEP_ASKED + 5
        self.look()
        self.look(fresh=False)
        self.assertEqual(self.passes, [])                  # not at every look, not after half a minute
        self.clock[0] += server.KEEP_EVERY
        self.look()
        self.assertEqual(len(self.passes), 1)

    def test_a_failed_pass_keeps_the_last_answer_with_one_line(self):
        good = {'v': 1, 'at': self.clock[0], 'now': {'on': False, 'switch': True}, 'week': {'sentence': 'x'},
                'causes': [{'key': 'limit_model'}], 'steps': [], 'runs': [], 'problems': []}
        with mock.patch.object(server.engine, 'auto_settings', lambda: {'enabled': True}), \
                mock.patch.object(keeprun, 'build', lambda **kw: good):
            server.read_keep()
        self.failed_pass()
        self.failed_pass()
        got = self.look()
        self.assertEqual(got['causes'], [{'key': 'limit_model'}])
        self.assertEqual(got['problems'], [server.KEEP_FAILED])     # once, however many passes failed

    def test_a_problem_that_is_not_a_failed_part_does_not_bring_the_pass_forward(self):
        page = {'v': 1, 'week': {}, 'problems': ['Keep running’s plugin is older than this Switchboard.']}
        with mock.patch.object(server.engine, 'auto_settings', lambda: {'enabled': True}), \
                mock.patch.object(keeprun, 'build', lambda **kw: page):
            server.read_keep()
        self.clock[0] += server.KEEP_ASKED + 10
        self.look()
        self.assertEqual(self.passes, [])

    def test_a_part_not_reached_yet_brings_it_forward(self):
        page = {'v': 1, 'week': {}, 'problems': ['Part of your chat history is still being read (40 files to go).']}
        with mock.patch.object(server.engine, 'auto_settings', lambda: {'enabled': True}), \
                mock.patch.object(keeprun, 'build', lambda **kw: page):
            server.read_keep()
        self.clock[0] += 5
        self.look()
        self.assertEqual(self.passes, [])
        self.clock[0] += server.KEEP_ASKED
        self.look()
        self.assertEqual(len(self.passes), 1)

    def test_a_press_makes_a_new_answer_at_once(self):
        with mock.patch.object(server.engine, 'auto_settings', lambda: {'enabled': True}), \
                mock.patch.object(keeprun, 'build', lambda **kw: {'v': 1, 'week': {}, 'problems': []}):
            server.read_keep()
        self.clock[0] += 5
        self.look(fresh=True)
        self.assertEqual(self.passes, [])                  # "fresh" alone waits KEEP_ASKED
        self.look(fresh=True, press=True)
        self.assertEqual(len(self.passes), 1)

    def test_a_busy_mac_answers_from_the_last_pass(self):
        with mock.patch.object(server.engine, 'auto_settings', lambda: {'enabled': True}), \
                mock.patch.object(keeprun, 'build', lambda **kw: {'v': 1, 'week': {}, 'problems': []}):
            server.read_keep()
        with mock.patch.object(server, 'easing', lambda: True):
            self.clock[0] += server.KEEP_EVERY + 1
            got = self.look()
            self.assertEqual((got['state'], self.passes), ('ready', []))      # the week pauses the same way
            self.look(press=True)
            self.assertEqual(len(self.passes), 1)                           # a press still gets its answer
            self.clock[0] += server.USAGE_STALE
            self.look()
            self.assertEqual(len(self.passes), 2)                           # over an hour old: read anyway
        self.assertEqual(self.look()['state'], 'ready')

    def test_the_first_look_still_says_reading(self):
        self.assertEqual(self.look(), {'state': 'reading'})
        self.assertEqual(len(self.passes), 1)


class FailedPartsFromTheRealBuild(Fixture):
    """keep_retry reads keeprun's own problem lines: a part that failed brings the pass forward."""

    def test_a_failed_part_of_the_real_build_is_retried_sooner(self):
        with mock.patch.object(keeprun, 'runs_view', side_effect=RuntimeError('made up')):
            page = keeprun.build(now=1795776600.0)
        self.assertTrue(server.keep_retry(page), page['problems'])
        page = keeprun.build(now=1795776600.0)
        self.assertFalse(server.keep_retry(page), page['problems'])


ROOM_WORDS = 'Automatic switching is on.'     # with the room part: what decides the move, and nothing more


class WithoutKeepRunning(Fixture):
    """The 11 AM build has no switchboard.running: no switch, no "Turn on" step, no error, and no sentence that speaks
    of Keep running as a thing to turn on; the model-limit row says what the build does without it."""

    def setUp(self):
        super().setUp()
        # neither part importable, whether or not its file is in this tree (a None in sys.modules makes the import fail)
        gone = mock.patch.dict(sys.modules, {'switchboard.running': None, 'switchboard.room': None})
        gone.start()
        self.addCleanup(gone.stop)
        import switchboard                                 # and no attribute left by an earlier import of either
        attrs = mock.patch.dict(switchboard.__dict__)
        attrs.start()
        self.addCleanup(attrs.stop)
        for name in ('running', 'room'):
            switchboard.__dict__.pop(name, None)
        self.chat('m' * 8 + '-0001', [reply(NOW - 7300), stop(NOW - 7200, 'limit_model')])

    def check(self, r):
        self.assertFalse(r['now']['switch'])
        self.assertNotIn('on', [x['key'] for x in r['steps']])
        words = json.dumps(r, ensure_ascii=False)
        self.assertNotIn('Keep running', words)
        self.assertNotIn('refused request', words)
        return [c for c in r['causes'] if c['key'] == 'limit_model'][0]

    def test_neither_part_is_there(self):
        r = keeprun.build(now=NOW)
        self.assertEqual(r['problems'], [])
        row = self.check(r)
        self.assertEqual((row['doing'], row['handled'], row['step']),
                         ('A request stopped by the limit ends the turn.', None, None))
        row = self.check(keeprun.sample(NOW))
        self.assertEqual((row['handled'], row['step']), (None, None))   # 'handled' is 'yes' only when measured

    def test_the_room_part_is_there(self):
        with mock.patch.dict(sys.modules, {'switchboard.room': types.ModuleType('switchboard.room')}):
            row = self.check(keeprun.build(now=NOW))
            self.assertEqual((row['doing'], row['handled'], row['step']), (ROOM_WORDS, None, None))
            row = self.check(keeprun.sample(NOW))
            self.assertEqual((row['doing'], row['handled'], row['step']), (ROOM_WORDS, None, None))

    def test_the_demo_has_no_plan_line_without_keep_running(self):
        # the demo is what screenshots are made from: without the plugin's module it shows what such a build shows
        for room in (None, types.ModuleType('switchboard.room')):
            with mock.patch.dict(sys.modules, {'switchboard.room': room}):
                d = keeprun.sample(NOW)
            self.assertIsNone(d['now']['words'])
            self.assertIsNone(d['now']['plan_at'])
            self.assertNotIn('Chats run on', json.dumps(d, ensure_ascii=False))
        fake = types.ModuleType('switchboard.running')
        fake.status = lambda: {'on': False, 'since': None, 'plugin': None, 'problems': []}
        with mock.patch.dict(sys.modules, {'switchboard.running': fake}):
            self.assertTrue(keeprun.sample(NOW)['now']['words'])     # with the module the made-up plan stays

    def test_with_keep_running_the_words_say_what_it_does(self):
        fake = types.ModuleType('switchboard.running')
        fake.status = lambda: {'on': False, 'since': None, 'plugin': None, 'problems': []}
        with mock.patch.dict(sys.modules, {'switchboard.running': fake}):
            r = keeprun.build(now=NOW)
            s = keeprun.sample(NOW)
        for page in (r, s):
            self.assertTrue(page['now']['switch'])
            step = [x for x in page['steps'] if x['key'] == 'on'][0]
            self.assertIn('When a model’s usage limit is reached on the account in use, Keep running sends the request '
                          'to the next model with room, and it continues a chat that stopped.', step['why'])
            self.assertNotIn('refused request', json.dumps(page, ensure_ascii=False))


class OnlyWhatIsMeasured(Fixture):
    """The reader's must-fix list of 15:06: no sentence says more than was counted."""

    def test_m1_chats_cut_off_are_a_step_not_a_14_day_row(self):
        for i, c in enumerate('xyz'):
            self.chat(c * 8 + '-0000-0000-0000-00000000000%d' % i,
                      [title('Chat %s' % c), prompt(NOW - 1500), reply(NOW - 1400, tools=('Bash',)),
                       result(NOW - 1390 + i * 5, shutdown=True)], age=1390 - i * 5)
        r = keeprun.build(now=NOW)
        self.assertIn('cut', [x['key'] for x in r['steps']])
        self.assertEqual([c for c in r['causes'] if c['key'] == 'restart'], [])
        self.assertNotIn('restart', [c['key'] for c in keeprun.sample(NOW)['causes']])

    def test_m3_the_model_limit_row_says_what_automatic_switching_is(self):
        for has_switch in (False, True):
            self.assertEqual(keeprun._doing('limit_model', False, has_switch, False, False, True),
                             ('Automatic switching is off.', 'auto'))
            self.assertEqual(keeprun._doing('limit_model', False, has_switch, False, True, True)[0],
                             'Automatic switching is on.')
        self.assertEqual(keeprun._doing('limit_model', False, False, False, True, False),
                         ('A request stopped by the limit ends the turn.', None))

    def test_m4_a_row_names_a_model_only_when_every_stop_names_it(self):
        t0 = NOW - 7200
        opus = stop(t0, 'limit_model')
        opus['message']['content'][0]['text'] = "You've hit your Opus limit · resets Nov 30 at 1pm"
        self.chat('a' * 8 + '-3010', [reply(t0 - 10), opus, reply(t0 + 60), stop(t0 + 120, 'limit_model')])
        names = [c['name'] for c in keeprun.build(now=NOW)['causes'] if c['key'] == 'limit_model']
        self.assertEqual(names, ['A model used up on the account in use'])

    def test_m5_the_lid_step_says_only_what_was_read(self):
        for x in keeprun.sample(NOW)['steps']:
            self.assertNotIn('do not come back', x.get('why') or '')
        with open(os.path.join(HERE, '..', 'switchboard', 'keeprun.py'), encoding='utf-8') as f:
            self.assertNotIn('do not come back', f.read())

    def test_m2_the_switch_lines_are_log_lines_not_a_row_of_stops(self):
        with open(os.path.join(HERE, '..', 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            html = f.read()
        section = html[html.index('const kpN = '):html.index('// ---------- Your week, under the accounts')]
        for gone in ('Not stops', 'none of them stopped', 'Chats re-checked', 'data-cause="rechecked"'):
            self.assertNotIn(gone, section)
        self.assertIn("'VS Code logged ' + kpN(x.slow) + ' “did not finish starting” ' + (x.slow === 1 ? 'line' : 'lines') + "
                      "' within 3 minutes after the switch to ' + esc(x.to || '') + ' at ' + esc(kpWhen(x.at)) + '.'",
                      section)
        rows = section[section.index("'<h3 class=\"kp-h\">' + (now.switch"):]
        self.assertLess(rows.index('</ol>'), rows.index('VS Code logged '))   # after the list of what stopped


class ShouldFixes(Fixture):
    """The reader's should-fix list of 15:06, items 6 to 16, each as measured."""

    def test_s6_five_hour_and_weekly_rows_say_only_that_switching_is_on(self):
        for key in ('limit_5hour', 'limit_weekly'):
            self.assertEqual(keeprun._doing(key, False, False, False, True), ('Automatic switching is on.', None))

    def test_s7_the_put_back_sentence_only_when_stay_signed_in_is_read_as_on(self):
        self.assertEqual(keeprun._doing('signed_out', False, False, False, True, stay_on=None), ('', None))
        self.assertEqual(keeprun._doing('signed_out', False, False, False, True, stay_on=False), ('', None))
        self.assertTrue(keeprun._doing('signed_out', False, False, False, True, stay_on=True)[0]
                        .startswith('Switchboard puts back a sign-in that went missing'))
        self.chat('s' * 8 + '-0001', [reply(NOW - 7300), stop(NOW - 7200, 'signed_out')])
        with mock.patch.object(core, 'settings', lambda: {'auto_policy': {'stay_signed_in': False}}):
            row = [c for c in keeprun.build(now=NOW, lines=[], auto_on=True, awake_on=True)['causes']
                   if c['key'] == 'signed_out'][0]
        self.assertEqual(row['doing'], '')
        with mock.patch.object(core, 'settings', lambda: {}):                   # a missing key means on
            row = [c for c in keeprun.build(now=NOW, lines=[], auto_on=True, awake_on=True)['causes']
                   if c['key'] == 'signed_out'][0]
        self.assertTrue(row['doing'].startswith('Switchboard puts back'))

    def test_s11_a_chat_waits_for_you_only_while_its_program_is_known_to_run(self):
        sid = lambda c: c * 8 + '-1111-2222-3333-444455556666'  # noqa: E731
        for c in 'ab':
            self.chat(sid(c), [title('Asks %s' % c), prompt(NOW - 1500), reply(NOW - 1400, tools=('AskUserQuestion',))],
                      age=1400)
        # 'a' runs; 'b' is not found in ps while other Claude Code programs run: not known to run, so not listed
        self.cmds['ps'] = ('/x/anthropic.claude-code-2.1.332/resources/native-binary/claude --resume=%s\n'
                           '/x/anthropic.claude-code-2.1.332/resources/native-binary/claude --resume=%s'
                           % (sid('a'), sid('z')))
        r = keeprun.build(now=NOW, lines=[], auto_on=True, awake_on=True)
        self.assertEqual([w['title'] for w in r['waiting']], ['Asks a'])

    def test_s12_the_wait_says_how_many_it_is_the_middle_of(self):
        t0 = NOW - 7200
        self.chat('w' * 8 + '-0001', [reply(t0 - 10), stop(t0, 'offline'), reply(t0 + 600),
                                     stop(t0 + 1000, 'offline'), prompt(t0 + 1100), reply(t0 + 1200)])
        row = [c for c in keeprun.build(now=NOW, lines=[], auto_on=True, awake_on=True)['causes']
               if c['key'] == 'offline'][0]
        self.assertEqual((row['went_on_min'], row['went_on_n']), (10, 1))    # the typed one is not in it
        with open(os.path.join(HERE, '..', 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            html = f.read()
        self.assertIn("kpMid(c.went_on_min, c.went_on_n)", html)
        self.assertIn("' later for the ' + (n === 1 ? 'one that went on by itself' : n + ' that went on by themselves')",
                      html)

    def test_s13_steps_keep_only_the_facts_read(self):
        with open(os.path.join(HERE, '..', 'switchboard', 'keeprun.py'), encoding='utf-8') as f:
            text = f.read()
        for claim in ('billed to that key', 'stay in Claude Code', "stay in '"):
            self.assertNotIn(claim, text)
        with open(self.settings, 'w') as f:
            json.dump({'env': {'ANTHROPIC_API_KEY': 'made-up-key'}}, f)
        step = [x for x in keeprun.build(now=NOW, lines=[], auto_on=True, awake_on=True)['steps']
                if x['key'] == 'api_key'][0]
        self.assertEqual(step['why'], 'The value is not read into this page.')

    def test_s14_a_retry_she_typed_between_two_errors_is_two_stops(self):
        t0 = NOW - 7200
        self.chat('t' * 8 + '-0001', [reply(t0 - 10), stop(t0, 'offline'), prompt(t0 + 60), stop(t0 + 90, 'offline'),
                                     stop(t0 + 95, 'offline')])
        row = [c for c in keeprun.build(now=NOW, lines=[], auto_on=True, awake_on=True)['causes']
               if c['key'] == 'offline'][0]
        self.assertEqual(row['count'], 2)            # the typed retry splits them; the record written twice is one

    def test_s15_the_cache_keeps_no_long_title_label_or_run_name(self):
        s = 'j' * 8 + '-0050'
        self.chat(s, [title('T' * 100), reply(NOW - 9500)])
        t0 = NOW - 8000
        Runs.run_folder(self, s, 'wf_one', [('a1', 'L' * 100, 'failed', [reply(t0), stop(t0 + 10, 'offline')], 'opus')],
                        name='N' * 100)
        Runs.run_folder(self, s, 'wf_two', [('a2', 'b', 'started', [reply(t0 + 600)], 'opus')], name='N' * 100,
                        start=NOW - 7000)
        r = keeprun.build(now=NOW, lines=[], auto_on=True, awake_on=True)
        with open(os.path.join(self.data, keeprun.CACHE_FILE), encoding='utf-8') as f:
            cache = f.read()
        for c in 'TLN':
            self.assertNotIn(c * 61, cache)
        self.assertTrue(r['runs'] == [] or r['runs'][0]['later_run'] is True, r['runs'])   # whole names still compared
        self.assertNotIn('N' * 61, json.dumps(r))

    def test_s16_no_plan_line_from_a_real_room_json_without_keep_running(self):
        os.makedirs(os.path.join(self.data, 'running'), exist_ok=True)
        with open(os.path.join(self.data, 'running', 'room.json'), 'w') as f:
            json.dump({'v': 1, 'at': NOW - 60, 'words': 'Fable is used up on a@example.com until Mon 1:00 PM. Chats '
                                                        'run on Opus 5.5 until then.'}, f)
        import switchboard
        with mock.patch.dict(sys.modules, {'switchboard.running': None}), mock.patch.dict(switchboard.__dict__):
            switchboard.__dict__.pop('running', None)
            r = keeprun.build(now=NOW, lines=[], auto_on=True, awake_on=True)
        self.assertEqual((r['now']['words'], r['now']['plan_at']), (None, None))
        fake = types.ModuleType('switchboard.running')
        fake.status = lambda: {'on': True, 'since': NOW, 'plugin': '0.1.0', 'problems': []}
        with mock.patch.dict(sys.modules, {'switchboard.running': fake}):
            self.assertTrue(keeprun.build(now=NOW, lines=[], auto_on=True, awake_on=True)['now']['words'])

    def test_s8_every_account_out_says_the_policy_without_now(self):
        lines = [{'t': NOW - 20000, 'kind': 'limit', 'text': 'Every account is out. Back at 12:19 PM.'}]
        for room, words in ((None, 'Switchboard says every account is out when the asked model is used up.'),
                            (types.ModuleType('switchboard.room'),
                             'Switchboard says every account is out only when no account has room on its 5-hour and '
                             'weekly windows.')):
            import switchboard
            with mock.patch.dict(sys.modules, {'switchboard.room': room}), mock.patch.dict(switchboard.__dict__):
                switchboard.__dict__.pop('room', None)
                row = [c for c in keeprun.build(now=NOW, lines=lines, auto_on=True, awake_on=True)['causes']
                       if c['key'] == 'every_out'][0]
                d = keeprun.sample(NOW)
            self.assertEqual(row['doing'], words)
            self.assertEqual([c['doing'] for c in d['causes'] if c['key'] == 'every_out'], [words])


class TheRunLines(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(HERE, '..', 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            self.html = f.read()

    def test_s9_the_page_has_keeprun_s_words_for_each_why(self):
        line = self.html[self.html.index('const KP_WHY = '):]
        line = line[:line.index('};') + 2]
        for k, words in keeprun.WHY_WORDS.items():
            if k not in ('stopped', 'unknown'):             # never in a run's why since page-true's first pass
                self.assertIn("%s: '%s'" % (k, words), line)
        self.assertNotIn("k.replace(/_/g, ' ')", self.html)    # never a raw key

    def test_s10_a_run_says_no_later_run_started_without_a_count(self):
        self.assertNotIn("' not run again'", self.html)
        self.assertIn("'No later run of the same workflow started'", self.html)


class TheHeading(unittest.TestCase):
    def test_s17_with_no_switch_the_section_is_named_for_what_it_shows(self):
        with open(os.path.join(HERE, '..', 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            html = f.read()
        self.assertIn("(now.switch ? 'Keep running' : 'What stopped your work')", html)
        self.assertIn("(now.switch ? 'What stopped your work, last 14 days' : 'Last 14 days')", html)


class Follows(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)

    def test_follows_says_whether_the_room_part_can_be_imported(self):
        sys.modules.pop('switchboard.room', None)
        here = os.path.exists(os.path.join(HERE, '..', 'switchboard', 'room.py'))
        self.assertEqual(server.keep_follows(), here)
        fake = types.ModuleType('switchboard.room')
        with mock.patch.dict(sys.modules, {'switchboard.room': fake}):
            self.assertTrue(server.keep_follows())
            core.DEMO = True
            self.assertTrue(server.keep_view()['follows'])
        if not here:
            self.assertFalse(server.keep_view()['follows'])

    def test_the_picker_words_follow_it(self):
        with open(os.path.join(HERE, '..', 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            html = f.read()
        self.assertIn("const follows = !!(keepData || {}).follows;", html)
        self.assertIn("(follows ? 'The models your chats use' : 'None reported yet')", html)
        self.assertIn("(counted.length || !follows ? '' : '<span class=\"muted\" data-k=\"model-none\">None ticked: "
                      "the models your chats use</span>')", html)


class ThePresses(unittest.TestCase):
    def test_every_press_in_the_section_asks_for_the_new_answer(self):
        with open(os.path.join(HERE, '..', 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            html = f.read()
        # the steps' buttons go through keepDo, which runs the named action and then asks again
        self.assertIn('data-do="keepDo" data-act="\' + KP_DO[a.post] + \'"', html)
        self.assertIn("keepDo(ds, b) { if (ds.act === 'keepDo' || !actions[ds.act]) return; "
                      "actions[ds.act]({on: ds.on, zone: 'keep'}, b); keepAgain(); }", html)
        self.assertIn("getJSON('/api/keep' + (n ? '' : '?fresh=press'))", html)
        self.assertIn(".then(did(zone || 'keep')).then(() => keepAgain())", html)
        self.assertNotIn('.kp-rows .st.yes { color: var(--accent)', html)

    def test_the_drawing_follows_the_exact_fields(self):
        # lanes/page/FOR-PAGE-PORT.md: no time lost anywhere; "the next reply came about N minutes later" from
        # went_on_min (second pass); "Handled" only for 'yes', else the step's words or nothing; a row's note; a run
        # with none left to run again says a later run started (never "Run again"); an absent field draws nothing
        with open(os.path.join(HERE, '..', 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            html = f.read()
        section = html[html.index('const kpN = '):html.index('// ---------- Your week, under the accounts')]
        for gone in ('lost_min', 'kpLost', 'hours_lost', "'. Run again'"):
            self.assertNotIn(gone, html)
        kp_why = html[html.index('const KP_WHY = '):]
        kp_why = kp_why[:kp_why.index('\n')]
        self.assertNotIn('stopped', kp_why)
        self.assertNotIn('unknown', kp_why)
        self.assertIn("'the next reply came about ' + counted(Math.round(min), 'minute') +", html)
        self.assertIn("st = c.handled === 'yes' ? 'Handled' : (c.step && KP_STEP[c.step]) || ''", section)
        self.assertIn("[kpN(c.count), c.note || '', kpMid(c.went_on_min, c.went_on_n)]", section)
        for gone in ('Not handled', 'Partly handled', 'went on after about'):
            self.assertNotIn(gone, section)
        self.assertIn('kpMid(c.went_on_min, c.went_on_n)', section)
        self.assertIn("r.later_run ? 'A later run of the same workflow started'", section)
        for raw in ("' + c.count", "' + r.dead", "' + r.agents", "' + x.slow + '"):
            self.assertNotIn(raw, section)                 # counts only through kpN: never "undefined"


if __name__ == '__main__':
    unittest.main()
