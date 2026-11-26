import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# A setting taken out of settings.json is taken out of its last good copy too, so a damaged file never brings it back.
import json
import os
import shutil
import tempfile
import unittest

from switchboard import core


class ASettingTakenOut(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='sbheal-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.tmp, 'data')
        os.makedirs(os.environ['SWITCHBOARD_HOME'])
        getattr(core, '_SETTINGS_FALLBACK', {}).update(sig=None, value=None)   # a fallback of a test before

    def tearDown(self):
        if self.old is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_does_not_come_back_from_the_copy(self):
        core.set_setting('auto', True)
        core.set_setting('auto_policy', {'rules': {'uuid-c': 'out'}})
        self.assertIsNone(core.update_setting('auto_policy', lambda v: core.UNSET))
        with open(core.data_path(core.SETTINGS_COPY)) as f:
            self.assertNotIn('auto_policy', json.load(f))
        with open(core.data_path('settings.json'), 'w') as f:
            f.write('{"auto": tr')                                 # damaged: it comes back from the copy
        self.assertNotIn('auto_policy', core.settings())
        self.assertTrue(core.settings().get('auto'))


if __name__ == '__main__':
    unittest.main()
