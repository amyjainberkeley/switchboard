import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""The self-check (selfcheck.py) and the two Models switches.

The self-check puts back Switchboard's status line and hooks when someone else takes them out of Claude Code's
settings while they are on in Switchboard. It knows nothing of the Models switches, and these tests keep it so:

- a Models switch turned off is never put back: not its helper files, not its note, not its lines in `env`;
- with both switches on, the self-check, its look-only form and doctor never report the four helper files, the note
  or the two `env` lines as missing or as someone else's, and never change them;
- the status line put back while both switches are on leaves every Models line and file as it was;
- a helper file or a Models line taken out by hand stays out: a change the person makes to those files is theirs
  (models.py), so the self-check does not fight it.

Every file here is a throwaway one; nothing is run but this test's own Python.
  SWITCHBOARD_HOME="$(mktemp -d)/data" python3 -m unittest discover -s tests -p 'test_models_selfcheck.py'"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import models, selfcheck, server
from switchboard import notify_hooks as nh, session_hooks as sh, statusline as sl

HERS = '{\n  "model": "opus",\n  "env": {\n    "OTHER": "1"\n  },\n  "theme": "dark"\n}\n'
T = time.time()
D = 86400.0


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-models-selfcheck-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        self.data = os.path.join(self.tmp, 'data')
        self.claude = os.path.join(self.home, '.claude')
        os.makedirs(self.claude)
        os.makedirs(self.data)
        env = mock.patch.dict(os.environ, {'HOME': self.home, 'SWITCHBOARD_HOME': self.data,
                                           'CLAUDE_CONFIG_DIR': self.claude})
        env.start()
        self.addCleanup(env.stop)
        managed = os.path.join(self.tmp, 'managed')
        os.makedirs(managed)
        for target, name, value in ((nh, 'MANAGED', managed), (sl, 'precompile', lambda *a, **k: None),
                                    (nh, 'precompile', lambda *a, **k: None), (sh, 'precompile', lambda *a, **k: None),
                                    (nh, 'verify', lambda cmd: None), (sh, 'verify', lambda cmd: None),
                                    (server, 'MODELS_KW', {'environ': {}, 'managed_dir': managed, 'versions': [
                                        {'where': '/x/claude', 'version': '2.1.320', 'primary': True}]})):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.path = os.path.join(self.claude, 'settings.json')
        self.assertTrue(sl.settings_path().startswith(self.tmp))
        with open(self.path, 'w') as f:
            f.write(HERS)

    def read(self, path=None):
        with open(path or self.path, 'rb') as f:
            return f.read()

    def env(self):
        return json.loads(self.read().decode('utf-8')).get('env') or {}

    def ours(self):
        """Every file of Models by the job in Claude Code's folder: {path relative to it: bytes}."""
        out = {}
        for folder in (models.AGENTS_DIR, models.RULES_DIR):
            d = os.path.join(self.claude, folder)
            for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
                out[os.path.join(folder, name)] = self.read(os.path.join(d, name))
        return out

    def both_on(self):
        self.assertTrue(server.models_turn(True, 'helpers')['changed'])
        self.assertTrue(server.models_turn(True, 'sonnet')['changed'])
        self.assertEqual(len(self.ours()), 5)                               # four helpers and the note
        self.assertEqual((self.env().get(models.SUBAGENT_VAR), self.env().get(models.SUBAGENT_EFFORT_VAR)),
                         ('sonnet', 'high'))

    def statusline_on(self):
        self.assertTrue(sl.apply(sl.install_plan(self.path, command_line=sl.command()))['changed'])

    def said_of_models(self, results):
        """What any result, notice or doctor line says about the Models switches' files or lines."""
        words = ('agents', 'rules', 'helper', 'switchboard-', models.SUBAGENT_VAR, models.SUBAGENT_EFFORT_VAR,
                 'Models', 'Sonnet')
        texts = [' '.join(str(x) for x in (r.name, r.looked, r.found, r.did, r.fix)) for r in results]
        texts += [n['text'] for n in selfcheck.notices(now=T)]
        texts += [' '.join(str(x) for x in line) for line in selfcheck.doctor_lines(now=T)]
        return [t for t in texts if any(w in t for w in words)]


class TurnedOffStaysOff(Base):
    def test_neither_switch_is_put_back_after_off(self):
        self.statusline_on()
        self.both_on()
        self.assertTrue(server.models_turn(False, 'helpers')['changed'])
        self.assertTrue(server.models_turn(False, 'sonnet')['changed'])
        before = self.read()
        for now in (T, T + 2 * D):                                          # later runs too: nothing comes back
            out = selfcheck.run(fix=True, now=now)
            self.assertEqual(self.said_of_models(out), [])
        self.assertEqual(self.ours(), {})
        self.assertNotIn(models.SUBAGENT_VAR, self.env())
        self.assertNotIn(models.SUBAGENT_EFFORT_VAR, self.env())
        self.assertEqual(self.read(), before)
        self.assertTrue(sl.is_ours(json.loads(self.read().decode('utf-8')).get('statusLine')))

    def test_one_off_one_on_only_the_one_on_stays(self):
        self.both_on()
        self.assertTrue(server.models_turn(False, 'sonnet')['changed'])
        files = self.ours()
        selfcheck.run(fix=True, now=T)
        self.assertEqual(self.ours(), files)
        self.assertNotIn(models.SUBAGENT_VAR, self.env())
        self.assertEqual(server.models_view()['enabled'], False)
        self.assertTrue(server.models_view()['helpers']['enabled'])


class OnIsNeverForeign(Base):
    def test_both_on_nothing_is_said_or_changed(self):
        self.statusline_on()
        self.both_on()
        before, files = self.read(), self.ours()
        out = selfcheck.run(fix=False, now=T) + selfcheck.run(fix=True, now=T)
        self.assertEqual(self.said_of_models(out), [])
        self.assertFalse([r for r in out if r.name.startswith('entry:') and r.state != selfcheck.OK], out)
        self.assertEqual((self.read(), self.ours()), (before, files))

    def test_the_status_line_put_back_keeps_every_models_line_and_file(self):
        self.statusline_on()
        self.both_on()
        files = self.ours()
        o = json.loads(self.read().decode('utf-8'))
        o.pop('statusLine')                                                 # another hand takes it out
        with open(self.path, 'w') as f:
            json.dump(o, f, indent=2)
        r = [x for x in selfcheck.run(fix=True, now=T) if x.name == 'entry:statusline'][0]
        self.assertEqual((r.state, r.did), (selfcheck.REPAIRED, 'put back'))
        self.assertEqual((self.env().get(models.SUBAGENT_VAR), self.env().get(models.SUBAGENT_EFFORT_VAR)),
                         ('sonnet', 'high'))
        self.assertEqual(self.ours(), files)
        # and each switch's Off still takes out exactly its own lines and files
        self.assertTrue(server.models_turn(False, 'sonnet')['changed'])
        self.assertTrue(server.models_turn(False, 'helpers')['changed'])
        self.assertEqual(self.ours(), {})
        self.assertEqual(self.env(), {'OTHER': '1'})
        self.assertTrue(sl.is_ours(json.loads(self.read().decode('utf-8')).get('statusLine')))


class TakenOutByHandStaysOut(Base):
    def test_a_helper_file_and_the_lines_taken_out_by_hand_are_not_put_back(self):
        self.both_on()
        gone = os.path.join(self.claude, models.AGENTS_DIR, 'switchboard-reviewer.md')
        os.remove(gone)
        o = json.loads(self.read().decode('utf-8'))
        o['env'] = {'OTHER': '1'}
        with open(self.path, 'w') as f:
            json.dump(o, f, indent=2)
        before, files = self.read(), self.ours()
        out = selfcheck.run(fix=True, now=T)
        self.assertEqual(self.said_of_models(out), [])
        self.assertEqual((self.read(), self.ours()), (before, files))
        self.assertFalse(os.path.exists(gone))


if __name__ == '__main__':
    unittest.main()
