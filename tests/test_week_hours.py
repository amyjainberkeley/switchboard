import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Your week, hour by hour. The chart draws every hour of the week as a bar at every window width (a narrow page draws
# them edge to edge), and an hour says where it went: its projects, its models, the kind of work, what ran in it, how
# its tokens divide and the account Claude Code was on. Here: the reader's hours, the payload's, then the page in
# headless Chrome (a throwaway profile each run, no window) on the demo's made-up week.
# Run with:
#   SWITCHBOARD_HOME="$(mktemp -d)/data" taskpolicy -b python3 -m unittest discover -s tests -p 'test_week_hours.py'
# (always under the Chrome lock: /usr/bin/lockf -k <lock> ...)
import copy
import json
import os
import random
import re
import shutil
import sys
import tempfile
import unittest
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)

from switchboard import core, demo, insights, server, usage
from test_app import CHROME
from test_page_week_details import StillClock
from test_page_week import hm
from test_insights_week import EVENT_KEYS, fixture_week
from test_usage_series import (CHAT_9, CHAT_12, FIXTURE, HELPERS_9, N1, N2, NOV10, NOV11, NOW, R1, REAL, UTC,
                               WORKFLOWS_10, H, close, write_chat)

WENT = ('projects', 'models', 'kinds', 'tokens', 'agents', 'workflows', 'tools')
TOKEN_WORDS = (('cache_read', 'Re-reading'), ('cache_write', 'Storing'), ('output', 'Replies'), ('input', 'New input'))
GARDEN, NOTES, RECIPES = 'Garden planner v2', 'Weekly notes', 'recipes'


def write_agent(root, project, session, name, reqs):
    """A made-up helper of a chat: [(timestamp, model, (input, output, cache write, cache read))]."""
    path = os.path.join(root, project, session, 'subagents', 'agent-%s.jsonl' % name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        for i, (ts, model, t) in enumerate(reqs):
            f.write(json.dumps({'type': 'assistant', 'timestamp': ts, 'requestId': 'req_%s_%d' % (name, i), 'isSidechain': True,
                                'message': {'id': 'msg_%s_%d' % (name, i), 'model': model, 'content': [],
                                            'usage': {'input_tokens': t[0], 'output_tokens': t[1],
                                                      'cache_creation_input_tokens': t[2],
                                                      'cache_read_input_tokens': t[3]}}},
                               separators=(',', ':')) + '\n')
    return path


# ---------------------------------------------------------------- the reader: an hour says where it went

class TheReadersHours(unittest.TestCase):
    def setUp(self):
        self.s = REAL(FIXTURE, NOV10, NOV11, tz=UTC, series_since=NOV10)
        self.hours = dict((int((h['t'] - NOV10) // H), h) for h in self.s['series']['hours'])

    def test_an_hour_with_use_adds_up_every_way(self):
        used = [h for h in self.hours.values() if h['weight'] > 0]
        self.assertEqual(sorted(k for k, h in self.hours.items() if h['weight'] > 0), [9, 10, 12, 14, 15, 23])
        for h in used:
            for part in ('who', 'projects', 'models', 'kinds'):
                close(self, sum(h[part].values()), h['weight'], part)
            close(self, usage.weight(h['tokens']), h['weight'])
            self.assertEqual(list(h['models']), list(usage.FAMILIES))
            self.assertEqual(list(h['kinds']), list(usage.KINDS))
            self.assertEqual(list(h['tokens']), list(usage.TOKENS))

    def test_an_hour_with_no_request_says_only_that(self):
        for k, h in self.hours.items():
            if k not in (9, 10, 12, 14, 15, 23):
                self.assertEqual(h, {'t': NOV10 + k * H, 'weight': 0.0, 'who': dict.fromkeys(usage.WHO, 0.0)})

    def test_each_hour_by_hand(self):
        # projects: the chat's title; tool calls: one per tool_use id in the hour (three of the chat's and the five
        # helpers' at 9, the workflow's three at 10, one each at 12 and 14); agents: the five helpers at 9, the
        # workflow's two agents of its one run at 10
        want = {9: ({GARDEN: CHAT_9 + HELPERS_9}, 8, 5, 0), 10: ({GARDEN: WORKFLOWS_10}, 3, 2, 1),
                12: ({GARDEN: CHAT_12}, 1, 0, 0), 14: ({NOTES: N2}, 1, 0, 0), 15: ({NOTES: N1}, 0, 0, 0),
                23: ({RECIPES: R1}, 0, 0, 0)}
        for k, (projects, tools, agents, runs) in want.items():
            h = self.hours[k]
            self.assertEqual(sorted(h['projects']), sorted(projects), k)
            for label, weight in projects.items():
                close(self, h['projects'][label], weight, (k, label))
            self.assertEqual((h['tools'], len(h['agents']), len(h['runs'])), (tools, agents, runs), k)

    def test_the_hours_add_up_to_the_day_and_the_week(self):
        (day,) = self.s['series']['days']
        used = [h for h in self.hours.values() if h['weight'] > 0]
        for part in ('projects', 'models', 'kinds'):
            for key, weight in day[part].items():
                close(self, sum(h[part].get(key, 0.0) for h in used), weight, (part, key))
        totals = self.s['totals']
        for kind in usage.TOKENS:
            self.assertEqual(sum(h['tokens'][kind] for h in used), totals['tokens'][kind], kind)
        self.assertEqual(sum(h['tools'] for h in used), totals['tool_calls'])
        ids = set(i for h in used for i in h['agents'])
        self.assertEqual(len(ids), totals['helper_agents'] + totals['workflow_agents'])     # each agent once
        self.assertEqual(len(set(i for h in used for i in h['runs'])), totals['workflows'])

    def test_an_agent_is_a_hashed_id_and_the_same_one_in_every_hour_it_works_in(self):
        tmp = tempfile.mkdtemp(prefix='switchboard-week-')
        self.addCleanup(shutil.rmtree, tmp, True)
        write_chat(tmp, '-p1', 's1', [('2026-11-10T10:05:00.000Z', 'claude-opus-5-5', (10, 0, 0, 0))], title='Website')
        write_agent(tmp, '-p1', 's1', 'a1', [('2026-11-10T10:10:00.000Z', 'claude-sonnet-5-5', (4, 0, 0, 0)),
                                             ('2026-11-10T11:20:00.000Z', 'claude-sonnet-5-5', (6, 0, 0, 0))])
        write_agent(tmp, '-p1', 's1', 'a2', [('2026-11-10T11:30:00.000Z', 'claude-sonnet-5-5', (8, 0, 0, 0))])
        hours = dict((int((h['t'] - NOV10) // H), h) for h in REAL(tmp, NOV10, NOV11, tz=UTC, series_since=NOV10)['series']['hours'])
        ten, eleven = hours[10], hours[11]
        self.assertEqual(len(ten['agents']), 1)
        self.assertEqual(len(eleven['agents']), 2)
        self.assertIn(ten['agents'][0], eleven['agents'])
        for i in ten['agents'] + eleven['agents']:
            self.assertRegex(i, r'^[0-9a-f]{16}$')                                  # never a path, a name or a session
        self.assertEqual((ten['projects'], eleven['projects']), ({'Website': 14.0}, {'Website': 14.0}))
        close(self, ten['models']['Opus'], 10.0)
        close(self, ten['models']['Sonnet'], 4.0)

    def test_the_series_is_plain_json(self):
        back = json.loads(json.dumps(self.s['series']))
        self.assertEqual(back['hours'][9]['agents'], self.hours[9]['agents'])


# ---------------------------------------------------------------- the payload: what the page gets for an hour

class Shares(unittest.TestCase):
    def test_whole_percents_biggest_first(self):
        self.assertEqual(insights._shares([('a', 20), ('b', 50), ('c', 30)]),
                         [{'label': 'b', 'share': 50}, {'label': 'c', 'share': 30}, {'label': 'a', 'share': 20}])

    def test_two_or_more_past_the_top_are_one_row(self):
        five = [('a', 40), ('b', 30), ('c', 20), ('d', 6), ('e', 4)]
        self.assertEqual(insights._shares(five, 3), [{'label': 'a', 'share': 40}, {'label': 'b', 'share': 30},
                                                     {'label': 'c', 'share': 20}, {'label': '2 more', 'share': 10}])
        self.assertEqual([r['label'] for r in insights._shares(five[:4], 3)], ['a', 'b', 'c', 'd'])   # one left: named
        self.assertEqual([r['label'] for r in insights._shares(five, None)], ['a', 'b', 'c', 'd', 'e'])

    def test_nothing_and_next_to_nothing_are_left_out(self):
        self.assertEqual(insights._shares([('a', 999), ('b', 1), ('c', 0), ('d', None), ('e', -5), ('f', float('nan'))]),
                         [{'label': 'a', 'share': 100}])
        self.assertEqual(insights._shares([]), [])
        self.assertEqual(insights._shares([('a', 0)]), [])

    def test_a_tie_keeps_the_order_given(self):
        self.assertEqual([r['label'] for r in insights._shares([('x', 1), ('y', 1), ('w', 1)])], ['x', 'y', 'w'])

    def test_always_a_hundred(self):
        rnd = random.Random(7)
        for n in range(1, 12):
            for top in (None, 3):
                rows = insights._shares([('p%d' % i, rnd.random() * 10 ** rnd.randint(0, 6)) for i in range(n)], top)
                self.assertEqual(sum(r['share'] for r in rows), 100, rows)
                self.assertTrue(all(r['share'] >= 1 for r in rows))


def reader_hour(t, weight, **parts):
    """One of the reader's hours, all of it one chat's own turns unless `parts` say more."""
    h = {'t': t, 'weight': float(weight), 'who': {'chat': float(weight), 'helpers': 0.0, 'workflows': 0.0},
         'projects': {'Website': float(weight)}, 'models': {'Opus': float(weight)}, 'kinds': {'build': float(weight)},
         'tokens': {'input': int(weight), 'output': 0, 'cache_write': 0, 'cache_read': 0, 'cache_write_1h': 0},
         'tools': 0, 'agents': [], 'runs': []}
    h.update(parts)
    return h


class TheHoursOnThePage(unittest.TestCase):
    T = NOV11 + 9 * H                                # 09:00 on Nov 11: three hours before NOW's (12:30 UTC)

    def hour(self, series, t=None):
        at = insights.iso(self.T if t is None else t, UTC)
        return next(h for h in insights._series(series, NOW, UTC)['hours'] if h['at'] == at)

    def test_an_hour_with_use_says_where_it_went(self):
        h = self.hour({'days': [], 'hours': [reader_hour(
            self.T, 200, projects={'Website': 120.0, 'Notes': 50.0, 'Billing': 20.0, 'Design': 6.0, 'Docs': 4.0},
            models={'Opus': 150.0, 'Haiku': 50.0, 'Sonnet': 0.0}, kinds={'build': 100.0, 'not labelled': 60.0, 'fix': 40.0},
            tokens={'input': 20, 'output': 20, 'cache_write': 10, 'cache_read': 1200, 'cache_write_1h': 10},
            tools=41, agents=['a', 'b', 'c'], runs=['r'])]})
        self.assertEqual(set(h), {'at', 'weight', 'who'} | set(WENT))
        self.assertEqual(h['projects'], [{'label': 'Website', 'share': 60}, {'label': 'Notes', 'share': 25},
                                         {'label': 'Billing', 'share': 10}, {'label': '2 more', 'share': 5}])
        self.assertEqual(h['models'], [{'label': 'Opus', 'share': 75}, {'label': 'Haiku', 'share': 25}])
        self.assertEqual(h['kinds'], [{'label': 'Build', 'share': 50}, {'label': 'Not labelled', 'share': 30},
                                      {'label': 'Fix', 'share': 20}])            # named as the page names a kind
        # weighted: 20 new input, 100 of replies (5 each), 20 stored (a 1-hour write is 2), 60 re-read (0.05 each)
        self.assertEqual(h['tokens'], {'input': 10, 'output': 50, 'cache_write': 10, 'cache_read': 30})
        self.assertEqual((h['agents'], h['workflows'], h['tools']), (3, 1, 41))

    def test_an_hour_with_no_use_and_a_reader_that_says_no_more(self):
        plain = {'t': self.T, 'weight': 5.0, 'who': {'chat': 5.0, 'helpers': 0.0, 'workflows': 0.0}}
        series = {'days': [], 'hours': [plain, reader_hour(self.T + H, 0)]}
        self.assertEqual(set(self.hour(series)), {'at', 'weight', 'who'})                # the reader said only who
        self.assertEqual(set(self.hour(series, self.T + H)), {'at', 'weight', 'who'})    # nothing happened in it
        self.assertEqual(set(self.hour(series, self.T + 2 * H)), {'at', 'weight', 'who'})

    def test_two_of_the_readers_hours_in_one_slot_count_an_agent_once(self):
        # where the clocks moved by half an hour, two of the reader's hours start inside one of the page's
        a = reader_hour(self.T, 10, tools=2, agents=['a', 'b'], runs=['r'])
        b = reader_hour(self.T + 1800, 30, projects={'Website': 10.0, 'Notes': 20.0}, models={'Sonnet': 30.0},
                        kinds={'fix': 30.0}, tokens={'input': 0, 'output': 6, 'cache_write': 0, 'cache_read': 0,
                                                     'cache_write_1h': 0}, tools=3, agents=['b', 'c'], runs=['r', 'q'])
        h = self.hour({'days': [], 'hours': [a, b]})
        self.assertEqual(h['weight'], 40.0)
        self.assertEqual(h['projects'], [{'label': 'Notes', 'share': 50}, {'label': 'Website', 'share': 50}])
        self.assertEqual(h['models'], [{'label': 'Sonnet', 'share': 75}, {'label': 'Opus', 'share': 25}])
        self.assertEqual(h['kinds'], [{'label': 'Fix', 'share': 75}, {'label': 'Build', 'share': 25}])
        self.assertEqual(h['tokens'], {'input': 25, 'output': 75, 'cache_write': 0, 'cache_read': 0})
        self.assertEqual((h['agents'], h['workflows'], h['tools']), (3, 2, 5))

    def test_the_fixtures_week(self):
        s, page = fixture_week()
        hour = dict((h['at'], h) for h in page['series']['hours'])
        nine, ten = hour['2026-11-10T09:00:00+00:00'], hour['2026-11-10T10:00:00+00:00']
        self.assertEqual(nine['projects'], [{'label': GARDEN, 'share': 100}])
        self.assertEqual((nine['agents'], nine['workflows'], nine['tools']), (5, 0, 8))
        self.assertEqual((ten['agents'], ten['workflows'], ten['tools']), (2, 1, 3))
        for h in page['series']['hours']:
            if h['weight'] > 0:
                for part in ('projects', 'models', 'kinds'):
                    self.assertEqual(sum(r['share'] for r in h[part]), 100, (h['at'], part))
                self.assertEqual(sum(h['tokens'].values()), 100, h['at'])
                self.assertNotIn('accounts', h)                                  # no History: no account is said
        json.dumps(page)


class TheAccountOfAnHour(unittest.TestCase):
    A, B, C = 'ana@garden.example', 'ana@notes.example', 'ana@recipes.example'
    STARTS = [1000, 4600, 8200, 11800]

    def on(self, lines, until=13000):
        return insights.accounts_by_hour(lines, self.STARTS, until)

    def switch(self, t, frm, to, **more):
        return dict({'t': t, 'kind': 'switch', 'from': frm, 'to': to, 'text': 'Switched'}, **more)

    def test_from_historys_switches(self):
        lines = [{'t': 4700, 'kind': 'info', 'text': 'Switchboard started'}, self.switch(5000, self.A, self.B),
                 self.switch(9000, self.B, self.C), self.switch(9100, self.C, self.B)]
        self.assertEqual(self.on(lines), [[],                           # it ends before History's first line
                                          [self.A, self.B],             # History begins in it: on A, then the switch
                                          [self.B, self.C, self.B],     # there and back inside one hour
                                          [self.B]])

    def test_the_hour_history_begins_in_says_what_is_known_from_then_on(self):
        lines = [{'t': 2000, 'kind': 'info', 'text': 'Switchboard started'}, self.switch(9000, self.A, self.B)]
        self.assertEqual(self.on(lines), [[self.A], [self.A], [self.A, self.B], [self.B]])
        self.assertEqual(self.on([dict(lines[0], t=4600)] + lines[1:])[0], [])   # it begins as that hour ends

    def test_one_email_spelled_two_ways_is_one_account(self):
        lines = [{'t': 0, 'kind': 'info', 'text': 'x'}, self.switch(5000, self.A, self.B),
                 self.switch(5100, self.B, self.B.upper()), self.switch(9000, self.B.upper(), 'Ana@Notes.example')]
        self.assertEqual(self.on(lines), [[self.A], [self.A, self.B], [self.B.upper()], ['Ana@Notes.example']])

    def test_the_order_of_the_lines_does_not_matter(self):
        lines = [self.switch(9000, self.B, self.C), {'t': 500, 'kind': 'info', 'text': 'x'}, self.switch(5000, self.A, self.B)]
        self.assertEqual(self.on(lines), [[self.A], [self.A, self.B], [self.B, self.C], [self.C]])
        self.assertEqual(self.on(iter(lines)), self.on(lines))

    def test_no_switch_no_history_and_lines_that_are_not_lines(self):
        self.assertEqual(self.on([]), [[], [], [], []])
        self.assertEqual(self.on(None), [[], [], [], []])
        self.assertEqual(self.on([{'t': 0, 'kind': 'limit', 'account': self.A, 'pct': 95, 'text': 'x'}]), [[], [], [], []])
        self.assertEqual(self.on(['x', None, {'kind': 'switch', 'to': self.B}, {'t': 'soon', 'kind': 'switch', 'to': self.B}]),
                         [[], [], [], []])

    def test_a_first_switch_that_does_not_say_where_from(self):
        lines = [{'t': 0, 'kind': 'info', 'text': 'x'}, {'t': 5000, 'kind': 'switch', 'account': self.B, 'text': 'x'}]
        self.assertEqual(self.on(lines), [[], [self.B], [self.B], [self.B]])    # `account` names it when `to` does not

    def test_a_switch_on_the_hour_and_the_same_account_twice(self):
        lines = [{'t': 0, 'kind': 'info', 'text': 'x'}, self.switch(4600, self.A, self.B), self.switch(5000, self.A, self.B)]
        self.assertEqual(self.on(lines), [[self.A], [self.B], [self.B], [self.B]])

    def test_the_last_hour_ends_now(self):
        lines = [{'t': 0, 'kind': 'info', 'text': 'x'}, self.switch(100, self.C, self.A), self.switch(12500, self.A, self.B)]
        self.assertEqual(self.on(lines, until=12000)[-1], [self.A])              # the switch is after now
        self.assertEqual(self.on(lines, until=13000)[-1], [self.A, self.B])

    def test_only_an_hour_with_use_names_its_account(self):
        lines = [{'t': NOV10 + 8 * H, 'kind': 'info', 'text': 'x'},
                 {'t': NOV10 + 9.5 * H, 'kind': 'switch', 'from': self.A, 'to': self.B, 'text': 'x'}]
        s, page = fixture_week(lines=lines)
        hour = dict((h['at'], h) for h in page['series']['hours'])
        self.assertEqual(hour['2026-11-10T09:00:00+00:00']['accounts'], [self.A, self.B])
        self.assertEqual(hour['2026-11-10T10:00:00+00:00']['accounts'], [self.B])
        self.assertNotIn('accounts', hour['2026-11-09T22:00:00+00:00'])          # use, but before History's first line
        self.assertGreater(hour['2026-11-09T22:00:00+00:00']['weight'], 0)
        self.assertNotIn('accounts', hour['2026-11-10T11:00:00+00:00'])          # History, but no use
        self.assertEqual([e['kind'] for e in page['events']], ['switch'])       # the events read the same lines


class TheDemoWeek(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)

    def test_every_hour_with_use_says_where_it_went(self):
        page = server.usage_view()
        used = [h for h in page['series']['hours'] if h['weight'] > 0]
        self.assertGreater(len(used), 100)
        for h in used:
            self.assertLessEqual(set(WENT), set(h), h['at'])
            for part in ('projects', 'models', 'kinds'):
                self.assertEqual(sum(r['share'] for r in h[part]), 100, (h['at'], part))
            self.assertEqual(sum(h['tokens'].values()), 100)
            self.assertLessEqual(len(h['projects']), insights.HOUR_PROJECTS + 1)
            for email in h.get('accounts', []):
                self.assertRegex(email, r'^[^@\s]+@[a-z.]+\.example$')            # a whole made-up email, never a short name
        self.assertTrue(any(h.get('accounts') for h in used))
        self.assertLess(len(json.dumps(page, separators=(',', ':'))), 200000)   # a week stays a small answer


# ---------------------------------------------------------------- the page

def counted(n, word):
    return '{:,} {}{}'.format(n, word, '' if n == 1 else 's')


def rows_of(h):
    """The hover's rows for one of the payload's hours, in the page's words: [name, [what it holds]]."""
    def share(x):
        return '%s %d%%' % (x['label'], x['share'])
    rows = []
    if h.get('projects'):
        rows.append(['Projects', [share(x) for x in h['projects']]])
    if h.get('models'):
        rows.append(['Models', [share(x) for x in h['models'][:3]]])
    if h.get('kinds'):
        rows.append(['Work', [share(x) for x in h['kinds'][:3]]])
    ran = [counted(h[k], w) for k, w in (('agents', 'agent'), ('workflows', 'workflow'), ('tools', 'tool call')) if h.get(k, 0) > 0]
    if ran:
        rows.append(['Ran', ran])
    t = h.get('tokens') or {}
    big = sorted(((k, w) for k, w in TOKEN_WORDS if t.get(k, 0) >= 1), key=lambda kw: -t[kw[0]])[:3]
    if big:
        rows.append(['Tokens', ['%s %d%%' % (w, t[k]) for k, w in big]])
    if len(h.get('accounts') or []) == 1:
        rows.append(['Account', list(h['accounts'])])
    return rows


BARS = r"""
      const f = [...document.querySelectorAll('iframe')];
      done(f.map((x) => { const d = x.contentDocument, c = d.getElementById('wk-chart'), svg = c.querySelector('svg');
        const spans = [...d.querySelectorAll('#wk-bars > g')].map((g) => { const b = [...g.children].map((el) => el.getBBox());
          return b.length ? [Math.min(...b.map((r) => r.x)), Math.max(...b.map((r) => r.x + r.width))] : null; });
        return {by: c.dataset.by, W: svg.viewBox.baseVal.width, spans, peak: (d.getElementById('wk-peak') || {}).textContent || null,
                days: [...svg.querySelectorAll('text:not(.halo)')].map((t) => t.textContent),
                rounded: d.querySelectorAll('#wk-bars path').length, squared: d.querySelectorAll('#wk-bars rect').length,
                sum: d.getElementById('wk-sum').textContent,
                overflow: d.documentElement.scrollWidth - d.documentElement.clientWidth}; }));"""

# The pointer over each of `hours` in each frame, and what the readout then says.
HOVER = r"""
      const f = [...document.querySelectorAll('iframe')];
      for (const x of f) x.contentDocument.getElementById('week').scrollIntoView();
      await wait(300);
      const hover = (x, i) => { const d = x.contentDocument, w = x.contentWindow, c = d.getElementById('wk-chart'), svg = c.querySelector('svg');
        const r = svg.getBoundingClientRect(), k = r.width / svg.viewBox.baseVal.width, slot = svg.viewBox.baseVal.width / 168;
        c.dispatchEvent(new w.PointerEvent('pointermove', {clientX: r.left + (i + .5) * slot * k, clientY: r.top + 110 * k, bubbles: true}));
        const tip = d.getElementById('tip'), b = tip.getBoundingClientRect(), text = (el) => (el ? el.textContent : null);
        return {hidden: tip.hidden, head: text(tip.querySelector('.t-h')), value: text(tip.querySelector('.t-v')),
          who: [...tip.querySelectorAll('.t-rows b')].map(text),
          rows: [...tip.querySelectorAll('.t-d dt')].map((dt) => [dt.textContent, [...dt.nextElementSibling.querySelectorAll('.t-s')].map(text)]),
          events: [...tip.querySelectorAll('.t-x div')].map((e) => [e.className, e.textContent]),
          inside: b.left >= 0 && b.top >= 0 && b.right <= w.innerWidth && b.bottom <= w.innerHeight, width: b.width, view: w.innerWidth}; };
      done(f.map((x) => HOURS.map((i) => hover(x, i))));"""


def hover(hours):
    return HOVER.replace('HOURS', json.dumps(list(hours)))


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class EveryWidthDrawsTheWeekByHour(StillClock):
    def test_a_bar_an_hour_from_a_wide_page_to_a_phone(self):
        wide, window, phone = self.shown(server.usage_view(), BARS, (1440, 678, 390))
        for got, gap in ((wide, 2), (window, 1), (phone, 0)):
            slot = got['W'] / 168
            self.assertEqual(2 if slot >= 6 else 1 if slot >= 3 else 0, gap, slot)     # the three widths are the three cases
            self.assertEqual((got['by'], len(got['spans']), got['overflow']), ('hour', 168, 0))
            pairs = [(a, b) for a, b in zip(got['spans'], got['spans'][1:]) if a and b]
            self.assertGreater(len(pairs), 100)
            for a, b in pairs:
                if gap:
                    self.assertAlmostEqual(b[0] - a[1], gap, delta=0.03)
                else:                       # edge to edge: no seam between two hours, and no hour hidden under the next
                    self.assertLessEqual(b[0], a[1])
                    self.assertGreater(b[0] - a[0], slot - 0.03)
            self.assertEqual(bool(got['rounded']), bool(gap))                      # a rounded top only where a bar has room for one
            self.assertTrue(got['squared'])
            self.assertGreaterEqual(len(got['days']), 6)                            # the days are still named under it
            self.assertTrue(got['sum'].startswith('Use by hour, '), got['sum'])
        self.assertIsNotNone(wide['peak'])
        self.assertEqual((window['peak'], phone['peak']), (wide['peak'], wide['peak']))


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class AnHourSaysWhereItWent(StillClock):
    def test_the_readout_says_what_the_payload_says_of_that_hour(self):
        page = server.usage_view()
        hours = page['series']['hours']
        used = [i for i, h in enumerate(hours) if h['weight'] > 0]
        spike = max(used, key=lambda i: hours[i]['weight'])
        idle = next(i for i, h in enumerate(hours) if not h['weight'])
        switched = [i for i in used if len(hours[i].get('accounts') or []) > 1]
        self.assertTrue(switched)                                                  # the demo's History has a switch in the week
        look = [used[0], spike, switched[0], idle, 167]
        wide, phone = self.shown(page, hover(look), (1440, 390))
        for frame in (wide, phone):
            for i, got in zip(look, frame):
                h, at = hours[i], datetime.fromisoformat(hours[i]['at'])
                self.assertFalse(got['hidden'])
                self.assertTrue(got['inside'], (i, got))                           # the readout never leaves the window
                self.assertLessEqual(got['width'], min(360, got['view'] - 16) + 0.5)
                self.assertTrue(got['head'].endswith(' · %s%s' % (hm(at), ', so far' if i == 167 else '')), got['head'])
                if not h['weight']:
                    self.assertEqual((got['value'], got['who'], got['rows']), ('Nothing', [], []))
                    continue
                self.assertTrue(got['value'].endswith(' an average hour'), got['value'])
                self.assertEqual(sum(int(p.rstrip('%')) for p in got['who']), 100, got['who'])
                self.assertEqual(got['rows'], rows_of(h), i)
        def words(frame):
            return [[got[k] for k in ('head', 'value', 'who', 'rows', 'events')] for got in frame]
        self.assertEqual(words(wide), words(phone))                               # the same words at every width
        # the first hour with use, by hand: the demo's projects, models, kinds of work and tokens
        first = dict(wide[0]['rows'])
        self.assertEqual(first['Projects'], ['Website 73%', 'Mobile app 13%', 'Notes 4%', '5 more 10%'])
        self.assertEqual(first['Models'], ['Opus 72%', 'Sonnet 20%', 'Fable 8%'])
        self.assertEqual(first['Work'], ['Not labelled 54%', 'Build 21%', 'Review 11%'])
        self.assertEqual(first['Tokens'], ['Re-reading 42%', 'Storing 36%', 'Replies 20%'])
        self.assertRegex(' · '.join(first['Ran']), r'^\d+ agents? · \d workflows? · [\d,]+ tool calls?$')
        self.assertEqual(wide[0]['who'], ['81%', '10%', '9%'])
        # an hour with a switch in it: the switch has its own line, so no Account row; else the whole email
        self.assertNotIn('Account', dict(wide[2]['rows']))
        self.assertTrue(any(cls == 'swi' and ' Switched ' in text for cls, text in wide[2]['events']), wide[2]['events'])
        on = [got for i, got in zip(look, wide) if len(hours[i].get('accounts') or []) == 1]
        self.assertTrue(on)
        for got in on:
            self.assertRegex(dict(got['rows'])['Account'][0], r'^[^@\s]+@[a-z.]+\.example$')

    def test_the_three_who_did_it_add_to_a_hundred(self):
        page = copy.deepcopy(server.usage_view())
        i = next(i for i, h in enumerate(page['series']['hours']) if h['weight'] > 0)
        page['series']['hours'][i].update(weight=3.0, who={'chat': 1.0, 'helpers': 1.0, 'workflows': 1.0})
        ((got,),) = self.shown(page, hover([i]), (1440,))
        self.assertEqual(got['who'], ['34%', '33%', '33%'])                       # workflows, helpers, you: never 99

    def test_the_tokens_biggest_first_and_a_count_in_the_thousands(self):
        page = copy.deepcopy(server.usage_view())
        i = next(i for i, h in enumerate(page['series']['hours']) if h['weight'] > 0)
        page['series']['hours'][i].update(tokens={'cache_read': 20, 'cache_write': 10, 'output': 65, 'input': 5},
                                          agents=1, workflows=1, tools=12345)
        ((got,),) = self.shown(page, hover([i]), (1440,))
        rows = dict(got['rows'])
        self.assertEqual(rows['Tokens'], ['Replies 65%', 'Re-reading 20%', 'Storing 10%'])   # not the fourth: new input
        self.assertEqual(rows['Ran'], ['1 agent', '1 workflow', '12,345 tool calls'])

    def test_a_week_kept_by_an_earlier_build_says_who_and_no_more(self):
        page = copy.deepcopy(server.usage_view())
        for h in page['series']['hours']:
            for key in WENT + ('accounts',):
                h.pop(key, None)
        i = max(range(168), key=lambda i: page['series']['hours'][i]['weight'])
        ((got,),) = self.shown(page, hover([i]), (1440,))
        self.assertFalse(got['hidden'])
        self.assertEqual(len(got['who']), 3)
        self.assertEqual(got['rows'], [])
        self.assertTrue(got['value'].endswith(' an average hour'))

    def test_a_line_reached_then_the_switch_it_caused_and_why_only_where_nothing_says_it(self):
        page = copy.deepcopy(server.usage_view())
        hours = page['series']['hours']
        used = [i for i, h in enumerate(hours) if h['weight'] > 0][:3]
        a, b = 'maya@northwind.example', 'm.reyes@harbor.example'
        start = [datetime.fromisoformat(hours[i]['at']).timestamp() for i in used]

        def event(t, **facts):
            e = dict.fromkeys(EVENT_KEYS)
            e.update(at=insights.iso(t), **facts)
            return e
        long_why = 'every other account was past its line when the weekly line of this one was reached, so it moved on'
        self.assertGreater(len(long_why), 80)
        page['events'] = [
            # the switch is recorded a few seconds before the limit that caused it, in the same minute
            event(start[0] + 720, kind='switch', to=b, account=b, by='auto',
                  why='%s weekly at 95%% (line 95%%); %s has room (40 points before its nearest line)' % (a, b), **{'from': a}),
            event(start[0] + 725, kind='limit', account=a, window='weekly', pct=95),
            event(start[1] + 60, kind='switch', to=a, account=a, by='you', why='you pressed Use; nothing else', **{'from': b}),
            event(start[2] + 60, kind='switch', to=b, account=b, by='auto', why=long_why, **{'from': a}),
        ]
        for i, on in zip(used, ([a, b], [b, a], [a, b])):
            hours[i]['accounts'] = on
        (got,) = self.shown(page, hover(used), (1440,))
        at = [hm(datetime.fromtimestamp(t)) for t in (start[0] + 720, start[1] + 60, start[2] + 60)]
        self.assertEqual(got[0]['events'], [['lim', '%s  %s reached its weekly line, 95%%' % (at[0], a)],
                                            ['swi', '%s  Switched %s → %s' % (at[0], a, b)]])
        self.assertEqual(got[1]['events'], [['swi', '%s  Switched %s → %s, you pressed Use' % (at[1], b, a)]])
        self.assertEqual(got[2]['events'], [['swi', '%s  Switched %s → %s' % (at[2], a, b)]])    # too long to say here
        for one in got:
            self.assertNotIn('Account', dict(one['rows']))
            self.assertTrue(one['inside'])


class ThePageSaysSoInItsOwnWords(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(HERE, '..', 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            self.page = f.read()

    def test_no_chart_by_day_is_left(self):
        for gone in ('byHour', "an average day'"):
            self.assertNotIn(gone, self.page)
        self.assertIn("box.dataset.by = 'hour';", self.page)

    def test_the_tokens_words_are_the_readers_four_kinds(self):
        m = re.search(r'const TOKEN_WORDS = (\[\[.*?\]\]);', self.page)
        self.assertEqual([k for k, _ in json.loads(m.group(1).replace("'", '"'))], [k for k, _ in TOKEN_WORDS])
        self.assertEqual(sorted(k for k, _ in TOKEN_WORDS), sorted(usage.TOKEN_KINDS))


if __name__ == '__main__':
    unittest.main()
