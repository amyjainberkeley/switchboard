import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""The person's routes (switchboard/providers/routes.py and route_presets.json).

- A choice is written as words a person types: codex, claude:sonnet, claude:sonnet:high. Every form parses and prints
  back; an unknown kind, provider, model or effort is refused with a sentence that lists the known ones.
- The table is kept in the data folder (locked, whole or not at all, mode 0600). A fresh install has none: every kind
  follows the order. set, reset one kind, reset all.
- One preset ships, off: by-the-job, rows that name a model inside each app (find: each app's small model at medium;
  write: each app's middle model at medium), each row with a source, a day read and what it rests on. Turning it off puts back exactly what was there before, byte for
  byte; a row the person set since stays. A preset row without a source, or naming a model the list does not know,
  is refused.
- Two writers at once lose nothing.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

from switchboard import core  # noqa: E402
from switchboard.providers import catalog, kinds, routes, words  # noqa: E402
from switchboard.providers.job import RouteChoice as C  # noqa: E402


class Clean(unittest.TestCase):
    def setUp(self):
        self.path = core.data_path(routes.ROUTES_FILE)
        for p in (self.path, core.data_path(catalog.OWN_FILE)):
            self.addCleanup(self.remove, p)
            self.remove(p)

    @staticmethod
    def remove(path):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass

    def raw(self):
        with open(self.path, 'rb') as f:
            return f.read()

    def refused(self, fn, sentence):
        with self.assertRaises(routes.RouteError) as got:
            fn()
        self.assertEqual(str(got.exception), sentence)


class Words(Clean):
    def test_every_form_parses_and_prints_back(self):
        for word, choice in (('codex', C('codex')), ('claude:sonnet', C('claude', 'sonnet')),
                             ('claude:sonnet:high', C('claude', 'sonnet', 'high')),
                             ('codex:gpt-6.2-astra:xhigh', C('codex', 'gpt-6.2-astra', 'xhigh')),
                             ('claude:haiku:medium', C('claude', 'haiku', 'medium'))):
            with self.subTest(word=word):
                self.assertEqual(routes.parse(word), choice)
                self.assertEqual(routes.word_of(choice), word)

    def test_case_is_made_the_lists_own(self):
        self.assertEqual(routes.parse(' Claude:SONNET:High '), C('claude', 'sonnet', 'high'))
        self.assertEqual(routes.word_of(routes.parse('CODEX:GPT-6-LUNA')), 'codex:gpt-6-luna')

    def test_a_word_of_another_shape(self):
        for word in ('', 'claude:', ':sonnet', 'claude::high', 'a:b:c:d', 'claude sonnet', 'codex:'):
            with self.subTest(word=word):
                self.refused(lambda: routes.parse(word), words.ROUTE_WORD.format(word=words.one_line(word, 60)))

    def test_an_unknown_provider_lists_the_known_ones(self):
        self.refused(lambda: routes.parse('zeta:fast'), words.UNKNOWN_PROVIDER.format(word='zeta',
                                                                                      ids='claude, codex'))

    def test_an_unknown_model_lists_the_known_ones(self):
        known = ', '.join(m.id for m in catalog.models('codex'))
        self.refused(lambda: routes.parse('codex:gpt-9'), words.UNKNOWN_MODEL.format(name='Codex', word='gpt-9',
                                                                                     known=known))

    def test_an_unknown_effort_lists_the_known_ones(self):
        known = ', '.join(catalog.efforts('claude'))
        self.refused(lambda: routes.parse('claude:sonnet:ultra'),
                     words.UNKNOWN_EFFORT.format(name='Claude', word='ultra', known=known))

    def test_an_unknown_kind_lists_the_kinds(self):
        self.refused(lambda: routes.set_kind('deploy', ['codex']),
                     words.UNKNOWN_KIND.format(word='deploy', kinds=', '.join(kinds.KINDS)))
        self.assertFalse(os.path.exists(self.path))


class TheTable(Clean):
    def test_a_fresh_install_has_no_routes(self):
        table = routes.load()
        self.assertEqual((dict(table.routes), table.preset), ({}, None))
        self.assertEqual(table.get('review'), ())
        self.assertFalse(os.path.exists(self.path))

    def test_set_keeps_it_in_order_in_a_private_file(self):
        got = routes.set_kind('review', ['codex:gpt-6.2-astra:high', 'Claude:Sonnet'])
        self.assertEqual(got, (C('codex', 'gpt-6.2-astra', 'high'), C('claude', 'sonnet')))
        self.assertEqual(routes.load().get('review'), got)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.path)).st_mode), 0o700)
        with open(self.path) as f:
            self.assertEqual(json.load(f), {'v': 1, 'routes': {'review': [
                {'provider': 'codex', 'model': 'gpt-6.2-astra', 'effort': 'high'},
                {'provider': 'claude', 'model': 'sonnet'}]}})

    def test_set_again_replaces_the_kind_and_leaves_the_others(self):
        routes.set_kind('review', ['codex'])
        routes.set_kind('find', ['claude:haiku'])
        routes.set_kind('review', ['claude'])
        self.assertEqual(dict(routes.load().routes), {'review': (C('claude'),), 'find': (C('claude', 'haiku'),)})

    def test_choices_as_route_choices_too(self):
        self.assertEqual(routes.set_kind('other', [C('codex', 'gpt-6-luna')]), (C('codex', 'gpt-6-luna'),))

    def test_a_provider_named_twice_or_nothing_named(self):
        self.refused(lambda: routes.set_kind('review', ['codex', 'claude:sonnet', 'codex:gpt-6-luna']),
                     words.ROUTE_TWICE.format(name='Codex'))
        self.refused(lambda: routes.set_kind('review', []), words.ROUTE_EMPTY.format(kind='review'))
        self.assertFalse(os.path.exists(self.path))

    def test_reset_one_kind_then_all(self):
        routes.set_kind('review', ['codex'])
        routes.set_kind('fix', ['claude'])
        routes.reset('review')
        self.assertEqual(dict(routes.load().routes), {'fix': (C('claude'),)})
        routes.reset()
        self.assertEqual(dict(routes.load().routes), {})
        self.assertFalse(os.path.exists(self.path))
        self.refused(lambda: routes.reset('deploy'), words.UNKNOWN_KIND.format(word='deploy',
                                                                               kinds=', '.join(kinds.KINDS)))

    def write_raw(self, text):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w') as f:
            f.write(text)

    def test_a_file_that_cannot_be_read_is_said_and_never_written_over(self):
        self.write_raw('{"v": 1, "routes": {')
        bad = words.ROUTES_BAD.format(path=self.path)
        self.refused(routes.load, bad)                                   # not "no routes"
        with self.assertRaises(routes.RouteError) as got:
            routes.set_kind('review', ['codex'])
        self.assertIn(self.path, str(got.exception))
        with open(self.path) as f:
            self.assertEqual(f.read(), '{"v": 1, "routes": {')
        routes.reset()                                                   # what the sentence names clears it
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(dict(routes.load().routes), {})

    def test_a_file_naming_what_is_not_known_is_said(self):
        bad = words.ROUTES_BAD.format(path=self.path)
        for doc in ({'v': 1, 'routes': {'fix': [{'provider': 'x\u001b[2J'}]}},
                    {'v': 1, 'routes': {'fix': [{'provider': 'codex', 'model': 'gpt-9'}]}},
                    {'v': 1, 'routes': {'fix': [{'provider': 'codex', 'model': 'gpt-6-luna', 'effort': 'extreme'}]}},
                    {'v': 1, 'routes': {'deploy': [{'provider': 'codex'}]}},
                    {'v': 1, 'routes': {'fix': [{'provider': 'codex'}, {'provider': 'codex'}]}},
                    {'v': 1, 'routes': {'fix': 'codex'}},
                    {'v': 1, 'routes': {}, 'preset': {'name': 'x\u001b]0;made up\u0007', 'wrote': {}}},
                    {'v': 1, 'routes': {}, 'preset': {'name': 'by-the-job', 'wrote': {'fix': [{'provider': 'zeta'}]}}},
                    ['not', 'an', 'object']):
            with self.subTest(doc=doc):
                self.write_raw(json.dumps(doc))
                self.refused(routes.load, bad)
                self.assertNotIn('\x1b', bad)
        self.write_raw(json.dumps({'v': 1, 'routes': {'fix': [{'provider': 'codex', 'model': 'gpt-6-luna'}]}}))
        self.assertEqual(routes.load().get('fix'), (C('codex', 'gpt-6-luna'),))

    def test_a_word_from_the_file_is_printed_on_one_line(self):
        self.assertEqual(routes.word_of(C('x\x1b[2Jy\nz')), 'xy z')


class ThePreset(Clean):
    def test_the_shipped_preset_and_its_rows(self):
        got = routes.presets()
        self.assertEqual(list(got), ['by-the-job'])
        self.assertEqual(got['by-the-job'], {
            'find': (C('claude', 'haiku', 'medium'), C('codex', 'gpt-6-luna', 'medium')),
            'write': (C('claude', 'sonnet', 'medium'), C('codex', 'gpt-6.2-sol', 'medium'))})

    def test_every_shipped_row_says_where_and_when(self):
        with open(routes.PRESETS, encoding='utf-8') as f:
            doc = json.load(f)
        for name, preset in doc['presets'].items():
            for kind, row in preset['rows'].items():
                with self.subTest(preset=name, kind=kind):
                    self.assertTrue(row['source'].strip())
                    self.assertRegex(row['read'], r'^\d{4}-\d{2}-\d{2}\Z')
                    self.assertIn("maker's own line", row['rests_on'])

    def test_on_then_off_from_nothing_leaves_nothing(self):
        routes.preset('by-the-job')
        table = routes.load()
        self.assertEqual(table.preset, 'by-the-job')
        self.assertEqual(table.get('find'), (C('claude', 'haiku', 'medium'), C('codex', 'gpt-6-luna', 'medium')))
        self.assertEqual(table.from_preset, frozenset(('find', 'write')))
        routes.preset(None)
        self.assertEqual((dict(routes.load().routes), routes.load().preset), ({}, None))
        self.assertFalse(os.path.exists(self.path))

    def test_off_puts_back_byte_for_byte(self):
        routes.set_kind('review', ['codex:gpt-6.2-astra:high'])
        routes.set_kind('build', ['claude:opus'])
        before = self.raw()
        routes.preset('by-the-job')
        self.assertEqual(routes.load().get('review'), (C('codex', 'gpt-6.2-astra', 'high'),))     # the person's
        self.assertEqual(routes.load().get('build'), (C('claude', 'opus'),))
        routes.preset('by-the-job')                                      # on again: no change
        routes.preset(None)
        self.assertEqual(self.raw(), before)

    def made_up_preset(self, rows):
        """A presets file of one made-up preset, in place of the shipped one for this test."""
        folder = tempfile.mkdtemp(prefix='sb-presets-')
        self.addCleanup(__import__('shutil').rmtree, folder, True)
        path = os.path.join(folder, 'route_presets.json')
        doc = {'v': 1, 'presets': {'made-up': {'rows': {
            k: {'choices': v, 'rests_on': 'A made-up line.', 'source': 'made up', 'read': '2026-11-25'}
            for k, v in rows.items()}}}}
        with open(path, 'w') as f:
            json.dump(doc, f)
        patcher = mock.patch.object(routes, 'PRESETS', path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_preset_never_replaces_a_row_the_person_set(self):
        self.made_up_preset({'find': ['claude:haiku:medium'], 'review': ['claude:sonnet:high']})
        routes.set_kind('review', ['codex', 'claude:sonnet:high'])
        before = self.raw()
        self.assertEqual(routes.preset('made-up'), (('find',), ('review',)))
        self.assertEqual(routes.load().get('review'), (C('codex'), C('claude', 'sonnet', 'high')))
        self.assertEqual(routes.load().get('find'), (C('claude', 'haiku', 'medium'),))
        self.assertEqual(routes.preset('made-up'), (('find',), ('review',)))         # on again: the same, no change
        routes.preset(None)
        self.assertEqual(self.raw(), before)

    def test_a_preset_row_is_tried_in_the_persons_order(self):
        self.made_up_preset({'find': ['codex:gpt-6-luna:medium', 'claude:haiku:medium']})
        routes.set_kind('review', ['codex', 'claude:sonnet'])
        routes.preset('made-up')
        table = routes.load()
        self.assertEqual(table.from_preset, frozenset(('find',)))
        self.assertEqual(table.route_for('find', ['claude', 'codex']),
                         (C('claude', 'haiku', 'medium'), C('codex', 'gpt-6-luna', 'medium')))
        self.assertEqual(table.route_for('find', ['codex', 'claude']),
                         (C('codex', 'gpt-6-luna', 'medium'), C('claude', 'haiku', 'medium')))
        self.assertEqual(table.route_for('find', ['claude']),                     # one not in the order goes last
                         (C('claude', 'haiku', 'medium'), C('codex', 'gpt-6-luna', 'medium')))
        self.assertEqual(table.route_for('review', ['claude', 'codex']), (C('codex'), C('claude', 'sonnet')))
        self.assertEqual(table.route_for('plan', ['claude', 'codex']), ())
        routes.set_kind('find', ['codex:gpt-6-luna:medium', 'claude:haiku:medium'])  # now the person's own
        table = routes.load()
        self.assertEqual(table.from_preset, frozenset())
        self.assertEqual(table.route_for('find', ['claude', 'codex'])[0], C('codex', 'gpt-6-luna', 'medium'))

    def test_a_row_the_person_set_since_stays(self):
        routes.set_kind('review', ['codex'])
        routes.preset('by-the-job')
        routes.set_kind('find', ['codex:gpt-6-luna'])
        routes.preset(None)
        self.assertEqual(dict(routes.load().routes), {'review': (C('codex'),), 'find': (C('codex', 'gpt-6-luna'),)})

    def test_a_row_the_person_reset_since_stays_reset(self):
        routes.preset('by-the-job')
        routes.reset('review')
        routes.preset(None)
        self.assertEqual(dict(routes.load().routes), {})

    def test_reset_all_turns_it_off(self):
        routes.preset('by-the-job')
        routes.reset()
        self.assertEqual(routes.load().preset, None)

    def test_off_when_none_is_on_changes_nothing(self):
        routes.preset(None)
        self.assertFalse(os.path.exists(self.path))

    def test_an_unknown_preset(self):
        self.refused(lambda: routes.preset('fastest'), words.UNKNOWN_PRESET.format(word='fastest',
                                                                                   names='by-the-job'))

    def test_a_row_without_a_source_or_a_day_is_refused(self):
        for row in ({'choices': ['claude:haiku'], 'read': '2026-11-24', 'rests_on': 'made up'},
                    {'choices': ['claude:haiku'], 'source': 'made up', 'rests_on': 'made up'},
                    {'choices': ['claude:haiku'], 'source': ' ', 'read': '2026-11-24', 'rests_on': 'made up'},
                    {'choices': ['claude:haiku'], 'source': 'made up', 'read': '2026-11-24'},
                    {'choices': ['claude:haiku'], 'source': 'made up', 'read': '2026-11-24', 'rests_on': ' '}):
            with self.subTest(row=row):
                doc = {'v': 1, 'presets': {'made-up': {'rows': {'find': row}}}}
                self.refused(lambda: routes.check_presets(doc),
                             words.PRESET_NO_SOURCE.format(name='made-up', kind='find'))

    def test_a_row_naming_a_model_the_list_does_not_know(self):
        doc = {'v': 1, 'presets': {'made-up': {'rows': {'find': {'choices': ['claude:sonata'],
                                                                 'source': 'made up', 'read': '2026-11-24',
                                                                 'rests_on': 'made up'}}}}}
        with self.assertRaises(routes.RouteError) as got:
            routes.check_presets(doc)
        self.assertTrue(str(got.exception).startswith(words.PRESET_ROW.format(name='made-up', kind='find', why='')))
        self.assertIn('"sonata"', str(got.exception))

    def test_a_row_for_a_kind_there_is_not(self):
        doc = {'v': 1, 'presets': {'made-up': {'rows': {'deploy': {'choices': ['claude'], 'source': 'made up',
                                                                   'read': '2026-11-24', 'rests_on': 'made up'}}}}}
        with self.assertRaises(routes.RouteError) as got:
            routes.check_presets(doc)
        self.assertIn('"deploy"', str(got.exception))


CODE = r'''
import sys
from switchboard.providers import routes
mine, other = sys.argv[1], sys.argv[2]
for i in range(%d):
    routes.set_kind(mine, ['codex' if i %% 2 else 'claude:sonnet'])
    routes.reset(mine)
    routes.set_kind(mine, ['claude:haiku:medium'])
    got = routes.load()
    assert got.get(mine) == routes.load().get(mine) and got.get(mine), (mine, i)
'''


class TwoWriters(Clean):
    def test_two_writers_at_once_lose_nothing(self):
        root = os.path.realpath(tempfile.mkdtemp(prefix='sb-routes-two-'))
        env = {'SWITCHBOARD_HOME': os.path.realpath(core.data_dir()), 'HOME': os.path.join(root, 'home'),
               'PYTHONDONTWRITEBYTECODE': '1', 'PATH': '/usr/bin:/bin',
               'PYTHONPATH': os.pathsep.join(p for p in sys.path if p)}
        results = {}

        def run(mine, other):
            results[mine] = subprocess.run([sys.executable, '-B', '-c', CODE % 40, mine, other], env=env,
                                           capture_output=True, text=True, timeout=120)
        threads = [threading.Thread(target=run, args=a) for a in (('build', 'plan'), ('plan', 'build'))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(150)
        for mine in ('build', 'plan'):
            self.assertEqual(results[mine].returncode, 0, results[mine].stderr[-800:])
        self.assertEqual(dict(routes.load().routes), {'build': (C('claude', 'haiku', 'medium'),),
                                                      'plan': (C('claude', 'haiku', 'medium'),)})


if __name__ == '__main__':
    unittest.main()
