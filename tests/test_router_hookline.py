import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The hook line: what a Claude Code or Codex chat is told of the person's routes, from the plan file alone.
#
# - The private rule (hookline._resolve) answers every reason code of the router contract's section 2.
# - line() at a chat's start: the routes as short lines and one line on how, for each agent; nothing for a plan that
#   is missing, old or off, or that has no route for this agent; never over 600 characters.
# - line() at a prompt: one line only when a fact the chat was told has changed (room lost or back, routes changed,
#   routing off), else nothing.
# - main(): reads only hook_event_name, session_id and source; a made-up marker in "prompt" is in nothing it prints or
#   writes; what a chat was told is a hash in router/seen/<16 hex>.json, dropped after 7 days; always exit 0.
# - The command a hooks file runs, through /bin/sh, prints the documented JSON, and ends itself after 3 seconds.
import copy
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard.router import adapter_words as W  # noqa: E402
from switchboard.router import hookline  # noqa: E402

NOW = 1764239000.0
MARKER = 'ZQX-MARKER-7f3a1c'


def make_plan(**over):
    p = {
        'v': 1, 'made_at': NOW - 10, 'good_for_s': 180, 'on': True, 'dry': False, 'by': 'switchboard test',
        'order': ['claude', 'codex'],
        'agents': {
            'claude': {'can': True, 'why_not': None, 'room': 'room', 'back_at': None, 'at_lines': False,
                       'line': {'five_hour': 90, 'week': 95}, 'in_use': '1'},
            'codex': {'can': True, 'why_not': None, 'room': 'room', 'back_at': None, 'at_lines': False},
        },
        'routes': {
            'find': [{'agent': 'claude', 'model': 'haiku', 'effort': 'medium'},
                     {'agent': 'codex', 'model': 'gpt-6-luna', 'effort': 'medium'}],
            'review': [{'agent': 'codex', 'model': 'gpt-6.1-sol', 'effort': 'high'}],
        },
        'why': {'find': 'your route for lookups', 'review': 'your route for reviews'},
        'helpers': {'force': False}, 'chat': {'effort': False, 'model_on_first_turn': False},
        'hand': {'on': True, 'write': False, 'max_s': 900}, 'carry_on': False,
        'kinds': {'order': ['build', 'fix', 'review', 'plan', 'find', 'write', 'other'], 'light': 'find', 'cut': 300,
                  'table': {'build': ['add'], 'fix': ['fix']}},
        'types': {'Explore': 'find', 'Plan': 'plan'},
    }
    for k, v in over.items():
        p[k] = v
    return p


def agents(**rooms):
    """agents with room words changed: agents(codex={'room': 'out', 'back_at': 5.0})."""
    a = copy.deepcopy(make_plan()['agents'])
    for name, change in rooms.items():
        a.setdefault(name, {'can': True, 'why_not': None, 'room': 'room', 'back_at': None, 'at_lines': False})
        a[name].update(change)
    return a


class Resolve(unittest.TestCase):
    def r(self, plan, kind='find', here='claude', now=NOW):
        return hookline._resolve(plan, kind, here, now)

    def test_unusable_plans(self):
        self.assertEqual(self.r(None)['reason'], 'no_plan')
        self.assertEqual(self.r('not a plan')['reason'], 'no_plan')
        self.assertEqual(self.r(make_plan(), now=NOW + 1000)['reason'], 'old_plan')
        self.assertEqual(self.r(make_plan(v=2))['reason'], 'no_plan')
        self.assertEqual(self.r(make_plan(on=False))['reason'], 'off')
        self.assertEqual(self.r(make_plan(), kind='write')['reason'], 'no_route')
        for got in (self.r(None), self.r(make_plan(on=False))):
            self.assertEqual(got['do'], 'nothing')
            self.assertEqual(got['passed'], [])

    def test_the_first_choice_here_is_a_model(self):
        got = self.r(make_plan())
        self.assertEqual((got['do'], got['model'], got['effort'], got['kind']), ('model', 'haiku', 'medium', 'find'))
        self.assertEqual(got['why'], 'your route for lookups')
        self.assertEqual(got['passed'], [])

    def test_another_agent_is_a_hand(self):
        got = self.r(make_plan(), kind='review', here='claude')
        self.assertEqual((got['do'], got['to'], got['model'], got['effort']), ('hand', 'codex', 'gpt-6.1-sol', 'high'))

    def test_hand_off_passes_another_agent(self):
        got = self.r(make_plan(hand={'on': False, 'write': False, 'max_s': 900}), kind='find', here='codex')
        self.assertEqual(got['passed'], [['claude', 'hand_off']])
        self.assertEqual((got['do'], got['model']), ('model', 'gpt-6-luna'))

    def test_no_model_and_no_effort_stays(self):
        p = make_plan(routes={'find': [{'agent': 'claude', 'model': None, 'effort': None}]})
        self.assertEqual((self.r(p)['do'], self.r(p)['reason']), ('nothing', 'stays'))

    def test_every_pass_code(self):
        for code in ('not_installed', 'signed_out', 'too_old', 'never', 'not_added'):
            with self.subTest(code=code):
                got = self.r(make_plan(agents=agents(claude={'can': False, 'why_not': code})))
                self.assertEqual(got['passed'], [['claude', code]])
                self.assertEqual(got['do'], 'hand')
        missing = make_plan(agents={'codex': make_plan()['agents']['codex']})
        self.assertEqual(self.r(missing)['passed'], [['claude', 'not_added']])
        out = self.r(make_plan(agents=agents(claude={'room': 'out'})))
        self.assertEqual(out['passed'], [['claude', 'out']])

    def test_unknown_room_is_taken_only_when_nothing_later_has_room(self):
        got = self.r(make_plan(agents=agents(claude={'room': 'unknown'})))
        self.assertEqual(got['passed'], [['claude', 'unknown']])
        self.assertEqual(got['do'], 'hand')
        got = self.r(make_plan(agents=agents(claude={'room': 'unknown'}, codex={'room': 'out'})))
        self.assertEqual((got['do'], got['model']), ('model', 'haiku'))
        self.assertEqual(got['passed'], [])

    def test_all_passed_names_the_earliest_known_back_time(self):
        p = make_plan(agents=agents(claude={'room': 'out', 'back_at': NOW + 900},
                                    codex={'room': 'out', 'back_at': NOW + 300}))
        got = self.r(p)
        self.assertEqual((got['do'], got['reason'], got['back_at']), ('nothing', 'all_passed', NOW + 300))
        self.assertEqual(got['passed'], [['claude', 'out'], ['codex', 'out']])
        p = make_plan(agents=agents(claude={'room': 'out'}, codex={'room': 'out'}))
        self.assertIsNone(self.r(p)['back_at'])


class Fixtures(unittest.TestCase):
    """The private rule agrees with every resolve case of tests/router_fixtures.json (a byte-for-byte copy of the
    core's file), so the line says what every other adapter would do."""

    def test_every_case(self):
        with open(os.path.join(HERE, 'router_fixtures.json'), encoding='utf-8') as f:
            cases = [c for c in json.load(f) if 'plan' in c]
        self.assertGreater(len(cases), 50)
        for c in cases:
            with self.subTest(name=c['name']):
                got = hookline._resolve(c['plan'], c['kind'], c['here'], c['now'], c.get('where'))
                self.assertEqual(got, c['want'])

    def test_the_line_looks_at_no_default(self):
        with open(os.path.join(HERE, 'router_fixtures.json'), encoding='utf-8') as f:
            cases = [c for c in json.load(f) if 'plan' in c and c['want'].get('reason') == 'default']
        self.assertGreater(len(cases), 5)
        for c in cases:
            with self.subTest(name=c['name']):
                self.assertNotEqual(hookline._resolve(c['plan'], c['kind'], c['here'], c['now'])['reason'], 'default')
                self.assertEqual(hookline.kind_lines(c['here'], c['plan'], c['now']),
                                 hookline.kind_lines(c['here'], dict(c['plan'], defaults={}), c['now']))


class StartLine(unittest.TestCase):
    def line(self, plan, agent, event='SessionStart'):
        return hookline.line(agent, event, plan, None, NOW)

    def test_codex_names_its_helper_files(self):
        got = self.line(make_plan(hand={'on': False, 'write': False, 'max_s': 900}), 'codex')
        self.assertIn('Reviews: the helper switchboard-review (model gpt-6.1-sol and effort high).', got)
        self.assertIn('Lookups: the helper switchboard-find (model gpt-6-luna and effort medium).', got)
        self.assertTrue(got.endswith(W.HOW_CODEX), got)

    def test_claude_names_the_model_for_a_helper(self):
        # Claude Code's Agent call takes a model and no effort, so the line names only the model of a helper. A route
        # to another app may name its effort: the person starts it there.
        got = self.line(make_plan(), 'claude')
        self.assertIn('Lookups: a helper with model haiku.', got)
        self.assertNotIn('haiku and effort', got)
        self.assertIn('Reviews: Codex, with model gpt-6.1-sol and effort high.', got)
        self.assertTrue(got.endswith(W.HOW_CLAUDE + W.HOW_HAND), got)
        self.assertNotIn('effort', W.HOW_CLAUDE)

    def test_claude_says_nothing_of_a_route_that_names_only_an_effort(self):
        p = make_plan(routes={'find': [{'agent': 'claude', 'model': None, 'effort': 'low'}],
                              'review': [{'agent': 'claude', 'model': 'sonnet', 'effort': 'high'}]})
        got = self.line(p, 'claude')
        self.assertNotIn('Lookups', got)
        self.assertIn('Reviews: a helper with model sonnet.', got)

    def test_the_first_prompt_of_a_chat_gets_the_same(self):
        p = make_plan()
        self.assertEqual(self.line(p, 'claude', 'UserPromptSubmit'), self.line(p, 'claude'))

    def test_nothing_for_a_plan_that_is_not_usable(self):
        for agent in ('claude', 'codex'):
            for p in (None, make_plan(made_at=NOW - 181), make_plan(v=2), make_plan(on=False),
                      make_plan(dry=True), make_plan(routes={})):
                with self.subTest(agent=agent, plan=p and (p['v'], p['made_at'], p['on'], p['dry'], p['routes'])):
                    self.assertEqual(self.line(p, agent), '')

    def test_every_reason_code_for_both_agents(self):
        stays = make_plan(routes={'find': [{'agent': 'claude', 'model': None, 'effort': None},
                                           {'agent': 'codex', 'model': None, 'effort': None}]},
                          hand={'on': False, 'write': False, 'max_s': 900})
        hand_off = make_plan(routes={'review': [{'agent': 'codex', 'model': 'gpt-6.1-sol', 'effort': 'high'}]},
                             hand={'on': False, 'write': False, 'max_s': 900})
        all_out = make_plan(agents=agents(claude={'room': 'out', 'back_at': NOW + 3600},
                                          codex={'room': 'out', 'back_at': None}))
        for agent in ('claude', 'codex'):
            with self.subTest(agent=agent):
                self.assertEqual(self.line(stays, agent), '')                     # stays: nothing to say
                self.assertEqual(self.line(hand_off, 'claude'), '')               # a route only for another app
                got = self.line(all_out, agent)
                self.assertIn('Lookups: nothing on the route can take it now, so it stays here. Room is back at ', got)
                self.assertIn('Reviews: nothing on the route can take it now, so it stays here.', got)
        for code in ('not_installed', 'signed_out', 'too_old', 'never', 'not_added', 'out'):
            change = {'can': False, 'why_not': code} if code != 'out' else {'room': 'out'}
            p = make_plan(agents=agents(claude=change))
            with self.subTest(code=code):
                self.assertIn('Lookups: Codex, with model gpt-6-luna and effort medium.', self.line(p, 'claude'))
                self.assertIn('Lookups: the helper switchboard-find (model gpt-6-luna and effort medium).',
                              self.line(p, 'codex'))
        unknown = make_plan(agents=agents(claude={'room': 'unknown'}, codex={'room': 'out'}))
        self.assertIn('Lookups: a helper with model haiku. Its room is not known.',
                      self.line(unknown, 'claude'))

    def test_never_over_600_characters_and_at_most_five_lines(self):
        long = 'm' + 'x' * 63
        routes = dict((k, [{'agent': 'codex', 'model': long, 'effort': 'medium'}])
                      for k in ('build', 'fix', 'review', 'plan', 'find', 'write', 'other'))
        for agent in ('claude', 'codex'):
            got = self.line(make_plan(routes=routes), agent)
            self.assertLessEqual(len(got), 600)
            self.assertLessEqual(len(got.splitlines()), 5)
            self.assertIn('Building', got)


class ChangeLine(unittest.TestCase):
    def told(self, plan, agent='codex'):
        return hookline.told(agent, plan, NOW)

    def at_prompt(self, plan, seen, agent='codex'):
        return hookline.line(agent, 'UserPromptSubmit', plan, seen, NOW)

    def test_nothing_when_nothing_changed(self):
        p = make_plan()
        self.assertEqual(self.at_prompt(p, self.told(p)), '')
        q = make_plan(made_at=NOW - 5)                                   # a newer plan, same facts
        self.assertEqual(self.at_prompt(q, self.told(p)), '')

    def test_room_lost_and_back(self):
        p = make_plan()
        lost = make_plan(agents=agents(codex={'room': 'out', 'back_at': NOW + 600}))
        got = self.at_prompt(lost, self.told(p), agent='claude')
        self.assertTrue(got.startswith('Codex has no room now. Now: '), got)
        self.assertNotIn('\n', got)
        got = self.at_prompt(p, self.told(lost), agent='claude')
        self.assertTrue(got.startswith('Codex has room again. Now: '), got)

    def test_routes_changed(self):
        p = make_plan()
        q = make_plan(routes={'find': [{'agent': 'codex', 'model': 'gpt-6-luna', 'effort': 'low'}]})
        got = self.at_prompt(q, self.told(p))
        self.assertEqual(got, "Switchboard's routes changed. Now: Lookups: the helper switchboard-find "
                              "(model gpt-6-luna and effort low).")

    def test_routing_went_off_said_once(self):
        p = make_plan()
        off = make_plan(on=False)
        self.assertEqual(self.at_prompt(off, self.told(p)), W.OFF)
        self.assertEqual(self.at_prompt(off, self.told(off)), '')

    def test_no_routes_for_this_agent_any_more(self):
        p = make_plan()
        q = make_plan(routes={'review': [{'agent': 'claude', 'model': 'sonnet', 'effort': None}]},
                      hand={'on': False, 'write': False, 'max_s': 900})
        self.assertEqual(self.at_prompt(q, self.told(p)), W.NONE_NOW)

    def test_a_stale_plan_says_nothing(self):
        p = make_plan()
        self.assertEqual(self.at_prompt(make_plan(made_at=NOW - 500), self.told(p)), '')
        self.assertEqual(self.at_prompt(None, self.told(p)), '')

    def test_one_line_within_600(self):
        long = 'm' + 'x' * 63
        routes = dict((k, [{'agent': 'codex', 'model': long, 'effort': 'medium'}])
                      for k in ('build', 'fix', 'review', 'plan', 'find', 'write', 'other'))
        got = self.at_prompt(make_plan(routes=routes), self.told(make_plan()))
        self.assertLessEqual(len(got), 600)
        self.assertNotIn('\n', got)
        self.assertTrue(got.startswith(W.CHANGED))

    def test_the_record_holds_hashes_only(self):
        rec = self.told(make_plan())
        text = json.dumps(rec)
        for word in ('gpt-6-luna', 'haiku', 'Lookups', 'room'):
            self.assertNotIn(word, text)


class Main(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp(prefix='hookline-data-')
        self.env = {'SWITCHBOARD_HOME': self.data, 'HOME': os.environ['HOME']}

    def put_plan(self, plan):
        os.makedirs(os.path.join(self.data, 'router'), exist_ok=True)
        with open(os.path.join(self.data, 'router', 'plan.json'), 'w', encoding='utf-8') as f:
            json.dump(plan, f)

    def run_main(self, agent, event, session='s-1', now=NOW, extra=None, raw=None):
        inp = {'hook_event_name': event, 'session_id': session, 'cwd': '/tmp/folder-x', 'model': 'm',
               'transcript_path': '/tmp/t.jsonl', 'prompt': 'please ' + MARKER + ' fix the bug'}
        if event == 'SessionStart':
            inp['source'] = 'startup'
            del inp['prompt']
            inp['initial'] = MARKER
        inp.update(extra or {})
        out = io.StringIO()
        data = raw if raw is not None else json.dumps(inp).encode('utf-8')
        code = hookline.main([agent], stdin=io.BytesIO(data), out=out, env=self.env, now=now)
        self.assertEqual(code, 0)
        return out.getvalue()

    def seen_path(self, session='s-1'):
        return os.path.join(self.data, 'router', 'seen', hashlib.sha256(session.encode()).hexdigest()[:16] + '.json')

    def all_written(self):
        out = []
        for top, _, names in os.walk(self.data):
            for n in names:
                with open(os.path.join(top, n), 'rb') as f:
                    out.append(f.read())
        return b'\n'.join(out)

    def test_prints_the_documented_json_for_both_agents(self):
        self.put_plan(make_plan())
        for agent in ('claude', 'codex'):
            got = json.loads(self.run_main(agent, 'SessionStart', session='s-' + agent))
            self.assertEqual(list(got), ['hookSpecificOutput'])
            self.assertEqual(got['hookSpecificOutput']['hookEventName'], 'SessionStart')
            self.assertEqual(got['hookSpecificOutput']['additionalContext'],
                             hookline.line(agent, 'SessionStart', make_plan(), None, NOW))

    def test_the_prompt_is_in_nothing_it_writes_or_prints(self):
        self.put_plan(make_plan())
        printed = self.run_main('codex', 'UserPromptSubmit')               # first prompt: the routes
        self.assertIn('Lookups', printed)
        self.put_plan(make_plan(routes={'find': [{'agent': 'codex', 'model': 'gpt-6-luna', 'effort': 'low'}]}))
        printed += self.run_main('codex', 'UserPromptSubmit')
        printed += self.run_main('claude', 'SessionStart', session='s-2')
        self.assertNotIn(MARKER, printed)
        self.assertNotIn(MARKER.encode(), self.all_written())
        self.assertNotIn(b'folder-x', self.all_written())

    def test_the_prompt_key_is_never_kept(self):
        kept = hookline._fields(json.dumps({'hook_event_name': 'UserPromptSubmit', 'session_id': 'a',
                                            'prompt': MARKER, 'nested': {'prompt': MARKER}}).encode())
        self.assertEqual(sorted(kept), ['hook_event_name', 'session_id'])
        self.assertNotIn(MARKER, repr(kept))

    def test_the_seen_file_and_a_second_prompt(self):
        self.put_plan(make_plan())
        self.assertTrue(self.run_main('codex', 'SessionStart'))
        self.assertTrue(os.path.isfile(self.seen_path()))
        self.assertEqual(os.stat(self.seen_path()).st_mode & 0o777, 0o600)
        self.assertEqual(self.run_main('codex', 'UserPromptSubmit'), '')
        self.put_plan(make_plan(agents=agents(codex={'room': 'out'})))
        got = json.loads(self.run_main('claude', 'UserPromptSubmit'))     # same session id, the other agent's line
        self.assertTrue(got['hookSpecificOutput']['additionalContext'].startswith('Codex has no room now.'))
        self.assertEqual(self.run_main('claude', 'UserPromptSubmit'), '')

    def test_seen_files_go_after_seven_days(self):
        self.put_plan(make_plan())
        self.run_main('codex', 'SessionStart', session='old')
        old = self.seen_path('old')
        week = 7 * 86400
        os.utime(old, (NOW - week - 60, NOW - week - 60))
        self.run_main('codex', 'SessionStart', session='new')
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(self.seen_path('new')))

    def test_nothing_for_a_stale_plan_or_none_or_bad_input(self):
        self.assertEqual(self.run_main('codex', 'SessionStart'), '')                # no plan file
        self.put_plan(make_plan(made_at=NOW - 1000))
        self.assertEqual(self.run_main('codex', 'SessionStart'), '')
        self.assertEqual(self.run_main('claude', 'UserPromptSubmit'), '')
        self.put_plan(make_plan())
        self.assertEqual(self.run_main('codex', 'SessionStart', raw=b'not json'), '')
        self.assertEqual(self.run_main('codex', 'SessionStart', raw=b'[1, 2]'), '')
        self.assertEqual(self.run_main('codex', 'Stop'), '')
        out = io.StringIO()
        self.assertEqual(hookline.main(['nobody'], stdin=io.BytesIO(b'{}'), out=out, env=self.env, now=NOW), 0)
        self.assertEqual(hookline.main([], stdin=io.BytesIO(b'{}'), out=out, env=self.env, now=NOW), 0)
        self.assertEqual(out.getvalue(), '')
        with open(os.path.join(self.data, 'router', 'plan.json'), 'w') as f:
            f.write('{"v": 1, ' + ' ' * (17 << 10) + '}')                      # over 16 KB
        self.assertEqual(self.run_main('codex', 'SessionStart'), '')

    def test_a_prompt_with_no_session_id_says_nothing(self):
        self.put_plan(make_plan())
        self.assertEqual(self.run_main('codex', 'UserPromptSubmit', session=None), '')
        self.assertTrue(self.run_main('codex', 'SessionStart', session=None))


    def test_a_line_that_could_not_be_printed_is_not_marked_as_told(self):
        # The chat is marked as told only after the line went out, so a closed pipe never loses the line for good.
        class Closed(io.StringIO):
            def write(self, text):
                raise BrokenPipeError()
        self.put_plan(make_plan())
        inp = json.dumps({'hook_event_name': 'UserPromptSubmit', 'session_id': 's-1'}).encode()
        self.assertEqual(hookline.main(['claude'], stdin=io.BytesIO(inp), out=Closed(), env=self.env, now=NOW), 0)
        self.assertFalse(os.path.exists(self.seen_path()))
        self.assertIn('Lookups', self.run_main('claude', 'UserPromptSubmit'))
        self.assertTrue(os.path.exists(self.seen_path()))


class Command(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp(prefix='hookline-cmd-')
        self.env = {'SWITCHBOARD_HOME': self.data, 'HOME': os.environ['HOME'], 'PATH': '/usr/bin:/bin',
                    'PYTHONDONTWRITEBYTECODE': '1'}
        os.makedirs(os.path.join(self.data, 'router'))

    def put_plan(self, plan):
        with open(os.path.join(self.data, 'router', 'plan.json'), 'w', encoding='utf-8') as f:
            json.dump(plan, f)

    def sh(self, cmd, inp, timeout=20):
        return subprocess.run(['/bin/sh', '-c', cmd], input=inp, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=self.env, timeout=timeout, cwd=self.data)

    def test_the_hooks_file_command_prints_the_json(self):
        self.put_plan(make_plan(made_at=time.time()))
        inp = json.dumps({'hook_event_name': 'SessionStart', 'session_id': 'c1', 'source': 'startup'}).encode()
        for agent in ('codex', 'claude'):
            r = self.sh(hookline.command(agent, python=sys.executable), inp)
            self.assertEqual(r.returncode, 0, r.stderr)
            got = json.loads(r.stdout.decode())
            self.assertIn('Lookups', got['hookSpecificOutput']['additionalContext'])
            self.assertEqual(got['hookSpecificOutput']['hookEventName'], 'SessionStart')
        self.assertTrue(hookline.command('codex').endswith(hookline.MARK))

    def test_a_folder_with_a_character_outside_the_basic_plane(self):
        # A copy in a folder whose name holds such a character (an emoji, say) still runs.
        folder = os.path.join(self.data, 'copy-\U0001F600')
        os.makedirs(folder)
        here = os.path.dirname(hookline.__file__)
        for name in ('hookline.py', 'adapter_words.py'):
            shutil.copy(os.path.join(here, name), os.path.join(folder, name))
        self.put_plan(make_plan(made_at=time.time()))
        inp = json.dumps({'hook_event_name': 'SessionStart', 'session_id': 'c4', 'source': 'startup'}).encode()
        r = self.sh(hookline.command('claude', python=sys.executable, folder=folder), inp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('Lookups', r.stdout.decode('utf-8'))

    def test_module_form(self):
        self.put_plan(make_plan(made_at=time.time()))
        inp = json.dumps({'hook_event_name': 'SessionStart', 'session_id': 'c2'}).encode()
        r = subprocess.run([sys.executable, '-S', '-m', 'switchboard.router.hookline', 'claude'], input=inp,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env, cwd=ROOT, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('additionalContext', r.stdout.decode())

    def test_a_stale_plan_prints_nothing_and_exits_0(self):
        self.put_plan(make_plan(made_at=time.time() - 1000))
        inp = json.dumps({'hook_event_name': 'SessionStart', 'session_id': 'c3'}).encode()
        r = self.sh(hookline.command('codex', python=sys.executable), inp)
        self.assertEqual((r.returncode, r.stdout), (0, b''))

    def test_it_ends_itself_after_3_seconds(self):
        self.put_plan(make_plan(made_at=time.time()))
        r_fd, w_fd = os.pipe()                                 # the write end stays open: the read waits
        p = subprocess.Popen(['/bin/sh', '-c', hookline.command('codex', python=sys.executable)],
                             stdin=r_fd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env)
        os.close(r_fd)
        t0 = time.time()
        try:
            out, _ = p.communicate(timeout=15)
        finally:
            os.close(w_fd)
        took = time.time() - t0
        self.assertEqual((p.returncode, out), (0, b''))
        self.assertLess(took, 10)
        self.assertGreaterEqual(took, 2.5)


class Words(unittest.TestCase):
    """Every sentence of adapter_words passes the providers package's word rules, and none says "best model", names
    a person or speaks of "her" or "the owner"."""

    def test_every_sentence(self):
        import test_providers_words as rules
        got = [(n, getattr(W, n)) for n in dir(W) if n.isupper() and isinstance(getattr(W, n), str)]
        got += [(n + '[%s]' % k, v) for n in ('KIND_NAMES', 'KIND_ABOUT', 'AGENT_NAMES')
                for k, v in getattr(W, n).items()]
        self.assertGreater(len(got), 40)
        for name, text in got:
            with self.subTest(name=name):
                self.assertEqual(rules.problems(text), [], text)
                self.assertIsNone(re.search(r'(?i)best model|\bher\b|the owner', text), text)


if __name__ == '__main__':
    unittest.main()
