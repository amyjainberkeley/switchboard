import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
import os

# The page's two Models switches and `switchboard models`, through the server's own functions: Models by the job
# (four helper files and one note, no setting) and Sonnet for helpers (the model line, and the effort line where
# Claude Code reads it). Either, both or neither, each undone exactly, and the command says both. Every test works
# in a made-up home with a made-up Claude Code folder and version (test_app.ModelsBase); nothing reaches a page.
import errno
import io
import itertools
import json
import shutil
import subprocess
import sys
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

import test_app
from switchboard import __main__ as cli
from switchboard import core, demo, history, models, server, term

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'models')
NEW = [{'where': '/x/claude', 'version': '2.1.320', 'primary': True}]     # reads CLAUDE_CODE_SUBAGENT_EFFORT
FILES = ['switchboard-lookup.md', 'switchboard-reader.md', 'switchboard-reviewer.md', 'switchboard-verifier.md']
DEAD = '7985'       # this round's port, proven dead before every run: nothing here may reach a page


class Base(test_app.ModelsBase):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': DEAD})
        env.start()
        self.addCleanup(env.stop)
        self.agents = os.path.join(self.claude, 'agents')
        self.rules = os.path.join(self.claude, 'rules')

    def new_claude_code(self):
        self.patch(server, 'MODELS_KW', dict(server.MODELS_KW, versions=NEW))

    def turn(self, part, on):
        return server.act('/api/models', {'enabled': on, 'part': part})

    def files(self):
        out = {}
        for dp, dn, fn in os.walk(self.claude):
            for f in fn:
                if not f.endswith('.switchboard-backup'):
                    p = os.path.join(dp, f)
                    with open(p, 'rb') as fh:
                        out[os.path.relpath(p, self.claude)] = fh.read()
        return out

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(list(args))
            except core.SwitchError as e:
                err.write(str(e) + '\n')
                code = 1
        return code, out.getvalue(), err.getvalue()


class ModelsByTheJob(Base):

    def test_on_writes_four_helpers_and_a_note_and_no_setting_and_off_takes_them_out(self):
        before = self.read()
        v = server.models_view()
        self.assertEqual((v['helpers']['enabled'], v['enabled']), (False, False))
        self.assertEqual(self.turn('helpers', True), {'changed': True, 'note': None})
        self.assertEqual(self.read(), before)                               # no setting at all
        self.assertEqual(sorted(os.listdir(self.agents)), FILES)
        self.assertEqual(os.listdir(self.rules), [models.NOTE_FILE])
        v = server.models_view()
        self.assertEqual((v['helpers'], v['enabled']), ({'enabled': True, 'blocked': None, 'left_out': []}, False))
        last = history.read(1)[-1]
        self.assertEqual((last['kind'], last['text'], last.get('by')), ('setting', 'Models by the job: on', 'you'))
        self.assertEqual(self.turn('helpers', False), {'changed': True, 'note': None})
        self.assertEqual(self.files(), {'settings.json': before})
        self.assertFalse(os.path.exists(self.agents) or os.path.exists(self.rules))
        self.assertEqual(history.read(1)[-1]['text'], 'Models by the job: off')
        self.assertEqual(self.turn('helpers', False), {'changed': False, 'note': None})

    def test_your_own_helper_with_one_of_the_names_stays_and_is_named(self):
        os.makedirs(self.agents)
        shutil.copy(os.path.join(FIX, 'agents', 'reviewer.md'), self.agents)
        v = server.models_view()['helpers']
        self.assertEqual(v['left_out'], [{'name': 'reviewer', 'why': 'you have your own'}])
        self.turn('helpers', True)
        self.assertEqual(sorted(os.listdir(self.agents)), sorted([f for f in FILES if 'reviewer' not in f] +
                                                                 ['reviewer.md']))
        v = server.models_view()
        self.assertEqual(v['helpers']['left_out'], [{'name': 'reviewer', 'why': 'you have your own'}])
        self.assertEqual(term.helpers_line(v), 'Models by the job: on. Left out: reviewer (you have your own).')
        self.turn('helpers', False)
        self.assertEqual(os.listdir(self.agents), ['reviewer.md'])

    def test_your_own_helper_named_in_a_yaml_block_stays_and_is_named(self):
        os.makedirs(self.agents)
        with open(os.path.join(self.agents, 'mine.md'), 'w') as f:
            f.write('---\nname: >-\n  reviewer\ndescription: Mine.\n---\n\nMine.\n')
        mine = [{'name': 'reviewer', 'why': 'you have your own'}]
        self.assertEqual(server.models_view()['helpers']['left_out'], mine)
        self.turn('helpers', True)
        self.assertEqual(sorted(os.listdir(self.agents)), sorted([f for f in FILES if 'reviewer' not in f] +
                                                                 ['mine.md']))
        self.assertEqual(server.models_view()['helpers']['left_out'], mine)
        self.turn('helpers', False)
        self.assertEqual(os.listdir(self.agents), ['mine.md'])

    def test_when_every_name_is_yours_it_says_so_and_changes_nothing(self):
        os.makedirs(self.agents)
        for h in models.HELPERS:
            with open(os.path.join(self.agents, 'mine-%s.md' % h['name']), 'w') as f:
                f.write('---\nname: %s\ndescription: Mine.\n---\nMine.\n' % h['name'])
        before = self.files()
        self.assertEqual(server.models_view()['helpers']['blocked'], 'You have your own helpers with these names.')
        with self.assertRaises(core.SwitchError):
            self.turn('helpers', True)
        self.assertEqual(self.files(), before)

    def test_when_your_settings_deny_every_one_it_says_that(self):
        with open(self.settings, 'w') as f:
            json.dump({'permissions': {'deny': ['Agent(%s)' % h['name'] for h in models.HELPERS]}}, f)
        self.assertEqual(server.models_view()['helpers']['blocked'],
                         'Every helper is left out here: lookup (your settings deny it), reader (your settings deny '
                         'it), reviewer (your settings deny it), verifier (your settings deny it).')
        with self.assertRaises(core.SwitchError):
            self.turn('helpers', True)
        self.assertFalse(os.path.exists(self.agents))

    def test_an_edited_helper_stays_when_it_goes_off(self):
        self.turn('helpers', True)
        path = os.path.join(self.agents, 'switchboard-verifier.md')
        with open(path, 'a') as f:
            f.write('- Run the slow tests too.\n')
        self.turn('helpers', False)
        self.assertEqual(os.listdir(self.agents), ['switchboard-verifier.md'])
        self.assertFalse(os.path.exists(self.rules))

    def test_a_record_that_cant_be_read_is_never_off_while_its_files_may_be_there(self):
        self.turn('helpers', True)
        with open(models.default_state_path(), 'w') as f:
            f.write('{"version": ')                                      # cut short
        v = server.models_view()
        self.assertIsNone(v['helpers']['enabled'])
        self.assertTrue(term.helpers_line(v).startswith("Models by the job: unknown. Switchboard's record of this "
                                                        'setting ('), term.helpers_line(v))
        with self.assertRaises(core.SwitchError):
            self.turn('helpers', False)
        self.assertEqual(sorted(os.listdir(self.agents)), FILES)         # nothing was taken out
        shutil.rmtree(self.agents)
        shutil.rmtree(self.rules)
        self.assertEqual(server.models_view()['helpers']['enabled'], False)
        self.assertEqual(self.turn('helpers', False), {'changed': False, 'note': None})

    def test_the_part_must_be_one_of_the_two(self):
        for part in ('explore', 'everything', None, 1):
            with self.subTest(part=part), self.assertRaises(core.SwitchError):
                server.act('/api/models', {'enabled': True, 'part': part})
        self.assertFalse(os.path.exists(self.agents))


class SonnetForHelpers(Base):

    def test_a_new_claude_code_gets_the_effort_line_too_and_off_gives_the_bytes_back(self):
        self.new_claude_code()
        before = self.read()
        self.assertEqual(server.models_view()['effort'], 'high')                # what it would put in
        self.turn('sonnet', True)
        self.assertEqual(json.loads(self.read())['env'], {'OTHER': '1', models.SUBAGENT_VAR: 'sonnet',
                                                          models.SUBAGENT_EFFORT_VAR: 'high'})
        v = server.models_view()
        self.assertEqual((v['enabled'], v['effort']), (True, 'high'))
        self.assertEqual(term.models_line(v), 'Sonnet for helpers: on, at high effort')
        self.assertFalse(os.path.exists(self.agents))                          # the other switch did not move
        self.turn('sonnet', False)
        self.assertEqual(self.read(), before)

    def test_an_older_claude_code_gets_the_model_line_alone(self):
        self.assertEqual(server.models_view()['effort'], None)                  # 2.1.309: no effort line
        self.turn('sonnet', True)
        self.assertNotIn(models.SUBAGENT_EFFORT_VAR, json.loads(self.read())['env'])
        self.assertEqual(term.models_line(server.models_view()), 'Sonnet for helpers: on')

    def test_off_after_you_changed_its_effort_takes_out_the_model_line_alone(self):
        self.new_claude_code()
        self.turn('sonnet', True)
        with open(self.settings) as f:
            d = json.load(f)
        d['env'][models.SUBAGENT_EFFORT_VAR] = 'max'
        with open(self.settings, 'w') as f:
            json.dump(d, f, indent=2)
        self.assertEqual(self.turn('sonnet', False)['changed'], True)
        self.assertEqual(json.loads(self.read())['env'], {'OTHER': '1', models.SUBAGENT_EFFORT_VAR: 'max'})

    def test_a_record_from_the_build_before_goes_off_byte_for_byte(self):
        with open(os.path.join(FIX, 'settings', 'unrelated.json'), 'rb') as f:
            original = f.read()
        with open(os.path.join(FIX, 'record-before', 'settings.json'), 'rb') as f:
            on = f.read()
        with open(self.settings, 'wb') as f:
            f.write(on)
        os.chmod(self.settings, 0o644)
        with open(self.settings + '.switchboard-backup', 'wb') as f:
            f.write(original)
        state = models.default_state_path()
        os.makedirs(os.path.dirname(state), exist_ok=True)
        with open(os.path.join(FIX, 'record-before', 'models.json')) as f:
            text = f.read().replace('@CLAUDE@', os.path.realpath(self.claude)).replace('@DATA@', os.path.dirname(state))
        with open(state, 'w') as f:
            f.write(text)
        v = server.models_view()
        self.assertEqual((v['enabled'], v['effort'], v['helpers']['enabled']), (True, None, False))
        self.assertEqual(self.cli('models'), (0, 'Models by the job: off\nSonnet for helpers: on\n', ''))
        self.turn('helpers', True)                                  # the other switch: its line stays as it is
        self.assertEqual(self.read(), on)
        self.turn('helpers', False)
        self.assertEqual(self.turn('sonnet', False), {'changed': True, 'note': None})
        self.assertEqual(self.read(), original)


class EffortLevelComesFirst(Base):

    def test_both_rows_and_the_command_say_it(self):
        with open(self.settings, 'w') as f:
            f.write('{"env": {"CLAUDE_CODE_EFFORT_LEVEL": "high"}}')
        v = server.models_view()
        self.assertEqual(v['effort_level'], {'value': 'high', 'where': 'your user settings'})
        line = 'CLAUDE_CODE_EFFORT_LEVEL=high in your user settings comes first: every helper runs at that effort.'
        self.assertEqual(term.effort_first_line(v), line)
        self.assertEqual(self.cli('models'), (0, 'Models by the job: off\nSonnet for helpers: off\n' + line + '\n', ''))
        with open(self.settings, 'w') as f:
            f.write('{}')
        self.assertIsNone(server.models_view()['effort_level'])
        self.assertIsNone(term.effort_first_line(server.models_view()))


class TheTwoTogether(Base):

    def test_every_order_gives_the_same_files(self):
        self.new_claude_code()
        start = self.files()
        seen = {frozenset(): start}
        steps = [('helpers', True), ('sonnet', True), ('helpers', False), ('sonnet', False)]
        for order in itertools.permutations(steps):
            on = set()
            if any((p, False) in order[:order.index((p, True))] for p in ('helpers', 'sonnet')):
                continue                                            # off before on: not an order of turning
            for part, turn_on in order:
                self.assertTrue(self.turn(part, turn_on)['changed'])
                on = (on | {part}) if turn_on else (on - {part})
                with self.subTest(order=order, on=sorted(on)):
                    got = self.files()
                    self.assertEqual(got, seen.setdefault(frozenset(on), got))
                    v = server.models_view()
                    self.assertEqual((v['helpers']['enabled'], v['enabled']), ('helpers' in on, 'sonnet' in on))
            self.assertEqual(self.files(), start)
        self.assertEqual(len(seen), 4)

    def test_one_switch_never_touches_the_others_files(self):
        self.new_claude_code()
        self.turn('helpers', True)
        helpers = dict((k, v) for k, v in self.files().items() if k != 'settings.json')
        self.turn('sonnet', True)
        self.turn('sonnet', False)
        self.assertEqual(dict((k, v) for k, v in self.files().items() if k != 'settings.json'), helpers)
        self.turn('sonnet', True)
        settings = self.read()
        self.turn('helpers', False)
        self.assertEqual(self.read(), settings)


class TheCommand(Base):
    FORMS = 'switchboard models [on|off]\nswitchboard models sonnet on|off\n'

    def test_alone_it_says_both(self):
        self.assertEqual(self.cli('models'), (0, 'Models by the job: off\nSonnet for helpers: off\n', ''))

    def test_on_and_off_turn_models_by_the_job(self):
        before = self.read()
        self.assertEqual(self.cli('models', 'on'), (0, 'Models by the job: on\nSonnet for helpers: off\n', ''))
        self.assertEqual(sorted(os.listdir(self.agents)), FILES)
        self.assertEqual(self.cli('models', 'off'), (0, 'Models by the job: off\nSonnet for helpers: off\n', ''))
        self.assertEqual(self.files(), {'settings.json': before})

    def test_sonnet_on_and_off_turn_sonnet_for_helpers(self):
        self.new_claude_code()
        before = self.read()
        self.assertEqual(self.cli('models', 'sonnet', 'on'),
                         (0, 'Models by the job: off\nSonnet for helpers: on, at high effort\n', ''))
        self.assertEqual(history.read(1)[-1]['text'], 'Sonnet for helpers: on')
        self.assertEqual(self.cli('models', 'sonnet', 'off'),
                         (0, 'Models by the job: off\nSonnet for helpers: off\n', ''))
        self.assertEqual(self.read(), before)

    def test_other_words_say_both_forms(self):
        for args in (('models', 'bogus'), ('models', 'sonnet'), ('models', 'sonnet', 'bogus'), ('models', 'on', 'off'),
                     ('models', 'helpers', 'on'), ('models', 'ON'), ('models', 'sonnet', 'on', 'off')):
            with self.subTest(args=args):
                self.assertEqual(self.cli(*args), (2, self.FORMS, ''))
        self.assertFalse(os.path.exists(self.agents))

    def test_the_demo_changes_neither(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True
        self.assertEqual(self.cli('models', 'on'),
                         (1, '', 'The demo has made-up accounts; turn Models by the job on or off on its page.\n'))
        self.assertEqual(self.cli('models', 'sonnet', 'on'),
                         (1, '', 'The demo has made-up accounts; turn Sonnet for helpers on or off on its page.\n'))

    def test_its_help_has_both_forms(self):
        got = cli.command_help('models')
        self.assertTrue(got.startswith('switchboard models [on|off] Models by the job: four helpers'), got)
        self.assertIn('\nswitchboard models sonnet on|off\n', got)
        self.assertIn('Sonnet for helpers', got)


# ---------- found by the review of the first build (each of these failed on it) ----------

LINK_OFF = ("Claude Code's settings file is a link now, and Switchboard doesn't write through links. Take its lines "
            'out of that file yourself.')
LINK_ON = "Claude Code's settings file is a link, and Switchboard doesn't write through links."
READ_ONLY = "Claude Code's settings file is set read-only, so Switchboard leaves it alone."
UNREADABLE = ("Claude Code's settings file can't be read, so Switchboard can't tell whether you deny one of these "
              'helpers.')
OWN_FOLDER = 'Switchboard could not write in its own folder (~/.switchboard). Nothing was changed.'
COULD_NOT = 'Switchboard could not make that change. Nothing was written.'


class BothPressedAtOnce(Base):

    def test_one_record_holds_both_and_both_off_put_everything_back(self):
        self.new_claude_code()
        before = self.files()
        for trial in range(8):
            gate, res = threading.Barrier(2), {}

            def go(part):
                gate.wait()
                res[part] = self.turn(part, True)
            ts = [threading.Thread(target=go, args=(p,)) for p in ('helpers', 'sonnet')]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            v = server.models_view()
            self.assertEqual((res['helpers']['changed'], res['sonnet']['changed']), (True, True))
            self.assertEqual((v['helpers']['enabled'], v['enabled']), (True, True), 'trial %d' % trial)
            self.turn('helpers', False)
            self.turn('sonnet', False)
            self.assertEqual(self.files(), before, 'trial %d' % trial)

    def test_every_writer_holds_the_lock(self):
        held = []
        real = server._models_turn
        self.patch(server, '_models_turn', lambda on, part: held.append(server.MODELS_LOCK.locked()) or real(on, part))
        self.turn('helpers', True)
        self.turn('helpers', False)
        self.assertEqual(held, [True, True])


class SettingsBecomeALinkOrReadOnlyAfterOn(Base):

    def link(self):
        target = os.path.join(self.tmp, 'dotfiles', 'settings.json')
        os.makedirs(os.path.dirname(target))
        shutil.move(self.settings, target)
        os.symlink(target, self.settings)
        return target

    def off_is_refused_through_the_link(self, order):
        self.new_claude_code()
        for part in order:
            self.turn(part, True)
        target = self.link()
        with open(target, 'rb') as f:
            held = f.read()
        with self.assertRaises(core.SwitchError) as e:
            self.turn('sonnet', False)
        self.assertEqual(str(e.exception), LINK_OFF)
        self.assertTrue(os.path.islink(self.settings))
        with open(target, 'rb') as f:
            self.assertEqual(f.read(), held)
        self.assertTrue(server.models_view()['enabled'])                 # the row stays on
        with open(target, 'w') as f:                                        # you take its lines out by hand
            f.write(self.ORIGINAL)
        self.assertFalse(server.models_view()['enabled'])                # and it reads off
        self.assertEqual(self.turn('sonnet', False), {'changed': False, 'note': None})

    def test_after_sonnet_on(self):
        self.off_is_refused_through_the_link(('sonnet',))

    def test_after_helpers_then_sonnet(self):
        self.off_is_refused_through_the_link(('helpers', 'sonnet'))

    def test_after_sonnet_then_helpers(self):
        self.off_is_refused_through_the_link(('sonnet', 'helpers'))

    def test_read_only_after_on(self):
        for order in (('sonnet',), ('sonnet', 'helpers')):
            with self.subTest(order=order):
                self.new_claude_code()
                for part in order:
                    self.turn(part, True)
                os.chmod(self.settings, 0o444)
                held = self.read()
                with self.assertRaises(core.SwitchError) as e:
                    self.turn('sonnet', False)
                self.assertEqual(str(e.exception), READ_ONLY)
                self.assertEqual((self.read(), oct(os.stat(self.settings).st_mode & 0o777)), (held, '0o444'))
                self.assertTrue(server.models_view()['enabled'])
                os.chmod(self.settings, 0o644)
                self.turn('sonnet', False)
                self.turn('helpers', False)

    def test_on_with_a_linked_file_says_one_short_line(self):
        self.link()
        with self.assertRaises(core.SwitchError) as e:
            self.turn('sonnet', True)
        self.assertEqual(str(e.exception), LINK_ON)
        self.assertEqual(server.models_view()['blocked'], LINK_ON)


class ModelsByTheJobsOwnWords(Base):

    def test_its_own_version(self):
        self.patch(server, 'MODELS_KW', dict(server.MODELS_KW, versions=[{'where': '/x/claude', 'version': '2.1.260',
                                                                           'primary': True}]))
        v = server.models_view()
        self.assertEqual((v['helpers']['blocked'], v['blocked']), ('Needs Claude Code 2.1.271 or newer.', None))
        self.assertEqual(self.cli('models'), (0, 'Models by the job: off. Needs Claude Code 2.1.271 or newer.\n'
                                                 'Sonnet for helpers: off\n', ''))
        self.assertEqual(self.cli('models', 'on'), (1, '', 'Needs Claude Code 2.1.271 or newer.\n'))
        self.assertFalse(os.path.exists(self.agents))

    def test_a_read_only_settings_file_does_not_block_it(self):
        os.chmod(self.settings, 0o444)
        self.addCleanup(os.chmod, self.settings, 0o644)
        self.assertIsNone(server.models_view()['helpers']['blocked'])
        self.assertEqual(self.turn('helpers', True)['changed'], True)
        self.assertEqual(sorted(os.listdir(self.agents)), FILES)
        self.turn('helpers', False)

    def test_a_settings_file_that_cant_be_read_blocks_it_in_its_own_words(self):
        with open(self.settings, 'w') as f:
            f.write('{not json')
        v = server.models_view()
        self.assertEqual(v['helpers']['blocked'], UNREADABLE)                 # before any press: nothing was tried
        self.assertEqual(term.helpers_line(v), 'Models by the job: off. ' + UNREADABLE)
        with self.assertRaises(core.SwitchError) as e:
            self.turn('helpers', True)
        self.assertEqual(str(e.exception), UNREADABLE + ' Nothing was written.')
        self.assertEqual(models.HELPERS_UNREADABLE, UNREADABLE + ' Nothing was written.')

    def test_a_failed_write_names_the_folders(self):
        os.makedirs(self.agents)
        os.chmod(self.agents, 0o555)
        self.addCleanup(os.chmod, self.agents, 0o755)
        with self.assertRaises(core.SwitchError) as e:
            self.turn('helpers', True)
        self.assertEqual(str(e.exception), "Switchboard could not write in Claude Code's agents or rules folder. "
                                           'Nothing was changed.')
        self.assertEqual(os.listdir(self.agents), [])
        self.assertFalse(os.path.exists(self.rules))


# ---------- found by the second read of the fixes (each of these failed on the build before) ----------

class OffOnAReadOnlyFileSwitchboardMade(Base):
    """No settings file; Sonnet for helpers on makes one; it is set read-only; Off. Both ways Off can take (the exact
    undo, and the plan after the other switch moved) refuse in the same words, and the row stays on."""

    def off_is_refused(self, between):
        os.remove(self.settings)
        self.new_claude_code()
        self.assertTrue(self.turn('sonnet', True)['changed'])
        for part, on in between:
            self.turn(part, on)
        os.chmod(self.settings, 0o444)
        held = self.read()
        with self.assertRaises(core.SwitchError) as e:
            self.turn('sonnet', False)
        self.assertEqual(str(e.exception), READ_ONLY)
        self.assertEqual((self.read(), oct(os.stat(self.settings).st_mode & 0o777)), (held, '0o444'))
        self.assertTrue(server.models_view()['enabled'])
        os.chmod(self.settings, 0o644)
        self.turn('sonnet', False)
        self.assertFalse(os.path.exists(self.settings))                    # it made the file: Off takes it away

    def test_by_the_exact_undo(self):
        self.off_is_refused(())

    def test_by_the_plan(self):
        self.off_is_refused((('helpers', True),))


class AFailedWriteNamesTheRightFolder(Base):
    """An OSError on a press names the folder it happened in: Switchboard's own, Claude Code's agents or rules folder,
    or its settings file; anything else is said plainly."""

    def setUp(self):
        super().setUp()
        self.own = os.path.join(self.home, '.switchboard')          # Switchboard's folder where it usually is
        env = mock.patch.dict(os.environ, {'SWITCHBOARD_HOME': self.own})
        env.start()
        self.addCleanup(env.stop)
        self.new_claude_code()

    def refused(self, part, on=True, folder=None):
        folder = folder or self.own
        os.chmod(folder, 0o555)
        try:
            with self.assertRaises(core.SwitchError) as e:
                server.models_turn(on, part)
        finally:
            os.chmod(folder, 0o755)
        return str(e.exception)

    def test_the_lock_file_cant_be_made(self):
        os.makedirs(self.own)
        for part in ('helpers', 'sonnet'):
            with self.subTest(part=part):
                before = self.files()
                self.assertEqual(self.refused(part), OWN_FOLDER)
                self.assertEqual(self.files(), before)

    def test_its_record_cant_be_saved(self):
        for first, second in (('sonnet', 'helpers'), ('helpers', 'sonnet')):
            with self.subTest(second=second):
                server.models_turn(True, first)
                before = self.files()
                self.assertEqual(self.refused(second), OWN_FOLDER)
                self.assertEqual(self.files(), before)                      # what it wrote was put back
                self.assertEqual(os.path.exists(self.agents), first == 'helpers')
                server.models_turn(False, first)

    def test_a_copy_in_its_own_folder_cant_be_written(self):
        server.models_turn(True, 'helpers')
        server.models_turn(True, 'sonnet')          # Off for helpers now takes the plan's way, which keeps copies
        backups = os.path.join(self.own, 'models-backups')
        os.makedirs(backups)
        before = self.files()
        self.assertEqual(self.refused('helpers', on=False, folder=backups), OWN_FOLDER)
        self.assertEqual(self.files(), before)

    def test_its_settings_file_keeps_its_own_line(self):
        before = self.read()
        self.assertEqual(self.refused('sonnet', folder=self.claude), "Claude Code's settings file can't be read.")
        self.assertEqual(self.read(), before)

    def test_anything_else_is_said_plainly(self):
        for filename in (os.path.join(self.tmp, 'elsewhere', 'x'), None):
            for part in ('helpers', 'sonnet'):
                with self.subTest(part=part, filename=filename):
                    with mock.patch.object(models, 'apply', side_effect=OSError(errno.EIO, 'Input/output error',
                                                                                 filename)):
                        with self.assertRaises(core.SwitchError) as e:
                            server.models_turn(True, part)
                    self.assertEqual(str(e.exception), COULD_NOT)


class ARefusalNamesItsOwnFile(Base):
    """A file that becomes a link, or is set read-only, between the plan and the write: the line names Claude Code's
    settings file only when that is the file, and says to take its lines out only on Off."""

    def refused(self, part, on, path, why):
        text = {'link': "%s is a link now, and Switchboard doesn't write through links. Nothing was changed.",
                'read-only': '%s is set read-only, so Switchboard leaves it alone. Nothing was changed.'}[why] % path
        if not on:
            self.turn(part, True)
        with mock.patch.object(models, 'undo', side_effect=models.Refused('%s changed since.' % path)), \
                mock.patch.object(models, 'apply', side_effect=models.Refused(text)):
            with self.assertRaises(core.SwitchError) as e:
                self.turn(part, on)
        if not on:
            self.turn(part, False)
        return str(e.exception)

    def test_each_line(self):
        cd = os.path.realpath(self.claude)
        settings = os.path.join(cd, 'settings.json')
        note = os.path.join(cd, 'rules', models.NOTE_FILE)
        reviewer = os.path.join(cd, 'agents', 'switchboard-reviewer.md')
        for part, on, path, why, want in (
                ('sonnet', True, settings, 'link', LINK_ON),
                ('sonnet', False, settings, 'link', LINK_OFF),
                ('sonnet', True, settings, 'read-only', READ_ONLY),
                ('sonnet', False, settings, 'read-only', READ_ONLY),
                ('helpers', True, reviewer, 'link', COULD_NOT),
                ('helpers', False, note, 'link', COULD_NOT),
                ('helpers', True, note, 'read-only', COULD_NOT),
                ('helpers', False, reviewer, 'read-only', COULD_NOT)):
            with self.subTest(part=part, on=on, file=os.path.basename(path), why=why):
                self.assertEqual(self.refused(part, on, path, why), want)
                v = server.models_view()
                self.assertEqual((v['enabled'], v['helpers']['enabled']), (False, False))


# One press from another process, as `switchboard models` in a terminal makes it while the page makes its own. It
# refuses to run unless every folder it was given is under the test's own temp folder. Once it has imported
# everything it makes its ready file and waits for the go file, so a slow import can't put the two presses apart.
CHILD = r'''
import json, os, sys, time
tree, tests, root = sys.argv[1], sys.argv[2], os.path.realpath(sys.argv[3])
part, ready, go, kw = sys.argv[4], sys.argv[5], sys.argv[6], json.loads(sys.argv[7])
for what, p in (('SWITCHBOARD_HOME', os.environ.get('SWITCHBOARD_HOME')), ('claude_dir', kw.get('claude_dir')),
                ('state_path', kw.get('state_path')), ('managed_dir', kw.get('managed_dir'))):
    if not p or not os.path.realpath(p).startswith(root + os.sep):
        raise SystemExit('not a made-up folder: %s' % what)
sys.path.insert(0, tree)
sys.path.insert(0, tests)
import _isolate  # noqa: F401  (it gives this process a throwaway home: the folders come in kw)
from switchboard import core, server
os.fsync = lambda fd: None
server.MODELS_KW = kw
open(ready, 'w').close()
while not os.path.exists(go):
    pass
t0 = time.time()
try:
    out = {'ok': True, 'r': server.models_turn(True, part)}
except core.SwitchError as e:
    out = {'ok': False, 'why': str(e)}
out.update(t0=t0, t1=time.time())
print(json.dumps(out))
'''


class TwoProcessesPressAtOnce(Base):
    """The page and `switchboard models` in a terminal are two processes, which the thread lock can't see: one press
    each, released together, must both land in the one record."""

    def test_both_presses_land(self):
        self.new_claude_code()
        tests = os.path.dirname(os.path.abspath(__file__))
        script = os.path.join(self.tmp, 'press_once.py')
        with open(script, 'w') as f:
            f.write(CHILD)
        kw = json.dumps(dict(server.MODELS_KW, claude_dir=self.claude, state_path=models.default_state_path()))
        start = self.files()
        together = 0
        for trial in range(3):
            go = os.path.join(self.tmp, 'go-%d' % trial)
            ready = [os.path.join(self.tmp, 'ready-%d-%s' % (trial, part)) for part in ('helpers', 'sonnet')]
            ps = [subprocess.Popen([sys.executable, '-B', script, os.path.dirname(tests), tests, self.tmp, part,
                                    r, go, kw],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ))
                  for part, r in zip(('helpers', 'sonnet'), ready)]
            # Release both only when both are ready; a child that stopped early is read below.
            end = time.time() + 180
            while (not all(os.path.exists(r) for r in ready) and all(p.poll() is None for p in ps)
                   and time.time() < end):
                time.sleep(0.01)
            open(go, 'w').close()
            outs = []
            for p in ps:
                o, e = p.communicate(timeout=180)
                lines = [ln for ln in o.decode('utf-8', 'replace').splitlines() if ln.startswith('{"ok"')]
                outs.append(json.loads(lines[-1]) if lines else {'ok': False, 'why': e.decode('utf-8', 'replace')})
            if all('t0' in o for o in outs) and abs(outs[0]['t0'] - outs[1]['t0']) < 0.05:
                together += 1
            self.assertEqual([bool(o['ok'] and o['r']['changed']) for o in outs], [True, True], outs)
            v = server.models_view()
            self.assertEqual((v['helpers']['enabled'], v['enabled']), (True, True), 'trial %d: %s' % (trial, outs))
            self.turn('helpers', False)
            self.turn('sonnet', False)
            self.assertEqual(self.files(), start, 'trial %d' % trial)
        self.assertGreater(together, 0, 'no trial pressed within 50 ms of the other: the check proves little')


class TheCopyBesideIsReplacedByAnotherWriter(Base):
    """The status line keeps its copy in the same file beside the settings file, and may save the file as it is now,
    with Switchboard's lines in it. Off must still take them out, both ways it can take."""

    def off_takes_the_lines_out(self, between):
        self.new_claude_code()
        before = self.read()
        self.turn('sonnet', True)
        with open(self.settings + '.switchboard-backup', 'wb') as f:
            f.write(self.read())
        for part, on in between:
            self.turn(part, on)
        self.assertEqual(self.turn('sonnet', False)['changed'], True)
        self.assertEqual(self.read(), before)
        self.assertFalse(server.models_view()['enabled'])

    def test_by_the_exact_undo(self):
        self.off_takes_the_lines_out(())

    def test_by_the_plan(self):
        self.off_takes_the_lines_out((('helpers', True),))


class ClaudeCodeIsUpdatedBetweenPresses(Base):
    """Sonnet for helpers on with one Claude Code, then on again, Models by the job on, and both off with another:
    every file comes back as it was."""
    OLD = [{'where': '/x/claude', 'version': '2.1.309', 'primary': True}]          # no effort line there

    def test_every_file_comes_back(self):
        record = models.default_state_path()
        for raw in (None, b'', b'\n', b'{\n  "env": {\n  },\n  "theme": "dark"\n}\n', self.ORIGINAL.encode()):
            for first, then in ((self.OLD, NEW), (NEW, self.OLD), ([], self.OLD), (NEW, [])):
                with self.subTest(raw=raw, first=first, then=then):
                    for p in (self.settings, self.settings + '.switchboard-backup', record):
                        if os.path.exists(p):
                            os.remove(p)
                    if raw is not None:
                        with open(self.settings, 'wb') as f:
                            f.write(raw)
                    start = self.files()
                    self.patch(server, 'MODELS_KW', dict(server.MODELS_KW, versions=first))
                    seen = [self.turn('sonnet', True)['changed']]
                    self.patch(server, 'MODELS_KW', dict(server.MODELS_KW, versions=then))
                    for part, on in (('sonnet', True), ('helpers', True), ('sonnet', False), ('helpers', False)):
                        seen.append(self.turn(part, on)['changed'])
                    self.assertEqual(seen, [True, False, True, True, True])
                    self.assertEqual(self.files(), start)
                    self.assertFalse(os.path.exists(self.agents) or os.path.exists(self.rules))
                    v = server.models_view()
                    self.assertEqual((v['enabled'], v['helpers']['enabled']), (False, False))


class TheDemo(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)

    def test_both_switches_start_off_and_the_page_turns_each_in_memory(self):
        v = server.models_view()
        self.assertEqual((v['enabled'], v['helpers']['enabled'], v['effort']), (False, False, 'high'))
        demo.act('/api/models', {'enabled': True, 'part': 'helpers'})
        v = server.models_view()
        self.assertEqual((v['enabled'], v['helpers']['enabled']), (False, True))
        demo.act('/api/models', {'enabled': True})
        self.assertEqual(server.models_view()['enabled'], True)


if __name__ == '__main__':
    unittest.main()
