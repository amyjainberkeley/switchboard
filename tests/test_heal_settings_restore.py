import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# settings.json (auto-switch on or off, the switch lines, the order) and the page's state.json: a damaged file never
# reads as "nothing set". A file that is not there is a fresh start; one that can't be read comes back from the last
# good copy kept at every write, or starts from a new install's settings, and either way the damaged file is kept and
# it is said.
import errno
import glob
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from switchboard import core, history, repairs
from switchboard.accounts import autoloop


class Home(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='sbheal-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.tmp, 'data')
        os.makedirs(os.environ['SWITCHBOARD_HOME'])
        getattr(core, '_SETTINGS_FALLBACK', {}).update(sig=None, value=None)   # a fallback of a test before

    def tearDown(self):
        os.environ['SWITCHBOARD_HOME'] = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, name='settings.json'):
        return core.data_path(name)

    def write(self, name, text):
        with open(self.path(name), 'w') as f:
            f.write(text)

    def raw(self, name):
        with open(self.path(name), 'rb') as f:
            return f.read()

    def aside(self, name='settings.json'):
        return sorted(glob.glob(self.path(name) + '.damaged-*'))

    def history_lines(self):
        try:
            with open(history.path()) as f:
                return [json.loads(x) for x in f if x.strip()]
        except OSError:
            return []

    def set_up_settings(self):
        core.set_setting('auto', True)
        core.set_setting('auto_policy', {'session_at': 80.0, 'weekly_at': 90.0, 'strategy': 'most-room'})
        core.set_setting('auto_set_by_you', True)


class Settings(Home):
    def test_every_write_keeps_a_last_good_copy(self):
        self.set_up_settings()
        self.assertEqual(json.loads(self.raw('settings.json.last-good')), json.loads(self.raw('settings.json')))
        core.update_setting('port', lambda v: 7955)
        self.assertEqual(json.loads(self.raw('settings.json.last-good'))['port'], 7955)

    def test_a_damaged_file_comes_back_from_the_last_good_copy_and_auto_switch_stays_on(self):
        self.set_up_settings()
        self.write('settings.json', '{"auto": tr')
        got = autoloop.load_settings()
        self.assertTrue(got.enabled)                                    # never "off" for a damaged file
        self.assertEqual((got.session_at, got.weekly_at, got.strategy), (80.0, 90.0, 'most-room'))
        self.assertEqual(json.loads(self.raw('settings.json'))['auto'], True)     # written back
        [kept] = self.aside()
        self.assertEqual(self.raw(os.path.basename(kept)), b'{"auto": tr')
        [note] = repairs.recent(core.data_dir())
        self.assertEqual((note['what'], note['kind']), ('settings', 'restored'))
        self.assertRegex(note['text'], r"^Switchboard's settings file was damaged\. It was restored from a copy "
                                       r"made \w{3} \w{3} \d+ at \d+:\d\d [AP]M\.$")
        self.assertEqual([h['text'] for h in self.history_lines() if h['kind'] == 'repair'], [note['text']])
        core.settings()
        core.settings()
        self.assertEqual(len(repairs.recent(core.data_dir())), 1)       # said once

    def test_a_change_made_on_a_damaged_file_keeps_every_other_setting(self):
        self.set_up_settings()
        self.write('settings.json', '[1, 2')
        core.set_setting('notify', False)
        s = core.settings()
        self.assertEqual((s['auto'], s['notify'], s['auto_policy']['strategy']), (True, False, 'most-room'))

    def test_with_no_good_copy_it_starts_from_a_new_install_and_keeps_the_bad_file(self):
        self.write('settings.json', '\x00\x01 not json')
        self.assertEqual(core.settings(), {})
        self.assertFalse(os.path.exists(self.path()))                   # a new install has no settings file
        [kept] = self.aside()
        self.assertEqual(self.raw(os.path.basename(kept)), b'\x00\x01 not json')
        [note] = repairs.recent(core.data_dir())
        self.assertEqual((note['what'], note['kind']), ('settings', 'defaults'))
        self.assertIn('no good copy', note['text'])

    def test_a_missing_file_is_a_fresh_start_not_a_damage(self):
        self.assertEqual(core.settings(), {})
        self.assertEqual(self.aside(), [])
        self.assertEqual(repairs.recent(core.data_dir()), [])

    def test_a_file_that_reads_fine_is_never_replaced_by_the_copy(self):
        self.set_up_settings()
        self.write('settings.json', json.dumps({'auto': False, 'port': 7955}))     # changed by hand
        self.assertEqual(core.settings(), {'auto': False, 'port': 7955})
        self.assertFalse(autoloop.load_settings().enabled)
        self.assertEqual(self.aside(), [])
        self.assertEqual(repairs.recent(core.data_dir()), [])

    def test_a_read_that_fails_for_a_moment_uses_the_copy_and_moves_nothing(self):
        self.set_up_settings()
        before = self.raw('settings.json')
        with mock.patch.object(core, '_settings_bytes', side_effect=OSError(errno.EIO, 'I/O error')):
            self.assertTrue(core.settings()['auto'])
        self.assertEqual(self.raw('settings.json'), before)
        self.assertEqual(self.aside(), [])
        self.assertEqual(repairs.recent(core.data_dir()), [])


class PageState(Home):
    def state_with_a_profile(self):
        with core.locked() as st:
            core.ensure_account(st, 'ana@work.example')['profile'] = 'Profile 3'

    def test_a_damaged_state_file_keeps_what_the_person_set_and_says_so_once(self):
        self.state_with_a_profile()
        self.write('state.json', '{"accounts": {"ana@')
        for _ in range(3):
            st = core.load()
            self.assertEqual(st['accounts']['ana@work.example']['profile'], 'Profile 3')
        kept = self.aside('state.json')
        self.assertEqual(len(kept), 1)
        self.assertEqual(self.raw(os.path.basename(kept[0])), b'{"accounts": {"ana@')
        notes = repairs.recent(core.data_dir())
        self.assertEqual([(n['what'], n['kind']) for n in notes], [('state', 'restored')])
        with core.locked() as st:                                       # the next write puts a good file back
            st['tick_at'] = 1
        self.assertEqual(json.loads(self.raw('state.json'))['accounts']['ana@work.example']['profile'], 'Profile 3')

    def test_a_missing_state_file_is_a_fresh_start(self):
        self.assertEqual(core.load()['accounts'], {})
        self.assertEqual(repairs.recent(core.data_dir()), [])

    def test_the_state_copy_is_kept_at_most_every_ten_minutes(self):
        self.state_with_a_profile()
        first = os.stat(self.path('state.json.last-good')).st_mtime_ns
        with core.locked() as st:
            st['tick_at'] = 2
        self.assertEqual(os.stat(self.path('state.json.last-good')).st_mtime_ns, first)
        old = time.time() - core.STATE_COPY_EVERY - 5
        os.utime(self.path('state.json.last-good'), (old, old))
        with core.locked() as st:
            st['tick_at'] = 3
        self.assertEqual(json.loads(self.raw('state.json.last-good'))['tick_at'], 3)


if __name__ == '__main__':
    unittest.main()
