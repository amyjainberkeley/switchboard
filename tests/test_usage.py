import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Tests for History's Work (switchboard/usage.py).
#
# The history under tests/fixtures/usage/projects is made up. Every expected number below was
# worked out by hand from that tree; the arithmetic is in the comments.
import calendar
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import timedelta, timezone
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)

from switchboard import usage
import hand_count

FIXTURE = os.path.join(HERE, 'fixtures', 'usage', 'projects')
UTC = timezone.utc
NOV9 = calendar.timegm((2026, 11, 9, 0, 0, 0))
NOV10 = calendar.timegm((2026, 11, 10, 0, 0, 0))
NOV11 = calendar.timegm((2026, 11, 11, 0, 0, 0))
S1 = 'aaaaaaaa-0000-4000-8000-000000000001'


def w(i, o, cw, cr):
    # today’s prices: a cache read is 0.05 of an input token, a 1-hour cache write 2 (every write in
    # the made-up tree is a 1-hour one, as Claude Code writes them: cache_creation.ephemeral_1h_input_tokens)
    return i + 5 * o + 2 * cw + 0.05 * cr


def w_fable(i, o, cw, cr):
    # the same on Fable 5.2, but a cache read there is 0.025 of its input token ($0.20 against $8)
    return i + 5 * o + 2 * cw + 0.025 * cr


# The in-window requests of Nov 10 (UTC), by hand from the fixture tree:
#   chat S1 (opus)  m1 (10,200,1000,5000) m2 (5,100,0,6000) m3 (5,50,100,6100) m4 (3,40,0,6200) m5 (1,10,0,100)
#   helper H1 (opus, no model)    h1a (3,300,2000,0) h1b (2,100,0,2000)
#   helper H2 (haiku)             h2a (100,20,0,0)
#   fork F1 (opus, no model)      f1a (4,80,500,1000)          (+ a copy of m1, which the chat owns)
#   helper H3 (opus, "inherit")   h3a (6,60,0,3000)
#   helper H4 (sonnet, no model)  h4a (7,70,700,0)
#   workflow W1 (sonnet)          w1a (50,500,3000,10000) w1b (5,50,0,12000)
#   workflow W2 (opus, no model)  w2a (8,400,1000,4000)
#   chat S2 (fable)               n1 (20,100,0,0) n2 (30,300,400,500)
#   chat S3 (sonnet)              r1 (1,2,3,4)
# Left out: m0 (first line 23:59:59.999 on Nov 9), r0 (Nov 9), r2 (exactly Nov 11 00:00, the end),
# the <synthetic> line, the line with no ids, the broken line, the torn last line.
S1_OWN = w(10, 200, 1000, 5000) + w(5, 100, 0, 6000) + w(5, 50, 100, 6100) + w(3, 40, 0, 6200) + w(1, 10, 0, 100)
H1 = w(3, 300, 2000, 0) + w(2, 100, 0, 2000)
H2 = w(100, 20, 0, 0)
F1 = w(4, 80, 500, 1000)
H3 = w(6, 60, 0, 3000)
H4 = w(7, 70, 700, 0)
W1 = w(50, 500, 3000, 10000) + w(5, 50, 0, 12000)
W2 = w(8, 400, 1000, 4000)
S2 = w_fable(20, 100, 0, 0) + w_fable(30, 300, 400, 500)
S3 = w(1, 2, 3, 4)
TOTAL = S1_OWN + H1 + H2 + F1 + H3 + H4 + W1 + W2 + S2 + S3


def by_label(cut):
    return dict((r['label'], r) for r in cut)


class Fixture(unittest.TestCase):
    """The whole result for one day of the made-up tree, checked against the hand count above."""

    @classmethod
    def setUpClass(cls):
        cls.s = usage.summarize(FIXTURE, NOV10, NOV11, tz=UTC)

    def test_hand_arithmetic_is_what_the_comments_say(self):
        self.assertAlmostEqual(S1_OWN, 5394)
        self.assertAlmostEqual(TOTAL, 32358.7)        # n2's 500 cache reads on Fable: 12.5, not 25

    def test_totals(self):
        t = self.s['totals']
        self.assertEqual(t['requests'], 17)
        self.assertEqual(t['tokens'], {'input': 260, 'output': 2382, 'cache_write': 8703, 'cache_read': 55904,
                                       'cache_write_1h': 8703,      # every write in the made-up tree is a 1-hour one
                                       'cache_read_low': 500})      # n2's reads, on Fable 5.2
        self.assertAlmostEqual(t['weight'], TOTAL)
        self.assertAlmostEqual(t['weight'], usage.weight(t['tokens']))
        self.assertEqual((t['chats'], t['helper_agents'], t['workflows'], t['workflow_agents']), (3, 5, 1, 2))
        # tu_1 tu_2 tu_3 tu_5 (chat), tu_n2 (chat), 4 helper tools, 3 workflow tools; the fork's copy of tu_1 once
        self.assertEqual(t['tool_calls'], 13)
        self.assertEqual(self.s['tool_calls'], {'chat': 5, 'helpers': 5, 'workflows': 3})

    def test_the_result_says_what_shares_are(self):
        self.assertIn('weighted tokens', self.s['weighting']['note'])
        self.assertIn('not by Anthropic', self.s['weighting']['note'])
        self.assertEqual(self.s['weighting']['factors'],
                         {'input': 1.0, 'output': 5.0, 'cache_write': 1.25, 'cache_read': 0.05, 'cache_write_1h': 2.0})

    def test_who(self):
        who = by_label(self.s['who'])
        self.assertAlmostEqual(who['Your chat turns']['detail']['weight'], S1_OWN + S2 + S3)
        self.assertAlmostEqual(who['Helpers']['detail']['weight'], H1 + H2 + F1 + H3 + H4)
        self.assertAlmostEqual(who['Workflows']['detail']['weight'], W1 + W2)
        self.assertAlmostEqual(sum(r['share'] for r in self.s['who']), 1.0)
        self.assertEqual(who['Your chat turns']['detail']['chats'], 3)
        self.assertEqual(who['Helpers']['detail']['agents'], 5)
        self.assertEqual((who['Workflows']['detail']['workflows'], who['Workflows']['detail']['agents']), (1, 2))

    def test_model(self):
        m = by_label(self.s['model'])
        self.assertEqual([r['label'] for r in self.s['model']], ['Fable', 'Opus', 'Sonnet', 'Haiku', 'Other'])
        self.assertAlmostEqual(m['Opus']['detail']['weight'], S1_OWN + H1 + F1 + H3 + W2)
        self.assertAlmostEqual(m['Sonnet']['detail']['weight'], H4 + W1 + S3)
        self.assertAlmostEqual(m['Haiku']['detail']['weight'], H2)
        self.assertAlmostEqual(m['Fable']['detail']['weight'], S2)
        self.assertEqual(m['Other']['detail']['requests'], 0)
        self.assertEqual(m['Fable']['detail']['models'], {'claude-fable-5-2': 2})

    def test_kind(self):
        k = by_label(self.s['kind'])
        m1, m2, m3 = w(10, 200, 1000, 5000), w(5, 100, 0, 6000), w(5, 50, 100, 6100)
        m4, m5 = w(3, 40, 0, 6200), w(1, 10, 0, 100)
        # chat turn m1-m3 used Read, Edit, Agent: an edit, so build by tools. H3 "lane:infra" edits: build by
        # tools. W1 phase "Build".
        self.assertAlmostEqual(k['build']['detail']['weight'], m1 + m2 + m3 + H3 + W1)
        self.assertEqual(set(k['build']['detail']['decided_by']), {'tools', 'phase'})
        self.assertAlmostEqual(k['build']['detail']['decided_by']['phase'], W1)
        # chat turn m4-m5 only searched (Grep); H1 "research:..."; H2 "Find watering code" (find beats code)
        self.assertAlmostEqual(k['research']['detail']['weight'], m4 + m5 + H1 + H2)
        self.assertAlmostEqual(k['research']['detail']['decided_by']['label'], H1 + H2)
        # F1 "double-check beds", W2 phase "Verify"
        self.assertAlmostEqual(k['review']['detail']['weight'], F1 + W2)
        # H4 "Morning brief" ran Bash only: labelled, undecided
        self.assertAlmostEqual(k['other']['detail']['weight'], H4)
        # S2 ran Bash, S3 only talked: chats with nothing to go on
        self.assertAlmostEqual(k['not labelled']['detail']['weight'], S2 + S3)
        for name in ('fix', 'docs', 'tests'):
            self.assertEqual(k[name]['detail']['requests'], 0)
        self.assertEqual([r['label'] for r in self.s['kind']], list(usage.KINDS))

    def test_work(self):
        work = self.s['work']
        self.assertEqual([r['label'] for r in work], ['Garden planner v2', 'Weekly notes', 'recipes'])
        g = work[0]['detail']
        all1 = S1_OWN + H1 + H2 + F1 + H3 + H4 + W1 + W2
        self.assertAlmostEqual(g['weight'], all1)
        self.assertAlmostEqual(g['own'], S1_OWN / all1)
        self.assertAlmostEqual(g['helpers'], (H1 + H2 + F1 + H3 + H4) / all1)
        self.assertAlmostEqual(g['workflows'], (W1 + W2) / all1)
        self.assertEqual(g['models'][0]['family'], 'Opus')
        self.assertEqual((g['agents'], g['workflow_runs'], g['session']), (7, 1, S1))
        self.assertAlmostEqual(work[1]['share'], S2 / TOTAL)

    def test_inherited_finding(self):
        f = self.s['inherited']
        # H1, F1, H3 ("inherit") and W2 have no model and ran on the chat's opus. H4 had no model but ran
        # on sonnet; H2 and W1 had a model set.
        self.assertAlmostEqual(f['weight'], H1 + F1 + H3 + W2)
        self.assertAlmostEqual(f['share'], (H1 + F1 + H3 + W2) / TOTAL)
        self.assertEqual(f['agents'], 4)
        self.assertEqual([x['label'] for x in f['top_labels']],
                         ['research:soil types', 'verify:beds', 'double-check beds', 'lane:infra'])
        self.assertEqual([x['family'] for x in f['by_family']], ['Opus'])
        self.assertEqual(f['unknown_share'], 0)

    def test_days_and_workflows(self):
        self.assertEqual([(d['day'], d['requests']) for d in self.s['days']], [('2026-11-10', 17)])
        wf = self.s['workflows']
        self.assertEqual(len(wf), 1)
        self.assertEqual((wf[0]['id'], wf[0]['name'], wf[0]['agents'], wf[0]['chat']),
                         ('wf_garden-001', 'garden-build', 2, 'Garden planner v2'))
        self.assertEqual(wf[0]['labels'], ['lane:beds', 'verify:beds'])
        self.assertEqual(wf[0]['phases'], ['Build', 'Verify'])
        self.assertAlmostEqual(wf[0]['share'], (W1 + W2) / TOTAL)

    def test_damaged_lines_are_counted_not_guessed(self):
        f = self.s['files']
        self.assertEqual((f['bad_lines'], f['torn_lines'], f['unkeyed_lines'], f['vanished']), (1, 1, 1, 0))


class Windows(unittest.TestCase):
    def test_window_edges_and_days(self):
        s = usage.summarize(FIXTURE, NOV9, NOV11, tz=UTC)
        # adds m0 (9,9,9,9) and r0 (100,100,100,100) on Nov 9; r2 sits exactly on the end and stays out
        self.assertEqual(s['totals']['requests'], 19)
        self.assertEqual([(d['day'], d['requests']) for d in s['days']], [('2026-11-09', 2), ('2026-11-10', 17)])
        self.assertAlmostEqual(s['days'][0]['weight'], w(9, 9, 9, 9) + w(100, 100, 100, 100))
        s = usage.summarize(FIXTURE, NOV11, NOV11 + 60, tz=UTC)
        self.assertEqual(s['totals']['requests'], 1)                 # r2, at exactly 00:00:00.000

    def test_a_window_through_the_middle_of_a_chat(self):
        since = NOV10 + 9 * 3600 + 30         # 09:00:30, after m1 started
        until = NOV10 + 10 * 3600 + 5 * 60    # 10:05:00, w1b exactly on the end
        s = usage.summarize(FIXTURE, since, until, tz=UTC)
        # m2 m3 h1a h1b h2a f1a h3a h4a w1a
        self.assertEqual(s['totals']['requests'], 9)
        self.assertAlmostEqual(s['totals']['weight'], w(5, 100, 0, 6000) + w(5, 50, 100, 6100) + H1 + H2 + F1
                               + H3 + H4 + w(50, 500, 3000, 10000))

    def test_days_follow_the_time_zone(self):
        plus10 = timezone(timedelta(hours=10))
        s = usage.summarize(FIXTURE, NOV10, NOV11, tz=plus10)
        days = dict((d['day'], d) for d in s['days'])
        # n2 is 16:00+02:00 = 14:00Z = 00:00 Nov 11 in +10; n1 15:00Z and r1 23:59Z are Nov 11 too
        self.assertEqual(days['2026-11-11']['requests'], 3)
        self.assertAlmostEqual(days['2026-11-11']['weight'], S2 + S3)
        self.assertEqual(days['2026-11-10']['requests'], 14)

    def test_an_empty_window(self):
        s = usage.summarize(FIXTURE, NOV11 + 86400, NOV11 + 2 * 86400)
        self.assertEqual(s['totals']['requests'], 0)
        self.assertEqual(s['work'], [])
        self.assertTrue(all(r['share'] == 0 for r in s['who']))
        self.assertEqual(s['inherited']['share'], 0)

    def test_a_missing_root(self):
        s = usage.summarize(os.path.join(tempfile.gettempdir(), 'no-such-switchboard-root'), NOV10, NOV11)
        self.assertEqual(s['totals']['requests'], 0)


class HandCount(unittest.TestCase):
    """The independent naive counter in tests/hand_count.py must agree with usage.py."""

    def check(self, since, until):
        s = usage.summarize(FIXTURE, since, until, tz=UTC)
        h = hand_count.count(FIXTURE, since, until)
        t = s['totals']
        self.assertEqual(t['requests'], h['requests'])
        self.assertEqual(t['tokens'], dict((k, h[k]) for k in usage.TOKENS))
        self.assertAlmostEqual(t['weight'], h['weight'])
        self.assertEqual(t['tool_calls'], h['tool_calls'])
        for r in s['model']:
            self.assertAlmostEqual(r['detail']['weight'], h['family_weight'].get(r['label'], 0))
        mine = dict((k, r['detail']['weight']) for k, r in zip(usage.WHO, s['who']))
        for k in usage.WHO:
            self.assertAlmostEqual(mine[k], h['who_weight'].get(k, 0))
        return t['requests']

    def test_agree_on_many_windows(self):
        windows = [(NOV10, NOV11), (NOV9, NOV11), (NOV11, NOV11 + 60), (NOV10 + 9 * 3600 + 30, NOV10 + 36300),
                   (NOV9, NOV9 + 86400), (0, 4102444800)]
        self.assertEqual([self.check(a, b) for a, b in windows], [17, 19, 1, 9, 2, 20])

    def test_hand_count_shares_no_code(self):
        with open(os.path.join(HERE, 'hand_count.py')) as f:
            text = f.read()
        self.assertNotIn('switchboard', text.split('"""', 2)[2])


class Titles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-usage-')
        self.root = os.path.join(self.tmp, 'projects')
        shutil.copytree(FIXTURE, self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_chat_with_only_helpers_left_takes_the_helpers_folder(self):
        os.unlink(os.path.join(self.root, '-Users-ana-garden', S1 + '.jsonl'))
        s = usage.summarize(self.root, NOV10, NOV11)
        self.assertIn('garden', [r['label'] for r in s['work']])
        # with the chat gone, its model is unknown, so the finding does not guess; the fork's copy of m1
        # is now the only copy, so the fork owns it
        self.assertEqual(s['inherited']['weight'], 0)
        self.assertAlmostEqual(s['inherited']['unknown_share'] * s['totals']['weight'],
                               H1 + F1 + w(10, 200, 1000, 5000) + H3 + H4 + W2)

    def test_a_helper_without_meta_cannot_be_judged(self):
        os.unlink(os.path.join(self.root, '-Users-ana-garden', S1, 'subagents', 'agent-h1.meta.json'))
        s = usage.summarize(self.root, NOV10, NOV11)
        self.assertAlmostEqual(s['inherited']['weight'], F1 + H3 + W2)
        self.assertAlmostEqual(s['inherited']['unknown_share'] * s['totals']['weight'], H1)
        k = by_label(s['kind'])
        self.assertAlmostEqual(k['research']['detail']['weight'],
                               w(3, 40, 0, 6200) + w(1, 10, 0, 100) + H1 + H2)   # H1 used WebSearch, Read


class Rules(unittest.TestCase):
    def test_kind_table(self):
        cases = [
            # (phase, label, agentType, tools) -> (kind, decided by)
            (('Build', 'research:x', None, ()), ('build', 'phase')),
            (('Lanes', 'fix:login', None, ()), ('fix', 'label')),
            ((None, 'Fix the failing tests', None, ()), ('fix', 'label')),       # fix is listed before tests
            ((None, 'write unit tests', None, ()), ('tests', 'label')),
            ((None, 'e2e run', None, ()), ('tests', 'label')),
            ((None, 'code review', None, ()), ('review', 'label')),             # review before build
            ((None, 'fact-check claims', None, ()), ('review', 'label')),
            ((None, 'README draft', None, ()), ('docs', 'label')),
            ((None, 'research:lawyers', None, ()), ('research', 'label')),
            ((None, 'Explore auth', None, ()), ('research', 'label')),
            ((None, None, 'Explore', ()), ('research', 'type')),
            ((None, None, 'web-fetch', ()), ('research', 'type')),
            ((None, 'implement parser', None, ()), ('build', 'label')),
            ((None, 'lane:infra', 'general-purpose', ['Bash', 'Edit']), ('build', 'tools')),
            ((None, 'lane:infra', 'general-purpose', ['Read', 'Grep', 'WebFetch']), ('research', 'tools')),
            ((None, 'lane:infra', 'general-purpose', ['Read', 'Bash']), ('other', 'none')),
            ((None, 'lane:infra', 'general-purpose', []), ('other', 'none')),
            (('Money', None, 'workflow-subagent', ['Bash']), ('other', 'none')),
            ((None, None, 'general-purpose', ['Bash']), ('not labelled', 'none')),
            ((None, None, None, []), ('not labelled', 'none')),
            ((None, None, None, ['Write']), ('build', 'tools')),
            (('  ', '', None, ['Glob']), ('research', 'tools')),
            ((None, 'Debugging', None, ()), ('fix', 'label')),                   # any case
            ((None, 'prefix-review-suffix', None, ()), ('review', 'label')),
            ((None, 'reviewing-ish', None, ()), ('review', 'label')),
            ((None, 'preview', None, ()), ('other', 'none')),                    # whole words only
            ((None, 'debugger', None, ()), ('other', 'none')),
        ]
        for (phase, label, kind_type, tools), want in cases:
            self.assertEqual(usage.kind_of(phase, label, kind_type, tools), want, (phase, label, kind_type, tools))

    def test_every_kind_in_the_table_is_a_kind(self):
        self.assertEqual(set(k for _, _, k in usage.KIND_RULES), set(usage.KINDS))

    def test_timestamps(self):
        self.assertEqual(usage.parse_ts('2026-11-10T00:00:00.000Z'), NOV10)
        self.assertEqual(usage.parse_ts('2026-11-10T00:00:00Z'), NOV10)
        self.assertEqual(usage.parse_ts('2026-11-10T00:00:00'), NOV10)                 # no zone: UTC
        self.assertEqual(usage.parse_ts('2026-11-10 00:00:00'), NOV10)
        self.assertEqual(usage.parse_ts('2026-11-10T02:00:00+02:00'), NOV10)
        self.assertEqual(usage.parse_ts('2026-11-09T19:00:00-0500'), NOV10)
        self.assertAlmostEqual(usage.parse_ts('2026-11-10T00:00:00.123456Z'), NOV10 + 0.123456)
        self.assertAlmostEqual(usage.parse_ts('2026-11-10T00:00:00.5'), NOV10 + 0.5)
        for bad in (None, '', 'yesterday', '2026-13-01T00:00:00Z', '2026-11-31T00:00:00Z', 1700000000, '2026-11-10'):
            self.assertIsNone(usage.parse_ts(bad), bad)

    def test_families(self):
        self.assertEqual([usage.family(m) for m in ('claude-opus-5-5', 'claude-sonnet-5-5', 'claude-haiku-5-5',
                                                     'claude-fable-5-2', 'Claude-3-Opus', 'gpt-x', '', None, 7)],
                         ['Opus', 'Sonnet', 'Haiku', 'Fable', 'Opus', 'Other', 'Other', 'Other', 'Other'])

    def test_weight(self):
        self.assertAlmostEqual(usage.weight({'input': 100, 'output': 10, 'cache_write': 8, 'cache_read': 1000}),
                               100 + 50 + 10 + 50)              # today’s prices: a cache read is 0.05
        self.assertAlmostEqual(usage.weight([100, 10, 8, 1000]), 210)


class Reading(unittest.TestCase):
    """Torn lines, odd files, files that vanish, spaced JSON, and lines across read chunks."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-usage-')
        self.root = os.path.join(self.tmp, 'projects')
        shutil.copytree(FIXTURE, self.root)
        self.notes = os.path.join(self.root, '-Users-ana-notes', 'bbbbbbbb-0000-4000-8000-000000000002.jsonl')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_torn_last_line_counts_once_it_is_finished(self):
        before = usage.summarize(self.root, NOV10, NOV11)['totals']['requests']
        with open(self.notes, 'a') as f:      # the writer finishes the line
            f.write(',"output_tokens":1}},"requestId":"req_n3","timestamp":"2026-11-10T17:00:00.000Z"}\n')
        s = usage.summarize(self.root, NOV10, NOV11)
        self.assertEqual(s['totals']['requests'], before + 1)
        self.assertEqual(s['files']['torn_lines'], 0)

    def test_lines_split_across_read_chunks(self):
        whole = usage.summarize(self.root, NOV9, NOV11, tz=UTC)
        for size in (1, 7, 100, 4096):
            with mock.patch.object(usage, 'CHUNK', size):
                small = usage.summarize(self.root, NOV9, NOV11, tz=UTC)
            whole.pop('files', None)
            small.pop('files', None)
            self.assertEqual(json.dumps(small, sort_keys=True), json.dumps(whole, sort_keys=True), size)

    def test_spaced_json_is_read_too(self):
        with open(self.notes) as f:
            lines = f.read().split('\n')
        spaced = []
        for line in lines[:-1]:
            try:
                spaced.append(json.dumps(json.loads(line)))           # json.dumps puts spaces after : and ,
            except ValueError:
                spaced.append(line)
        with open(self.notes, 'w') as f:
            f.write('\n'.join(spaced) + '\n')
        s = usage.summarize(self.root, NOV10, NOV11)
        self.assertAlmostEqual(by_label(s['model'])['Fable']['detail']['weight'], S2)
        self.assertEqual(by_label(s['work'])['Weekly notes']['detail']['requests'], 2)

    def test_a_file_that_vanishes_mid_read_is_skipped(self):
        real = usage.read_transcript

        def flaky(path, chat=False):
            if path.endswith('agent-h4.jsonl'):
                raise FileNotFoundError(path)
            return real(path, chat=chat)

        with mock.patch.object(usage, 'read_transcript', flaky):
            s = usage.summarize(self.root, NOV10, NOV11)
        self.assertEqual(s['files']['vanished'], 1)
        self.assertAlmostEqual(s['totals']['weight'], TOTAL - H4)

    def test_a_file_that_vanishes_before_it_is_looked_at(self):
        real_stat = os.stat

        def stat(path, *a, **k):
            if str(path).endswith('agent-h2.jsonl'):
                raise FileNotFoundError(path)
            return real_stat(path, *a, **k)

        with mock.patch.object(usage.os, 'stat', stat):
            s = usage.summarize(self.root, NOV10, NOV11)
        self.assertAlmostEqual(s['totals']['weight'], TOTAL - H2)

    def test_junk_files_and_folders_are_ignored(self):
        with open(os.path.join(self.root, 'stray.jsonl'), 'w') as f:
            f.write('{"type":"assistant"}\n')
        os.makedirs(os.path.join(self.root, '-Users-ana-notes', 'x', 'tool-results'))
        with open(os.path.join(self.root, '-Users-ana-notes', 'x', 'tool-results', 'r.jsonl'), 'w') as f:
            f.write('not json\n')
        with open(os.path.join(self.root, '-Users-ana-notes', 'empty.jsonl'), 'w'):
            pass
        s = usage.summarize(self.root, NOV10, NOV11)
        self.assertAlmostEqual(s['totals']['weight'], TOTAL)

    def test_files_older_than_the_window_are_not_opened(self):
        old = NOV10 - 3600
        for dirpath, _, names in os.walk(os.path.join(self.root, '-Users-ana-recipes')):
            for n in names:
                os.utime(os.path.join(dirpath, n), (old, old))
        with mock.patch.object(usage, 'read_transcript', wraps=usage.read_transcript) as spy:
            s = usage.summarize(self.root, NOV10, NOV11)
        self.assertFalse(any('recipes' in c.args[0] for c in spy.call_args_list))
        self.assertAlmostEqual(s['totals']['weight'], TOTAL - S3)


class Cache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-usage-')
        self.root = os.path.join(self.tmp, 'projects')
        shutil.copytree(FIXTURE, self.root)
        self.cache = os.path.join(self.tmp, 'data', 'usage-cache.json')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_it(self):
        return usage.summarize(self.root, NOV10, NOV11, cache_path=self.cache, tz=UTC)

    def same(self, a, b):
        a, b = dict(a), dict(b)
        a.pop('files')
        b.pop('files')
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_second_call_reads_nothing_and_agrees(self):
        first = self.run_it()
        self.assertEqual((first['files']['read'], first['files']['from_cache']), (10, 0))
        second = self.run_it()
        self.assertEqual((second['files']['read'], second['files']['from_cache']), (0, 10))
        self.same(first, second)
        self.same(first, usage.summarize(self.root, NOV10, NOV11, tz=UTC))
        self.assertEqual(oct(os.stat(self.cache).st_mode & 0o777), '0o600')
        self.assertEqual(os.listdir(os.path.dirname(self.cache)), ['usage-cache.json'])   # no temp file left

    def test_a_changed_file_is_read_again_whole(self):
        self.run_it()
        h4 = os.path.join(self.root, '-Users-ana-garden', S1, 'subagents', 'agent-h4.jsonl')
        with open(h4, 'a') as f:      # compact JSON, the way Claude Code appends
            f.write(json.dumps({'type': 'assistant', 'timestamp': '2026-11-10T09:21:00.000Z', 'requestId': 'req_h4b',
                                'message': {'id': 'msg_h4b', 'model': 'claude-sonnet-5-5', 'content': [],
                                            'usage': {'input_tokens': 1, 'output_tokens': 0,
                                                      'cache_creation_input_tokens': 0,
                                                      'cache_read_input_tokens': 0}}},
                               separators=(',', ':')) + '\n')
        s = self.run_it()
        self.assertEqual((s['files']['read'], s['files']['from_cache']), (1, 9))
        self.assertAlmostEqual(s['totals']['weight'], TOTAL + 1)
        self.same(s, usage.summarize(self.root, NOV10, NOV11, tz=UTC))

    def test_a_broken_cache_is_rebuilt(self):
        first = self.run_it()
        for junk in ('{not json', '[]', '{"version": 999, "files": {}}', '{"version": 1, "files": []}', ''):
            with open(self.cache, 'w') as f:
                f.write(junk)
            again = self.run_it()
            self.assertEqual(again['files']['read'], 10, junk)
            self.same(first, again)

    def test_a_damaged_entry_is_read_again(self):
        self.run_it()
        with open(self.cache) as f:
            c = json.load(f)
        path = sorted(c['files'])[0]
        c['files'][path]['data'] = 'garbage'
        with open(self.cache, 'w') as f:
            json.dump(c, f)
        s = self.run_it()
        self.assertEqual(s['files']['read'], 1)
        self.assertAlmostEqual(s['totals']['weight'], TOTAL)

    def test_deleted_files_leave_the_cache(self):
        self.run_it()
        gone = os.path.join(self.root, '-Users-ana-recipes', 'cccccccc-0000-4000-8000-000000000003.jsonl')
        os.unlink(gone)
        self.run_it()
        with open(self.cache) as f:
            self.assertNotIn(gone, json.load(f)['files'])

    def test_the_cache_holds_numbers_not_text(self):
        self.run_it()
        with open(self.cache) as f:
            text = f.read()
        for words in ('made-up', 'msg_m1', 'req_m1', 'tu_1', 'thinking', 'signature', '/Users/ana/garden'):
            self.assertNotIn(words, text)
        self.assertIn('Garden planner v2', text)     # titles and labels are kept: they are what the page shows

    def test_cannot_write_the_cache(self):
        blocker = os.path.join(self.tmp, 'file')
        with open(blocker, 'w') as f:
            f.write('x')
        s = usage.summarize(self.root, NOV10, NOV11, cache_path=os.path.join(blocker, 'cache.json'))
        self.assertAlmostEqual(s['totals']['weight'], TOTAL)


class Command(unittest.TestCase):
    def test_prints_numbers_titles_and_labels_only(self):
        out = io.StringIO()
        with mock.patch.object(usage.time, 'time', return_value=NOV11):
            usage.main(['--root', FIXTURE, '--days', '2', '--no-cache'], out=out)
        text = out.getvalue()
        self.assertTrue(text.startswith('Work, '), text)
        self.assertIn('Garden planner v2', text)
        self.assertIn('weighted tokens', text)
        for secret in ('made-up', 'msg_', 'req_', 'tu_', '/Users/ana'):
            self.assertNotIn(secret, text)

    def test_json(self):
        out = io.StringIO()
        usage.main(['--root', FIXTURE, '--day', '2026-11-10', '--no-cache', '--json'], out=out)
        s = json.loads(out.getvalue())
        self.assertIn('totals', s)
        self.assertNotIn('made-up', out.getvalue())

    def test_writes_its_cache_where_told(self):
        tmp = tempfile.mkdtemp(prefix='switchboard-usage-')
        try:
            cache = os.path.join(tmp, 'c.json')
            usage.main(['--root', FIXTURE, '--day', '2026-11-10', '--cache', cache], out=io.StringIO())
            self.assertTrue(os.path.exists(cache))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class HelperRows(unittest.TestCase):
    """One row per kind of helper, its model and its chat's: what models.would_have_moved needs to be exact."""

    def setUp(self):
        self.s = usage.summarize(FIXTURE, NOV9, NOV9 + 7 * 86400)

    def test_rows_add_up_to_the_helpers_and_workflows(self):
        rows = self.s['helper_rows']
        who = dict((r['label'], r['share']) for r in self.s['who'])
        self.assertAlmostEqual(sum(r['share'] for r in rows), who['Helpers'] + who['Workflows'], places=12)
        self.assertEqual(sum(r['requests'] for r in rows),
                         sum(r['detail']['requests'] for r in self.s['who'] if r['label'] != 'Your chat turns'))

    def test_no_model_set_on_the_chat_s_model_is_usage_s_inherited_share(self):
        base = sum(r['share'] for r in self.s['helper_rows']
                   if r['set_model'] in (None, 'inherit') and r['family'] == r['chat_family'])
        self.assertAlmostEqual(base, self.s['inherited']['share'], places=12)

    def test_would_have_moved_is_exact(self):
        from switchboard import models
        w = models.would_have_moved(self.s, lookups=False)
        self.assertTrue(w['exact'])
        # Moved: the general-purpose helper and the workflow agent that named no model and ran on the chat's Opus.
        # Not moved: the fork (always the chat's model) and the helper whose definition says inherit.
        by = dict((r['label'], r['share']) for r in self.s['helper_rows'])
        self.assertAlmostEqual(w['share'], by['research:soil types'] + by['verify:beds'], places=12)
        self.assertEqual(sorted(x['reason'] for x in w['not_counted']), ['fork', 'inherit'])
        self.assertAlmostEqual(w['no_model_set'], self.s['inherited']['share'], places=12)


if __name__ == '__main__':
    unittest.main()
