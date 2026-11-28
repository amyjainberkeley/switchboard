import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The person's routing switches (switchboard/router/settings.py): all off until turned on, a file that cannot be read
# is all off and said to History once, never guessed at, and a change is written whole or not at all.
import json
import os
import shutil
import stat
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

from switchboard import core, history  # noqa: E402
from switchboard.router import settings, words  # noqa: E402

UNSET = {'workflow': {'model': None, 'effort': None}, 'helper': {'model': None, 'effort': None},
         'chat': {'model': None, 'effort': None}}
OFF = {'on': False, 'dry': False, 'helpers': {'force': False}, 'chat': {'effort': False, 'model_on_first_turn': False},
       'hand': {'on': False, 'write': False}, 'carry_on': False, 'defaults': UNSET}


class Base(unittest.TestCase):
    def setUp(self):
        for p in (core.data_path('router'), history.path()):
            if os.path.isdir(p):
                shutil.rmtree(p)
            elif os.path.exists(p):
                os.remove(p)

    def write(self, text):
        os.makedirs(core.data_path('router'), exist_ok=True)
        with open(settings.path(), 'w', encoding='utf-8') as f:
            f.write(text)

    def said(self):
        return [r for r in history.read() if r['text'] == words.SETTINGS_UNREADABLE]


class AllOffUntilTurnedOn(Base):
    def test_no_file_is_all_off_and_writes_nothing(self):
        self.assertEqual(settings.read(), OFF)
        self.assertFalse(os.path.exists(core.data_path('router')))

    def test_each_switch_turns_on_alone(self):
        for name in settings.NAMES:
            with self.subTest(name=name):
                settings.reset()
                got = settings.set_switch(name, True)
                self.assertIs(settings.get(got, name), True)
                self.assertEqual(settings.read(), got)
                # Routing on also brings the two switches that follow it unless the person turned them off.
                follow = settings.FOLLOW_ON if name == 'on' else ()
                others = [n for n in settings.NAMES if n != name and n not in follow]
                self.assertTrue(all(settings.get(got, n) is False for n in others))
                self.assertTrue(all(settings.get(got, n) is True for n in follow))

    def test_the_file_is_private_and_whole(self):
        settings.set_switch('hand.on', True)
        settings.set_switch('dry', True)
        self.assertEqual(stat.S_IMODE(os.stat(settings.path()).st_mode), 0o600)
        with open(settings.path(), encoding='utf-8') as f:
            doc = json.load(f)
        self.assertEqual(doc['v'], 1)
        self.assertEqual((doc['hand'], doc['dry'], doc['on']), ({'on': True, 'write': False}, True, False))
        settings.set_switch('dry', False)
        self.assertFalse(settings.read()['dry'])

    def test_a_missing_key_is_off(self):
        self.write('{"v": 1, "hand": {"on": true}}')
        got = settings.read()
        self.assertEqual(got['hand'], {'on': True, 'write': False})
        self.assertFalse(got['on'])
        self.assertEqual(self.said(), [])

    def test_unknown_name_and_value(self):
        with self.assertRaises(settings.SettingsError) as e:
            settings.set_switch('hand.everything', True)
        self.assertIn('hand.write', str(e.exception))
        with self.assertRaises(TypeError):
            settings.set_switch('on', 1)
        self.assertFalse(os.path.exists(settings.path()))


class Defaults(Base):
    """What work gets when its kind has no route, by where it was started: unset until the person sets it, each a
    word the list of models knows for the agent named."""

    def test_six_names_all_unset_until_set(self):
        self.assertEqual(settings.WORDS, ('defaults.workflow.model', 'defaults.workflow.effort',
                                          'defaults.helper.model', 'defaults.helper.effort',
                                          'defaults.chat.model', 'defaults.chat.effort'))
        self.assertEqual(settings.read()['defaults'], UNSET)
        self.assertEqual(settings.defaults()['defaults'], UNSET)

    def test_each_default_is_set_alone_as_the_list_spells_it(self):
        for name, given, kept in (('defaults.workflow.effort', 'XHIGH', 'xhigh'), ('defaults.helper.model', 'Haiku',
                                  'haiku'), ('defaults.chat.model', 'opus', 'opus'), ('defaults.chat.effort', ' max ',
                                  'max'), ('defaults.workflow.model', 'sonnet', 'sonnet'),
                                  ('defaults.helper.effort', 'low', 'low')):
            with self.subTest(name=name):
                settings.reset()
                got = settings.set_default(name, given, 'claude')
                # A model is kept for the agent it was set for; an effort is one word.
                self.assertEqual(settings.get(got, name), {'claude': kept} if name.endswith('.model') else kept)
                self.assertEqual(settings.read(), got)
                self.assertTrue(all(settings.get(got, n) is None for n in settings.WORDS if n != name))
                self.assertTrue(all(settings.get(got, n) is False for n in settings.NAMES))

    def test_unset_again_with_none(self):
        settings.set_default('defaults.workflow.effort', 'xhigh', 'claude')
        self.assertIsNone(settings.get(settings.set_default('defaults.workflow.effort', None, 'claude'),
                                       'defaults.workflow.effort'))
        self.assertEqual(settings.read()['defaults'], UNSET)

    def test_a_word_the_list_does_not_know_changes_nothing(self):
        settings.set_default('defaults.chat.effort', 'high', 'claude')
        for name, word in (('defaults.chat.effort', 'ultra'), ('defaults.chat.model', 'gpt-6-luna'),
                           ('defaults.chat.model', 'me@x.example')):
            with self.subTest(word=word):
                with self.assertRaises(settings.SettingsError) as e:
                    settings.set_default(name, word, 'claude')
                self.assertIn('Claude', str(e.exception))
        self.assertEqual(settings.read()['defaults']['chat'], {'model': None, 'effort': 'high'})

    def test_the_agent_named_decides_which_words_are_known(self):
        got = settings.set_default('defaults.helper.model', 'gpt-6-luna', 'codex')
        self.assertEqual(got['defaults']['helper']['model'], {'codex': 'gpt-6-luna'})
        with self.assertRaises(settings.SettingsError):
            settings.set_default('defaults.helper.model', 'haiku', 'nobody')

    def test_a_default_model_is_kept_for_the_agent_it_was_set_for(self):
        settings.set_default('defaults.helper.model', 'gpt-6-luna', 'codex')
        got = settings.set_default('defaults.helper.model', 'haiku', 'claude')
        self.assertEqual(got['defaults']['helper']['model'], {'codex': 'gpt-6-luna', 'claude': 'haiku'})
        self.assertEqual(settings.read(), got)
        got = settings.set_default('defaults.helper.model', None, 'codex')
        self.assertEqual(got['defaults']['helper']['model'], {'claude': 'haiku'})
        got = settings.set_default('defaults.helper.model', None, 'claude')
        self.assertIsNone(got['defaults']['helper']['model'])
        self.assertEqual(settings.read()['defaults'], UNSET)

    def test_a_default_model_that_names_no_agent_makes_the_file_unreadable(self):
        for text in ('{"v": 1, "defaults": {"helper": {"model": "haiku"}}}',
                     '{"v": 1, "defaults": {"helper": {"model": {"claude": 5}}}}',
                     '{"v": 1, "defaults": {"helper": {"model": {"a/b": "haiku"}}}}',
                     '{"v": 1, "defaults": {"helper": {"model": ["haiku"]}}}'):
            with self.subTest(text=text):
                self.write(text)
                self.assertEqual(settings.read(say=False), OFF)
        self.write('{"v": 1, "defaults": {"helper": {"model": {"claude": "haiku", "codex": null}}}}')
        self.assertEqual(settings.read(say=False)['defaults']['helper'], {'model': {'claude': 'haiku'},
                                                                          'effort': None})

    def test_an_unknown_name_or_a_value_that_is_not_text(self):
        with self.assertRaises(settings.SettingsError) as e:
            settings.set_default('defaults.teammate.model', 'haiku', 'claude')
        self.assertIn('defaults.chat.effort', str(e.exception))
        with self.assertRaises(settings.SettingsError):
            settings.set_default('on', 'haiku', 'claude')
        with self.assertRaises(settings.SettingsError):
            settings.set_switch('defaults.chat.model', True)
        with self.assertRaises(TypeError):
            settings.set_default('defaults.chat.model', 5, 'claude')
        self.assertFalse(os.path.exists(settings.path()))

    def test_switches_and_defaults_keep_each_other(self):
        settings.set_switch('on', True)
        settings.set_default('defaults.workflow.effort', 'xhigh', 'claude')
        settings.set_switch('dry', True)
        got = settings.read()
        self.assertEqual((got['on'], got['dry'], got['defaults']['workflow']['effort']), (True, True, 'xhigh'))
        with open(settings.path(), encoding='utf-8') as f:
            self.assertEqual(json.load(f)['defaults']['workflow'], {'model': None, 'effort': 'xhigh'})

    def test_a_default_that_is_not_a_word_makes_the_file_unreadable(self):
        for text in ('{"v": 1, "defaults": {"workflow": {"effort": 5}}}',
                     '{"v": 1, "defaults": {"workflow": {"model": "a/b"}}}',
                     '{"v": 1, "defaults": {"workflow": "xhigh"}}', '{"v": 1, "defaults": ["xhigh"]}'):
            with self.subTest(text=text):
                self.write(text)
                self.assertEqual(settings.read(say=False), OFF)

    def test_a_default_read_from_the_file(self):
        self.write('{"v": 1, "on": true, "defaults": {"workflow": {"effort": "xhigh"}, "chat": {"model": null}}}')
        got = settings.read()
        self.assertEqual(got['defaults']['workflow'], {'model': None, 'effort': 'xhigh'})
        self.assertEqual(got['defaults']['chat'], {'model': None, 'effort': None})
        self.assertTrue(got['on'])


class NeverAGuess(Base):
    BAD = ('not json', '[]', '{"v": 2, "on": true}', '{"on": "yes"}', '{"on": 1}', '{"hand": true}',
           '{"hand": {"on": "true"}}', '{"v": true, "on": true}', '{"on": NaN}', '"on"')

    def test_a_file_that_cannot_be_read_is_all_off(self):
        for text in self.BAD:
            with self.subTest(text=text):
                self.write(text)
                self.assertEqual(settings.read(say=False), OFF)

    def test_said_to_history_once(self):
        self.write('{"on": "yes"}')
        settings.read()
        settings.read()
        self.assertEqual(len(self.said()), 1)
        self.assertEqual(self.said()[0]['kind'], 'problem')
        self.write('{"on": "yes", "dry": "no"}')          # another unreadable file: said again
        settings.read()
        self.assertEqual(len(self.said()), 2)

    def test_said_again_after_it_was_readable(self):
        self.write('{"on": "yes"}')
        settings.read()
        self.write('{"on": true}')
        self.assertTrue(settings.read()['on'])
        self.write('{"on": "yes"}')
        os.utime(settings.path(), ns=(1, 1))               # the same bytes as the first time, a new mark
        settings.read()
        self.assertEqual(len(self.said()), 2)

    def test_read_without_say_writes_nothing(self):
        self.write('nope')
        settings.read(say=False)
        self.assertEqual(self.said(), [])
        self.assertFalse(os.path.exists(core.data_path(settings.SAID_FILE)))

    def test_a_change_never_writes_over_an_unreadable_file(self):
        self.write('{"on": "yes"}')
        with self.assertRaises(settings.SettingsError) as e:
            settings.set_switch('on', True)
        self.assertIn(settings.path(), str(e.exception))
        with open(settings.path(), encoding='utf-8') as f:
            self.assertEqual(f.read(), '{"on": "yes"}')

    def test_reset_clears_an_unreadable_file(self):
        self.write('nope')
        settings.reset()
        self.assertEqual(settings.read(), OFF)
        settings.set_switch('on', True)
        self.assertTrue(settings.read()['on'])

    def test_a_write_that_fails_leaves_the_old_file(self):
        settings.set_switch('on', True)
        with open(settings.path(), 'rb') as f:
            before = f.read()
        with mock.patch('os.replace', side_effect=OSError(28, 'No space left on device')):
            with self.assertRaises(OSError):
                settings.set_switch('dry', True)
        with open(settings.path(), 'rb') as f:
            self.assertEqual(f.read(), before)
        self.assertEqual([n for n in os.listdir(core.data_path('router')) if n.endswith('.tmp')], [])


if __name__ == '__main__':
    unittest.main()
