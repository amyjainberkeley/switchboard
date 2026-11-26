import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The person's choices (switchboard/providers/choices.py, DESIGN 13): the order, added providers, the yes per folder
# and provider, `stay`, the one-time yeses to asking Codex and to `hand`, how each app opens and the note's size.
# Folders are made-up ones under a temp folder; the settings file is this run's own.
import json
import os
import stat
import sys
import tempfile
import unicodedata
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core
from switchboard.providers import choices, contract

NOW = 1795600000.0


class Base(unittest.TestCase):
    def setUp(self):
        self.path = core.data_path(contract.SETTINGS_FILE)
        if os.path.exists(self.path):
            os.remove(self.path)
        self.root = os.path.realpath(tempfile.mkdtemp(prefix='sb-choices-'))
        self.folder = os.path.join(self.root, 'site')
        os.makedirs(self.folder)

    def saved(self):
        with open(self.path) as f:
            return json.load(f)


class Defaults(Base):
    def test_before_anything_is_chosen(self):
        self.assertEqual(choices.order(), ['claude'])
        self.assertEqual(choices.added(), {'claude': 0})
        self.assertIsNone(choices.consent(self.folder, 'codex'))
        self.assertIsNone(choices.stays(self.folder))
        self.assertFalse(choices.ask_ok('codex'))
        self.assertFalse(choices.hand_ack('codex'))
        self.assertEqual(choices.open_with('codex'), contract.IN_TERMINAL)
        self.assertEqual(choices.handoff_budget(), 96000)
        self.assertFalse(os.path.exists(self.path))        # reading writes nothing

    def test_a_broken_file_reads_as_the_defaults(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w') as f:
            f.write('{not json')
        self.assertEqual(choices.order(), ['claude'])
        with open(self.path, 'w') as f:
            json.dump({'v': 1, 'order': 'codex', 'added': [1], 'folders': [], 'open_with': 3,
                       'handoff_budget': 'big', 'ask_ok': True, 'hand_ack': 'yes'}, f)
        self.assertEqual(choices.order(), ['claude'])
        self.assertEqual(choices.added(), {'claude': 0})
        self.assertIsNone(choices.consent(self.folder, 'codex'))
        self.assertEqual(choices.open_with('codex'), contract.IN_TERMINAL)
        self.assertEqual(choices.handoff_budget(), 96000)


class Order(Base):
    def test_add_puts_a_provider_after_claude(self):
        choices.add('codex', NOW)
        self.assertEqual(choices.added(), {'claude': 0, 'codex': NOW})
        self.assertEqual(choices.order(), ['claude', 'codex'])
        choices.add('codex', NOW + 50)                     # adding again keeps the first time
        self.assertEqual(choices.added()['codex'], NOW)

    def test_an_order_set_is_kept_and_claude_is_always_in_it(self):
        choices.add('codex', NOW)
        choices.set_order(['codex', 'claude'])
        self.assertEqual(choices.order(), ['codex', 'claude'])
        choices.set_order(['codex'])
        self.assertEqual(choices.order(), ['codex', 'claude'])
        choices.set_order(['claude'])
        self.assertEqual(choices.order(), ['claude', 'codex'])

    def test_unknown_repeated_or_not_added_ids_are_refused(self):
        for bad in (['nope'], ['claude', 'claude'], ['codex'], ['Claude']):
            with self.assertRaises(ValueError):
                choices.set_order(bad)
        with self.assertRaises(ValueError):
            choices.add('nope', NOW)
        self.assertFalse(os.path.exists(self.path))

    def test_the_file_is_whole_owner_only_and_in_the_providers_folder(self):
        choices.add('codex', NOW)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(self.path, os.path.join(core.data_dir(), 'providers', 'settings.json'))
        self.assertEqual(self.saved()['v'], 1)


class Consent(Base):
    def test_yes_never_and_cleared(self):
        choices.set_consent(self.folder, 'codex', 'yes', NOW)
        self.assertEqual(choices.consent(self.folder, 'codex'), 'yes')
        self.assertIsNone(choices.consent(self.folder, 'claude'))
        self.assertIsNone(choices.consent(self.root, 'codex'))      # one folder, not its parent
        choices.set_consent(self.folder, 'codex', 'never', NOW)
        self.assertEqual(choices.consent(self.folder, 'codex'), 'never')
        choices.set_consent(self.folder, 'codex', None, NOW)
        self.assertIsNone(choices.consent(self.folder, 'codex'))
        with self.assertRaises(ValueError):
            choices.set_consent(self.folder, 'codex', 'maybe', NOW)
        with self.assertRaises(ValueError):
            choices.set_consent(self.folder, 'nope', 'yes', NOW)

    def test_allow_lifts_a_typed_never(self):
        choices.set_consent(self.folder, 'codex', 'never', NOW)
        choices.allow(self.folder, 'codex', NOW + 1)
        self.assertEqual(choices.consent(self.folder, 'codex'), 'yes')

    def test_folder_keys_are_real_paths_in_nfc(self):
        cafe = os.path.join(self.root, unicodedata.normalize('NFC', 'Café'))
        os.makedirs(cafe)
        link = os.path.join(self.root, 'link-to-cafe')
        os.symlink(cafe, link)
        choices.set_consent(link, 'codex', 'yes', NOW)
        self.assertIn(unicodedata.normalize('NFC', os.path.realpath(cafe)), self.saved()['folders'])
        nfd = os.path.join(self.root, unicodedata.normalize('NFD', 'Café'))
        self.assertEqual(choices.consent(nfd, 'codex'), 'yes')
        self.assertEqual(choices.consent(cafe, 'codex'), 'yes')
        entry = self.saved()['folders'][unicodedata.normalize('NFC', os.path.realpath(cafe))]
        st = os.stat(cafe)
        self.assertEqual(entry['id'], [st.st_dev, st.st_ino])

    def test_another_spelling_of_the_same_folder_is_found_by_its_identity(self):
        folder = os.path.join(self.root, 'MySite')
        os.makedirs(folder)
        other = os.path.join(self.root, 'mysite')
        if not (os.path.isdir(other) and os.path.samefile(folder, other)):
            self.skipTest('this disk tells case apart')
        choices.set_consent(folder, 'codex', 'yes', NOW)
        self.assertEqual(choices.consent(other, 'codex'), 'yes')
        choices.set_consent(other, 'codex', 'never', NOW)
        self.assertEqual(choices.consent(folder, 'codex'), 'never')
        self.assertEqual(len(self.saved()['folders']), 1)

    def test_a_folder_that_took_a_gone_folders_place_is_not_its_entry(self):
        old = os.path.join(self.root, 'old')
        os.makedirs(old)
        choices.set_consent(old, 'codex', 'yes', NOW)
        os.rmdir(old)
        new = os.path.join(self.root, 'new')
        os.makedirs(new)
        data = self.saved()
        st = os.stat(new)
        data['folders'][os.path.realpath(old)]['id'] = [st.st_dev, st.st_ino]   # as if the number came back
        core.write_json(self.path, data)
        self.assertIsNone(choices.consent(new, 'codex'))


class Stay(Base):
    def setUp(self):
        super().setUp()
        choices.add('codex', NOW)

    def test_stay_marks_never_for_the_others_and_off_clears_exactly_those(self):
        choices.set_stay(self.folder, 'claude', NOW)
        self.assertEqual(choices.stays(self.folder), 'claude')
        self.assertEqual(choices.consent(self.folder, 'codex'), 'never')
        self.assertIsNone(choices.consent(self.folder, 'claude'))
        choices.set_stay(self.folder, None, NOW + 1)
        self.assertIsNone(choices.stays(self.folder))
        self.assertIsNone(choices.consent(self.folder, 'codex'))

    def test_a_typed_never_outlives_stay_off(self):
        choices.set_consent(self.folder, 'codex', 'never', NOW)
        choices.set_stay(self.folder, 'claude', NOW + 1)
        choices.set_stay(self.folder, None, NOW + 2)
        self.assertEqual(choices.consent(self.folder, 'codex'), 'never')

    def test_a_yes_before_the_stay_is_asked_again_after_it(self):
        choices.set_consent(self.folder, 'codex', 'yes', NOW)
        choices.set_stay(self.folder, 'claude', NOW + 1)
        self.assertEqual(choices.consent(self.folder, 'codex'), 'never')
        choices.set_stay(self.folder, None, NOW + 2)
        self.assertIsNone(choices.consent(self.folder, 'codex'))

    def test_allow_during_a_stay_lifts_that_never_and_not_the_stay(self):
        choices.set_stay(self.folder, 'claude', NOW)
        choices.allow(self.folder, 'codex', NOW + 1)
        self.assertEqual(choices.consent(self.folder, 'codex'), 'yes')
        self.assertEqual(choices.stays(self.folder), 'claude')
        choices.set_stay(self.folder, None, NOW + 2)
        self.assertEqual(choices.consent(self.folder, 'codex'), 'yes')

    def test_a_new_stay_takes_back_the_marks_of_the_old(self):
        choices.set_stay(self.folder, 'claude', NOW)
        choices.set_stay(self.folder, 'codex', NOW + 1)
        self.assertEqual(choices.stays(self.folder), 'codex')
        self.assertEqual(choices.consent(self.folder, 'claude'), 'never')
        self.assertIsNone(choices.consent(self.folder, 'codex'))
        choices.set_stay(self.folder, None, NOW + 2)
        self.assertIsNone(choices.consent(self.folder, 'claude'))

    def test_a_stay_is_for_its_folder_only(self):
        other = os.path.join(self.root, 'other')
        os.makedirs(other)
        choices.set_stay(self.folder, 'claude', NOW)
        self.assertIsNone(choices.stays(other))
        self.assertIsNone(choices.consent(other, 'codex'))

    def test_an_unknown_provider_cannot_be_stayed_on(self):
        with self.assertRaises(ValueError):
            choices.set_stay(self.folder, 'nope', NOW)


class OnceAndHow(Base):
    def test_ask_ok_and_hand_ack_are_kept(self):
        choices.set_ask_ok(NOW, 'codex')
        choices.set_hand_ack(NOW, 'codex')
        self.assertTrue(choices.ask_ok('codex'))
        self.assertTrue(choices.hand_ack('codex'))
        self.assertEqual(self.saved()['ask_ok'], {'codex': NOW})
        self.assertEqual(self.saved()['hand_ack'], {'codex': NOW})
        self.assertFalse(choices.ask_ok('claude'))           # each yes is for the provider it was given for
        self.assertFalse(choices.hand_ack('claude'))

    def test_an_answer_kept_as_one_number_is_no_providers_and_is_asked_again(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w') as f:
            json.dump({'v': 1, 'hand_ack': NOW}, f)          # as an earlier build kept it, for every provider at once
        self.assertFalse(choices.hand_ack('codex'))
        choices.set_hand_ack(NOW + 1, 'codex')
        self.assertEqual(self.saved()['hand_ack'], {'codex': NOW + 1})
        with self.assertRaises(ValueError):                  # only an id the registry knows is kept
            choices.set_hand_ack(NOW, 'nobody')

    def test_the_first_provider_is_the_registrys_first(self):
        from switchboard import providers
        self.assertEqual(choices.first(), providers.ids()[0])
        self.assertEqual(choices.order()[0], providers.ids()[0])

    def test_open_with(self):
        choices.set_open_with('codex', contract.IN_APP)
        self.assertEqual(choices.open_with('codex'), contract.IN_APP)
        self.assertEqual(choices.open_with('claude'), contract.IN_TERMINAL)
        with self.assertRaises(ValueError):
            choices.set_open_with('codex', 'print')

    def test_the_note_budget_is_kept_within_its_bounds(self):
        for given, want in ((10, 32000), (10 ** 9, 400000), (48000, 48000), (True, 96000), (12.5, 96000)):
            core.write_json(self.path, {'v': 1, 'handoff_budget': given})
            self.assertEqual(choices.handoff_budget(), want, given)



class AnUnreadableFile(Base):
    """A settings file that is there and cannot be read is never written over: a `never` or a `stay` in it would be
    lost. Reading still gives the defaults; a missing file is still empty."""
    BROKEN = '{"v": 1, "folders": {"/Users/maya/site": {"consent": {"codex": {"state": "never"'

    def write(self, text):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w') as f:
            f.write(text)

    def test_every_change_refuses_and_writes_nothing(self):
        for text in (self.BROKEN, '[1, 2]'):
            self.write(text)
            changes = (lambda: choices.add('codex', NOW), lambda: choices.set_order(['claude']),
                       lambda: choices.set_consent(self.folder, 'codex', 'yes', NOW),
                       lambda: choices.allow(self.folder, 'codex', NOW),
                       lambda: choices.set_stay(self.folder, 'claude', NOW), lambda: choices.set_ask_ok(NOW, 'codex'),
                       lambda: choices.set_hand_ack(NOW, 'codex'), lambda: choices.set_open_with('codex', 'app'))
            for change in changes:
                with self.assertRaises(contract.ProviderError) as cm:
                    change()
                self.assertEqual(cm.exception.kind, 'unreadable')
                with open(self.path) as f:
                    self.assertEqual(f.read(), text)
            self.assertEqual(choices.order(), ['claude'])
            self.assertIsNone(choices.consent(self.folder, 'codex'))

    def test_a_missing_file_is_still_empty(self):
        choices.set_consent(self.folder, 'codex', 'yes', NOW)
        self.assertEqual(choices.consent(self.folder, 'codex'), 'yes')


class ConsentAt(Base):
    def test_the_time_of_a_yes_and_none_otherwise(self):
        self.assertIsNone(choices.consent_at(self.folder, 'codex'))                 # never asked
        choices.set_consent(self.folder, 'codex', 'yes', NOW)
        self.assertEqual(choices.consent_at(self.folder, 'codex'), NOW)
        choices.allow(self.folder, 'codex', NOW + 60)
        self.assertEqual(choices.consent_at(self.folder, 'codex'), NOW + 60)
        choices.set_consent(self.folder, 'codex', 'never', NOW + 120)
        self.assertIsNone(choices.consent_at(self.folder, 'codex'))
        self.assertIsNone(choices.consent_at(self.folder, 'no-such-provider'))

    def test_a_yes_written_before_times_were_kept(self):
        data = {'v': 1, 'folders': {os.path.realpath(self.folder): {'consent': {'codex': {'state': 'yes'}}}}}
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w') as f:
            json.dump(data, f)
        self.assertEqual(choices.consent(self.folder, 'codex'), 'yes')
        self.assertIsNone(choices.consent_at(self.folder, 'codex'))
        data['folders'][os.path.realpath(self.folder)]['consent']['codex']['at'] = True
        with open(self.path, 'w') as f:
            json.dump(data, f)
        self.assertIsNone(choices.consent_at(self.folder, 'codex'))


if __name__ == '__main__':
    unittest.main()
