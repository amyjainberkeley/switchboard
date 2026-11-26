import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# `switchboard doctor` says when Switchboard last put Claude Code's lost sign-in back (the engine's health check, on
# its stand-ins: fakes_accounts). Made-up *.example accounts.
import json
import os
import shutil
import tempfile
import time
import unittest

from fakes_autoswitch import FakeClock
from fakes_accounts import Rig
from switchboard import engine
from switchboard.accounts import context, putback


class TheDoctorLine(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='put-back-doctor-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.tmp, 'data')
        os.makedirs(os.environ['SWITCHBOARD_HOME'])
        self.clock = FakeClock(wall=time.time(), mono=time.monotonic())
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono, sleep=self.clock.sleep)
        self.use = context.use(self.rig.ctx)
        self.use.__enter__()
        putback.reset()
        self.rig.keep('uuid-b', 'ana@home.example', 'org-b')

    def tearDown(self):
        self.use.__exit__(None, None, None)
        putback.reset()
        self.rig.close()
        if self._old_home is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def lines(self):
        return [t for _, t in engine.health_items(engine.health()) if 'sign-in' in t]

    def test_never_then_when_and_for_whom(self):
        self.assertIn((True, "Put back Claude Code's lost sign-in: never"), engine.health_items(engine.health()))
        self.rig.set_live(json.dumps({'mcpOAuth': {}}))
        putback.look(self.rig.ctx)
        self.clock.advance(putback.PUT_BACK_AFTER_S + 1)
        self.assertEqual(putback.look(self.rig.ctx).action, 'put_back')
        at = time.strftime('%H:%M on %a %b %d', time.localtime(self.clock.wall()))
        self.assertEqual(self.lines(), ["Put back Claude Code's lost sign-in: last at %s, for ana@home.example" % at])
        self.assertEqual(engine.health_lines(engine.health()), [])          # worth knowing, never a fault

    def test_a_time_out_of_range_in_the_record_still_gives_a_line(self):
        with open(self.rig.ctx.path(putback.STATE), 'w') as f:
            json.dump({'done': [], 'missing': None, 'last': {'at': 1e300, 'account': 'ana@home.example'}}, f)
        self.assertEqual(self.lines(), ["Put back Claude Code's lost sign-in: last at a time that can't be read, for "
                                        "ana@home.example"])


if __name__ == '__main__':
    unittest.main()
