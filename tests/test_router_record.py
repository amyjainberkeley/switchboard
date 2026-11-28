import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# What the adapters leave behind (switchboard/router/record.py): the decisions file read with bad lines counted and
# kept under 1 MB, the signal files read and removed (only well-formed ones, at most 200 a call), and each decision
# said in one plain History sentence made from fixed words.
import json
import os
import shutil
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))
sys.path.insert(0, HERE)

from switchboard import core, history  # noqa: E402
from switchboard.router import record  # noqa: E402
from test_providers_words import problems  # noqa: E402

T = 1764239012.5


def rec(**kw):
    r = {'t': T, 'agent': 'claude', 'where': 'helper', 'type': 'general-purpose', 'kind': 'find', 'did': 'model',
         'from_model': 'opus', 'model': 'haiku', 'effort': 'medium', 'to': None, 'reason': 'route',
         'why': 'your route for lookups', 'passed': [], 'plan': 1764239000.0}
    r.update(kw)
    return r


class Base(unittest.TestCase):
    def setUp(self):
        p = core.data_path('router')
        if os.path.isdir(p):
            shutil.rmtree(p)
        os.makedirs(p)

    def append(self, *lines):
        with open(record.path(), 'ab') as f:
            for x in lines:
                f.write((x if isinstance(x, str) else json.dumps(x)).encode('utf-8') + b'\n')


class Decisions(Base):
    def test_no_file(self):
        shutil.rmtree(core.data_path('router'))
        self.assertEqual(record.read(), ([], 0))
        self.assertEqual(record.trim(), 0)

    def test_bad_lines_are_passed_over_and_counted(self):
        self.append(rec(t=1.0), 'not json', '[1]', rec(agent='Not An Id'), rec(did='maybe'), rec(where='desk'),
                    rec(kind=5), rec(passed=[['codex']]), rec(model=7), '{"t": NaN}', rec(t=2.0), '', rec(t=3.0),
                    rec(why=5))
        recs, bad = record.read()
        self.assertEqual([r['t'] for r in recs], [1.0, 2.0, 3.0])
        self.assertEqual(bad, 10)
        self.assertEqual([r['t'] for r in record.read(2)[0]], [2.0, 3.0])
        self.assertEqual(record.read(0)[0], [])

    def test_a_kind_that_could_not_be_read_and_no_why_are_good(self):
        self.append(rec(kind=None, why=None, reason='default'), rec(why=None))
        self.assertEqual(record.read(), ([rec(kind=None, why=None, reason='default'), rec(why=None)], 0))
        older = rec()
        del older['why']
        self.assertTrue(record.good(older))

    def test_a_line_cut_by_the_read_limit_is_not_counted(self):
        self.append(*[rec(t=float(i)) for i in range(40)])
        with mock.patch.object(record, 'READ_MAX', 1000):
            recs, bad = record.read()
        self.assertEqual(bad, 0)
        self.assertEqual(recs[-1]['t'], 39.0)
        self.assertLess(len(recs), 40)

    def test_trim_keeps_the_newest_whole_lines_under_1_mb(self):
        line = json.dumps(rec()) + '\n'
        n = record.MAX_BYTES // len(line) + 200
        self.append(*[rec(t=float(i)) for i in range(n)])
        self.assertGreater(os.path.getsize(record.path()), record.MAX_BYTES)
        took = record.trim()
        size = os.path.getsize(record.path())
        self.assertLessEqual(size, record.KEEP_BYTES)
        self.assertGreater(took, 0)
        recs, bad = record.read()
        self.assertEqual(bad, 0)
        self.assertEqual(recs[-1]['t'], float(n - 1))
        self.assertEqual(recs[0]['t'], float(n - len(recs)))
        with open(record.path(), 'rb') as f:
            self.assertTrue(f.read().endswith(b'\n'))
        self.assertEqual(oct(os.stat(record.path()).st_mode & 0o777), oct(0o600))

    def test_trim_leaves_a_small_file_alone(self):
        self.append(rec())
        before = os.stat(record.path())
        self.assertEqual(record.trim(), 0)
        self.assertEqual(os.stat(record.path()).st_mtime_ns, before.st_mtime_ns)

    def test_trim_keeps_what_was_appended_while_it_ran(self):
        line = json.dumps(rec()) + '\n'
        self.append(*[rec(t=float(i)) for i in range(record.MAX_BYTES // len(line) + 2000)])
        self.assertGreater(os.path.getsize(record.path()), record.MAX_BYTES)
        real_open, calls = open, []

        def opening(p, mode='r', *a, **k):
            calls.append(p)
            if len(calls) == 2:                     # an adapter appends between the read and the write
                with real_open(p, 'ab') as f:
                    f.write((json.dumps(rec(t=9e9)) + '\n').encode('utf-8'))
            return real_open(p, mode, *a, **k)

        with mock.patch('switchboard.router.record.open', opening, create=True):
            self.assertGreater(record.trim(), 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(record.read(1)[0][0]['t'], 9e9)


class Fold(Base):
    """An adapter writes each decision to its own file; fold() moves the settled ones into decisions.jsonl."""

    NOW = 1764239100.0

    def put(self, name, body, age=10.0):
        folder = core.data_path(record.DECIDED)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(body if isinstance(body, str) else json.dumps(body))
        os.utime(path, (self.NOW - age, self.NOW - age))
        return path

    def name(self, at, n):
        return '%013d-%08x.json' % (int(at * 1000), n)

    def left(self):
        return sorted(os.listdir(core.data_path(record.DECIDED)))

    def test_settled_files_are_appended_in_name_order_and_removed(self):
        self.append(rec(t=1.0))
        self.put(self.name(self.NOW - 5, 2), rec(t=3.0))
        self.put(self.name(self.NOW - 9, 1), rec(t=2.0))
        young = self.put(self.name(self.NOW - 1, 3), rec(t=4.0), age=1.0)
        self.assertEqual(record.fold(self.NOW), (2, 0))
        self.assertEqual([r['t'] for r in record.read()[0]], [1.0, 2.0, 3.0])
        self.assertEqual(self.left(), [os.path.basename(young)])
        self.assertEqual(record.fold(self.NOW + 2), (1, 0))
        self.assertEqual([r['t'] for r in record.read()[0]], [1.0, 2.0, 3.0, 4.0])
        self.assertEqual(self.left(), [])
        self.assertEqual(record.read()[0][1], rec(t=2.0))

    def test_a_new_record_file_is_private(self):
        os.rmdir(core.data_path('router'))
        self.put(self.name(self.NOW - 5, 1), rec())
        self.assertEqual(record.fold(self.NOW), (1, 0))
        self.assertEqual(oct(os.stat(record.path()).st_mode & 0o777), oct(0o600))

    def test_a_broken_file_waits_an_hour_after_its_name_then_goes_and_is_counted(self):
        at = self.NOW - 1800
        files = [self.put(self.name(at, 1), 'not json'), self.put(self.name(at, 2), '[1]'),
                 self.put(self.name(at, 3), rec(did='maybe')), self.put(self.name(at, 4), rec(note='x' * 5000)),
                 self.put(self.name(at, 5), '{"t": 1')]
        self.assertEqual(record.fold(self.NOW), (0, 0))
        self.assertTrue(all(os.path.exists(p) for p in files))
        self.assertEqual(record.fold(at + 3600), (0, 5))
        self.assertEqual(self.left(), [])
        self.assertFalse(os.path.exists(record.path()))

    def test_a_link_is_never_followed(self):
        target = self.put('elsewhere.txt', rec())
        link = os.path.join(core.data_path(record.DECIDED), self.name(self.NOW - 4000, 1))
        os.symlink(target, link)
        self.assertEqual(record.fold(self.NOW), (0, 1))
        self.assertFalse(os.path.lexists(link))
        self.assertTrue(os.path.exists(target))
        self.assertFalse(os.path.exists(record.path()))

    def test_a_name_not_of_the_form_is_left_alone(self):
        names = ['notes.txt', '.1764239000000-00000001.json.tmp', '176423900000-00000001.json']
        for n in names:
            self.put(n, rec(), age=9000)
        self.assertEqual(record.fold(self.NOW), (0, 0))
        self.assertEqual(self.left(), sorted(names))

    def test_at_most_500_a_call(self):
        for i in range(520):
            self.put(self.name(self.NOW - 100 + i / 1000, i), rec(t=float(i)))
        self.assertEqual(record.fold(self.NOW), (500, 0))
        self.assertEqual(record.fold(self.NOW), (20, 0))
        self.assertEqual([r['t'] for r in record.read()[0]], [float(i) for i in range(520)])

    def test_no_folder(self):
        self.assertEqual(record.fold(self.NOW), (0, 0))

    def test_a_file_gone_before_it_is_read_is_passed_over(self):
        path = self.put(self.name(self.NOW - 5, 1), rec())
        real = os.open

        def taken(p, *a, **k):
            if p == path:
                os.remove(path)
            return real(p, *a, **k)

        with mock.patch('switchboard.router.record.os.open', taken):
            self.assertEqual(record.fold(self.NOW), (0, 0))


class Signals(Base):
    def put(self, name, body):
        folder = core.data_path(record.SIGNALS)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, name), 'w', encoding='utf-8') as f:
            f.write(body if isinstance(body, str) else json.dumps(body))
        return os.path.join(folder, name)

    def test_well_formed_ones_are_read_and_removed(self):
        a = self.put('1764239100000-0000000a.json', {'t': 1764239100.0, 'agent': 'claude', 'what': 'at_line',
                                                     'window': 'five_hour', 'pct': 91})
        b = self.put('1764239200000-0000000b.json', {'t': 1764239200.0, 'agent': 'codex', 'what': 'limit_stop'})
        got, left = record.signals()
        self.assertEqual(got, [{'t': 1764239100.0, 'agent': 'claude', 'what': 'at_line', 'window': 'five_hour',
                                'pct': 91},
                               {'t': 1764239200.0, 'agent': 'codex', 'what': 'limit_stop', 'window': None,
                                'pct': None}])
        self.assertEqual(left, 0)
        self.assertFalse(os.path.exists(a) or os.path.exists(b))
        self.assertEqual(record.signals(), ([], 0))

    def test_anything_else_is_left_and_counted(self):
        ok = {'t': 1.0, 'agent': 'claude', 'what': 'at_line', 'window': 'week', 'pct': 96}
        bad = [('1764239100000-00000001.json', 'not json'),
               ('1764239100000-00000002.json', dict(ok, what='switch')),
               ('1764239100000-00000003.json', dict(ok, agent='me@x.example')),
               ('1764239100000-00000004.json', dict(ok, pct='96')),
               ('1764239100000-00000005.json', dict(ok, window=None)),
               ('1764239100000-00000006.json', dict(ok, window='Five Hour')),
               ('1764239100000-00000007.json', '[1]'),
               ('1764239100000-00000008.json', dict(ok, note='x' * 5000)),
               ('a-signal.json', ok),
               ('.1764239100000-00000009.json.tmp', ok)]
        paths = [self.put(n, b) for n, b in bad]
        link = os.path.join(core.data_path(record.SIGNALS), '1764239100000-0000000c.json')
        os.symlink(paths[1], link)
        got, left = record.signals()
        self.assertEqual((got, left), ([], len(bad) + 1))
        self.assertTrue(all(os.path.lexists(p) for p in paths + [link]))

    def test_at_most_200_a_call(self):
        for i in range(250):
            self.put('%013d-%08x.json' % (1764239100000 + i, i), {'t': float(i), 'agent': 'codex',
                                                                  'what': 'limit_stop'})
        got, left = record.signals()
        self.assertEqual((len(got), left), (200, 0))
        self.assertEqual([g['t'] for g in got[:2]], [0.0, 1.0])
        got, left = record.signals()
        self.assertEqual((len(got), left), (50, 0))

    def test_no_folder(self):
        self.assertEqual(record.signals(), ([], 0))


class Sentences(unittest.TestCase):
    CASES = (
        (rec(), 'A lookup helper in Claude ran on Haiku at medium: your route for lookups.'),
        (rec(kind='write', model='sonnet', effort=None, where='workflow'),
         'A workflow agent in Claude ran on Sonnet: your route for writing.'),
        (rec(agent='codex', where='chat', kind='fix', did='effort', model=None, effort='high'),
         'Your chat in Codex ran at high effort: your route for fixes.'),
        # A hand-over is told by the hand helper's own record (type switchboard-hand), once.
        (rec(kind='review', did='hand', to='codex', model='gpt-6.2-astra', effort='high', type='switchboard-hand'),
         'A review from Claude went to Codex on GPT-6.2 Astra at high: your route for reviews.'),
        (rec(kind='build', did='hand', to='codex', model=None, effort=None, type='switchboard-hand'),
         'A build job from Claude went to Codex: your route for builds.'),
        (rec(kind='review', did='kept', model=None, effort=None),
         'A review helper in Claude kept Opus, the model it was started with.'),
        (rec(kind='review', did='kept', from_model=None, model=None, effort=None),
         'A review helper in Claude kept the model it was started with.'),
        (rec(did='none', model=None, effort=None, reason='all_passed',
             passed=[['claude', 'out'], ['codex', 'signed_out']]),
         'A lookup helper in Claude stayed as it was: no choice on your route for lookups could take it now '
         '(Claude at your lines, Codex signed out).'),
        (rec(did='none', model=None, effort=None, reason='all_passed', passed=[]),
         'A lookup helper in Claude stayed as it was: no choice on your route for lookups could take it now.'),
        # "No choice could take it" is said only when every choice was passed. A route that names nothing to change,
        # a word that could not be used, or a hand-over not made yet is not that, and says nothing.
        (rec(did='none', model=None, effort=None, reason='stays'), None),
        (rec(did='none', model=None, effort=None, reason='hand_later', to='codex'), None),
        (rec(did='none', model=None, effort=None, reason='bad_value'), None),
        (rec(did='none', model=None, effort=None, reason='route'), None),
        (rec(did='would', to='codex', model='gpt-6-luna', reason='hand_later'), None),
        (rec(did='would', model=None, effort=None, reason='stays'), None),
        (rec(did='would'), 'Trial run, nothing changed: a lookup helper in Claude would have run on Haiku at medium.'),
        (rec(did='would', where='chat', model=None, effort='low'),
         'Trial run, nothing changed: your chat in Claude would have run at low effort.'),
        (rec(did='would', to='codex', model='gpt-6-luna'),
         'Trial run, nothing changed: a lookup from Claude would have gone to Codex on GPT-6 Luna at medium.'),
        (rec(did='would', model=None, effort=None),
         'Trial run, nothing changed: a lookup helper in Claude would have stayed as it was.'),
        (rec(kind='something new', model='not a model word!'), 'A helper in Claude ran on a model at medium: your route.'),
        (rec(model='haiku-9'), 'A lookup helper in Claude ran on haiku-9 at medium: your route for lookups.'),
        (rec(why='the preset you turned on, for lookups'),
         'A lookup helper in Claude ran on Haiku at medium: the preset you turned on, for lookups.'),
        (rec(why='zebra words'), 'A lookup helper in Claude ran on Haiku at medium: your route for lookups.'),
        (rec(where='workflow', kind='build', did='effort', model=None, effort='xhigh', reason='default', why=None),
         'A workflow agent in Claude ran at xhigh effort: your default for workflow agents.'),
        (rec(kind='build', did='model', model='sonnet', effort=None, reason='default', why=None),
         'A build helper in Claude ran on Sonnet: your default for helpers.'),
        (rec(where='chat', kind=None, did='effort', model=None, effort='high', reason='default', why=None),
         'Your chat in Claude ran at high effort: your default for your chat.'),
        (rec(kind=None, did='model', model='haiku', effort=None, reason='default', why=None),
         'A helper in Claude ran on Haiku: your default for helpers.'),
        (rec(where='workflow', kind='build', did='kept', model=None, effort='xhigh', reason='effort_lower', why=None),
         'A workflow agent in Claude kept its own effort, already at or below xhigh: your default for workflow '
         'agents.'),
        (rec(where='chat', did='kept', model=None, effort=None, reason='effort_lower', why=None),
         'Your chat in Claude kept its own effort: your default for your chat.'),
        (rec(kind='review', did='kept', model='opus', effort=None, reason='caller_model'),
         'A review helper in Claude kept Opus, the model it was started with.'),
        # The model a caller named is its own text: shown only when the list of models knows it.
        (rec(kind='review', did='kept', from_model='zebra-notes', model='zebra-notes', effort=None,
             reason='caller_model'),
         'A review helper in Claude kept the model it was started with.'),
        (rec(where='workflow', did='would', model=None, effort='xhigh', reason='default', why=None),
         'Trial run, nothing changed: a workflow agent in Claude would have run at xhigh effort.'),
    )

    def test_each_decision_in_one_sentence(self):
        for r, want in self.CASES:
            with self.subTest(want=want):
                self.assertEqual(record.line(r), want)

    def test_lines_keeps_time_and_order_and_skips_what_says_nothing(self):
        recs = [rec(t=1.0), rec(t=2.0, did='model', model=None, effort=None), {'t': 3.0}, rec(t=4.0, did='kept')]
        got = record.lines(recs)
        self.assertEqual([t for t, _ in got], [1.0, 4.0])

    def test_nothing_an_adapter_wrote_is_shown_as_it_is(self):
        marker = 'zebra-marker'
        r = rec(type=marker, reason='all_passed', from_model=marker + ' x', passed=[['claude', marker]], did='none')
        text = record.line(r)
        self.assertNotIn(marker, text)
        self.assertIsNone(record.line(rec(reason=marker, did='none')))
        # A one-word marker as the model a caller named, which a project's name could be.
        for r in (rec(did='kept', from_model=marker, model=marker, reason='caller_model'),
                  rec(did='kept', from_model=marker, model=None, effort=None, reason='caller_model')):
            self.assertNotIn(marker, record.line(r))
        self.assertNotIn('general-purpose', record.line(rec()))

    def test_the_word_rules_hold_for_every_line(self):
        for r, want in self.CASES:
            if want is None:
                continue
            with self.subTest(want=want):
                self.assertEqual(problems(want), [])
                self.assertFalse(any(rx.match(want) for rx, _, _, _ in history.RULES))


if __name__ == '__main__':
    unittest.main()
