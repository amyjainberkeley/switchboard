import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""What each provider's app can be asked for (switchboard/providers/catalog.py and catalog.json).

- The shipped file passes its own check: every model, every list of efforts and every way of starting an app says
  where it was read and on which day, and names only providers this build knows.
- A broken entry is refused with one plain sentence: no source, no day read, a day of another shape, a model word an
  app could take for an option, a start with no place for its value, a version that cannot be read, an unknown
  provider, two models of one name.
- The person's own file (in the data folder) is checked the same way and wins: a model of the same id is replaced,
  a new one is added, its efforts replace the shipped ones; it can never change how an app is started.
- start_args gives the extra words for a version that takes them, and none for one before (or a version not known):
  never a guess. An unknown model or effort gives a sentence that lists the known ones.
"""
import copy
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

from switchboard import core  # noqa: E402
from switchboard.providers import catalog, ids, words  # noqa: E402

DAY = re.compile(r'^\d{4}-\d{2}-\d{2}\Z')


def shipped():
    with open(catalog.SHIPPED, encoding='utf-8') as f:
        return json.load(f)


class Clean(unittest.TestCase):
    def setUp(self):
        self.own = core.data_path(catalog.OWN_FILE)
        self.remove()
        self.addCleanup(self.remove)

    def remove(self):
        try:
            os.remove(self.own)
        except FileNotFoundError:
            pass

    def write_own(self, doc):
        os.makedirs(os.path.dirname(self.own), mode=0o700, exist_ok=True)
        with open(self.own, 'w', encoding='utf-8') as f:
            f.write(doc if isinstance(doc, str) else json.dumps(doc))


class TheShippedFile(Clean):
    def test_it_passes_its_own_check(self):
        got = catalog.check(shipped(), catalog.SHIPPED_WHERE)
        self.assertEqual(sorted(got), sorted(ids()))
        self.assertEqual(catalog.providers(), tuple(sorted(ids())))

    def test_every_entry_says_where_and_when_it_was_read(self):
        doc = shipped()
        for pid, entry in doc['providers'].items():
            items = [('model ' + m['id'], m) for m in entry['models']]
            items += [('efforts', entry['efforts'])] if entry.get('efforts') else []
            items += [('start ' + k, v) for k, v in entry['start'].items() if v]
            for what, item in items:
                with self.subTest(provider=pid, what=what):
                    self.assertTrue(item['source'].strip())
                    self.assertRegex(item['read'], DAY)

    def test_the_models_the_preset_needs_are_there(self):
        for pid, word in (('claude', 'haiku'), ('claude', 'sonnet')):
            self.assertEqual(catalog.find_model(pid, word).id, word)

    def test_the_models_the_checked_lists_confirm(self):
        # OpenAI's own list as Codex keeps it, and Anthropic's model-config aliases, as two readers checked them
        self.assertEqual([m.id for m in catalog.models('codex', own=False)],
                         ['gpt-6.2-astra', 'gpt-6.2-sol', 'gpt-6.1-terra', 'gpt-6.1-sol', 'gpt-6-astra', 'gpt-6-sol',
                          'gpt-6-luna', 'gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna'])
        self.assertEqual([m.id for m in catalog.models('claude', own=False)], ['haiku', 'sonnet', 'opus', 'fable'])
        luna = catalog.find_model('codex', 'gpt-6-luna')
        self.assertEqual((luna.name, luna.says, luna.read), ('GPT-6 Luna', 'Fast and affordable model for easier tasks.',
                                                            '2026-11-25'))
        self.assertEqual(catalog.find_model('codex', 'GPT-6.2 Sol').says, 'Fast, go-to model for coding and everyday work.')
        for pid in ('claude', 'codex'):
            for m in catalog.models(pid, own=False):
                with self.subTest(model=m.id):
                    self.assertEqual(m.read, '2026-11-25')
        effort = catalog.start_of('codex', 'effort')
        self.assertIn('https://learn.chatgpt.com/docs/config-file/config-reference', effort.source)
        self.assertIn('codex --help', effort.source)
        # an option is passed only from the version it was read in (kept as it was)
        self.assertEqual([catalog.start_of(p, g).since for p in ('claude', 'codex') for g in ('model', 'effort')],
                         ['2.1.309', '2.1.309', '0.155.1', '0.169.1'])

    def test_each_start_has_one_place_for_its_value(self):
        for pid in catalog.providers():
            for what in ('model', 'effort'):
                start = catalog.start_of(pid, what)
                if start is None:
                    continue
                with self.subTest(provider=pid, what=what):
                    self.assertEqual(sum('{%s}' % what in a for a in start.args), 1)


class ABrokenEntryIsRefused(Clean):
    def refused(self, change, says):
        doc = copy.deepcopy(shipped())
        change(doc)
        with self.assertRaises(catalog.CatalogError) as got:
            catalog.check(doc, 'a made-up file')
        text = str(got.exception)
        self.assertIn('a made-up file', text)
        self.assertIn(says, text)
        self.assertTrue(text.endswith('.'), text)
        self.assertNotIn('\n', text)

    def test_no_source(self):
        self.refused(lambda d: d['providers']['codex']['models'][0].pop('source'), 'no source')

    def test_no_day_read(self):
        self.refused(lambda d: d['providers']['claude']['models'][1].pop('read'), 'no source')

    def test_an_empty_source(self):
        self.refused(lambda d: d['providers']['claude']['models'][1].update(source='  '), 'no source')

    def test_a_day_of_another_shape(self):
        self.refused(lambda d: d['providers']['claude']['efforts'].update(read='Nov 24'), 'not in the shape')

    def test_a_start_without_a_source(self):
        self.refused(lambda d: d['providers']['codex']['start']['model'].pop('source'), 'no source')

    def test_a_model_word_that_looks_like_an_option(self):
        self.refused(lambda d: d['providers']['codex']['models'][0].update(id='--sandbox'), 'not in the shape')

    def test_a_start_with_no_place_for_its_value(self):
        self.refused(lambda d: d['providers']['claude']['start']['model'].update(args=['--model']),
                     'not in the shape')

    def test_a_version_that_cannot_be_read(self):
        self.refused(lambda d: d['providers']['claude']['start']['effort'].update(since='soon'), 'not in the shape')

    def test_an_unknown_provider(self):
        self.refused(lambda d: d['providers'].update(zeta=d['providers']['codex']), '"zeta"')

    def test_two_models_of_one_name(self):
        def change(d):
            d['providers']['claude']['models'][1]['name'] = d['providers']['claude']['models'][0]['name'].upper()
        self.refused(change, 'twice')

    def test_not_an_object(self):
        with self.assertRaises(catalog.CatalogError):
            catalog.check(['not', 'an', 'object'], 'a made-up file')


class ThePersonsFile(Clean):
    def test_it_wins_and_adds(self):
        self.write_own({'v': 1, 'providers': {'claude': {
            'models': [{'id': 'sonnet', 'name': 'Sonnet', 'says': 'A made-up line of my own.', 'source': 'my notes',
                        'read': '2026-11-20'},
                       {'id': 'claude-sonnet-5-5', 'name': 'Sonnet pinned', 'says': None, 'source': 'my notes',
                        'read': '2026-11-20'}],
            'efforts': {'values': ['low', 'high'], 'source': 'my notes', 'read': '2026-11-20'}}}})
        self.assertEqual(catalog.find_model('claude', 'SONNET').says, 'A made-up line of my own.')
        self.assertEqual(catalog.find_model('claude', 'sonnet pinned').id, 'claude-sonnet-5-5')
        self.assertEqual(catalog.find_model('claude', 'haiku').id, 'haiku')          # the shipped ones stay
        self.assertEqual(catalog.efforts('claude'), ('low', 'high'))
        self.assertEqual(catalog.efforts('codex'), catalog.efforts('codex', own=False))
        ids_ = [m.id for m in catalog.models('claude')]
        self.assertEqual(ids_.count('sonnet'), 1)

    def test_it_is_checked_the_same_way(self):
        self.write_own({'v': 1, 'providers': {'codex': {'models': [{'id': 'gpt-x', 'name': 'X', 'says': None}]}}})
        with self.assertRaises(catalog.CatalogError) as got:
            catalog.models('codex')
        self.assertIn(self.own, str(got.exception))
        self.assertIn('no source', str(got.exception))

    def test_it_never_changes_how_an_app_is_started(self):
        self.write_own({'v': 1, 'providers': {'codex': {'start': {'model': {
            'args': ['--dangerously-bypass-approvals-and-sandbox', '{model}'], 'since': '0.1.0', 'source': 'mine',
            'read': '2026-11-20'}}}}})
        with self.assertRaises(catalog.CatalogError) as got:
            catalog.start_args('codex', 'gpt-6.2-sol', None, '0.169.1')
        self.assertEqual(str(got.exception), words.CATALOG_OWN_START.format(where=self.own, name='Codex'))

    def test_a_file_that_is_not_json(self):
        self.write_own('{not json')
        with self.assertRaises(catalog.CatalogError) as got:
            catalog.models('claude')
        self.assertEqual(str(got.exception), words.CATALOG_NOT_JSON.format(where=self.own))

    def test_no_file_is_the_shipped_list(self):
        self.assertEqual(catalog.models('codex'), catalog.models('codex', own=False))


class StartArgs(Clean):
    def test_a_version_before_and_after_an_option_existed(self):
        self.assertEqual(catalog.start_args('claude', 'sonnet', 'high', '2.1.300'), [])
        self.assertEqual(catalog.start_args('claude', 'sonnet', 'high', '2.1.309'),
                         ['--model', 'sonnet', '--effort', 'high'])
        self.assertEqual(catalog.start_args('claude', 'Haiku', None, '2.1.330'), ['--model', 'haiku'])
        self.assertEqual(catalog.start_args('codex', 'gpt-6.2-sol', 'high', '0.150.0'), [])
        self.assertEqual(catalog.start_args('codex', 'gpt-6.2-sol', 'high', '0.155.1'), ['--model', 'gpt-6.2-sol'])
        self.assertEqual(catalog.start_args('codex', 'gpt-6.2-sol', 'high', '0.169.1'),
                         ['--model', 'gpt-6.2-sol', '-c', 'model_reasoning_effort="high"'])
        self.assertEqual(catalog.start_args('codex', None, 'xhigh', '0.172.0'), ['-c', 'model_reasoning_effort="xhigh"'])
        self.assertEqual(catalog.start_args('codex', 'gpt-6-luna', None, '0.169.0-alpha.11.1'),
                         ['--model', 'gpt-6-luna'])

    def test_a_version_not_known_takes_nothing(self):
        for version in (None, '', 'not a version'):
            with self.subTest(version=version):
                self.assertEqual(catalog.start_args('claude', 'sonnet', 'high', version), [])
                self.assertFalse(catalog.takes('claude', 'model', version))

    def test_takes(self):
        self.assertTrue(catalog.takes('codex', 'model', '0.169.1'))
        self.assertFalse(catalog.takes('codex', 'effort', '0.155.1'))
        self.assertTrue(catalog.takes('codex', 'effort', '0.169.1'))
        self.assertFalse(catalog.takes('codex', 'speed', '0.169.1'))

    def test_nothing_asked_is_nothing_passed(self):
        self.assertEqual(catalog.start_args('claude', None, None, '2.1.330'), [])


class Unknown(Clean):
    def test_an_unknown_model_lists_the_known_ones(self):
        with self.assertRaises(catalog.CatalogError) as got:
            catalog.find_model('codex', 'gpt-9')
        known = ', '.join(m.id for m in catalog.models('codex'))
        self.assertEqual(str(got.exception), words.UNKNOWN_MODEL.format(name='Codex', word='gpt-9', known=known))
        for m in catalog.models('codex'):
            self.assertIn(m.id, str(got.exception))

    def test_by_id_or_by_name_case_ignored(self):
        self.assertEqual(catalog.find_model('codex', 'GPT-6.2 Astra').id, 'gpt-6.2-astra')
        self.assertEqual(catalog.find_model('codex', 'gpt-6.2-ASTRA').id, 'gpt-6.2-astra')
        self.assertEqual(catalog.find_model('claude', ' Opus ').id, 'opus')

    def test_an_unknown_effort_lists_the_known_ones(self):
        with self.assertRaises(catalog.CatalogError) as got:
            catalog.find_effort('claude', 'ultra')
        self.assertEqual(str(got.exception), words.UNKNOWN_EFFORT.format(
            name='Claude', word='ultra', known=', '.join(catalog.efforts('claude'))))
        self.assertEqual(catalog.find_effort('codex', 'ULTRA'), 'ultra')

    def test_an_unknown_provider(self):
        with self.assertRaises(catalog.CatalogError) as got:
            catalog.models('zeta')
        self.assertIn('zeta', str(got.exception))


if __name__ == '__main__':
    unittest.main()
