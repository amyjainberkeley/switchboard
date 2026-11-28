import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The router wired into Switchboard (switchboard/router/wire.py): one look per pass of the loop, the person's one
# switch, the trial run, the lines the terminal list shows and the lines History takes. Above everything: while
# routing is off and was never on, nothing of the router is written anywhere.
import dataclasses
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))
sys.path.insert(0, HERE)

from switchboard.providers.contract import Found  # noqa: E402
from switchboard.providers.job import RouteChoice  # noqa: E402
from switchboard.providers.routes import Table  # noqa: E402
from switchboard.router import adapter_words as AW  # noqa: E402
from switchboard.router import cache_words as CW  # noqa: E402
from switchboard.router import codex_files, plan, plugin_setting, record, settings, wire  # noqa: E402
from switchboard.router import wire_words as W  # noqa: E402
from switchboard.router import words  # noqa: E402

import fakes_provider_kit as kit  # noqa: E402
from test_providers_words import problems  # noqa: E402


# These tests hold routing as it is once the hand-over and the one "continue" are in (router/ready.py): both then
# follow "on". tests/test_router_hand_ready.py holds the state this tree ships in.
_SHIPPED_FOLLOW_ON = settings.FOLLOW_ON


def setUpModule():
    settings.FOLLOW_ON = ('hand.on', 'carry_on')


def tearDownModule():
    settings.FOLLOW_ON = _SHIPPED_FOLLOW_ON

NOW = kit.NOW
NEW = [{'where': '/usr/local/bin/claude', 'version': '2.1.332', 'primary': True}]
KEY = 'CLAUDE_CODE_PLUGIN_DIRS'
TABLE = Table({'find': (RouteChoice('claude', 'haiku', 'medium'), RouteChoice('codex', 'gpt-6-luna', 'medium')),
               'review': (RouteChoice('codex', 'gpt-6.2-astra', 'high'), RouteChoice('claude', 'sonnet'))})


class _Routes:
    """The routes module as plan.gather() reaches it through the Kit."""

    def __init__(self, table=TABLE):
        self.table = table

    def load(self, catalog=None):
        return self.table
SETTINGS_TEXT = '{\n  "model": "sonnet",\n  "env": {\n    "SOME_VAR": "1"\n  }\n}\n'


def snapshot(*roots, times=True):
    """Every folder and file under the roots: kind, mode, size, change time and, for a file, a hash of its bytes.
    A root that is not there is noted as missing. times=False leaves the change times out (a file put back whole
    has the same bytes and a new time)."""
    out = {}
    for root in roots:
        if not os.path.lexists(root):
            out[root] = 'missing'
            continue
        for folder, dirs, names in os.walk(root):
            st = os.lstat(folder)
            out[folder] = ('dir', st.st_mode, st.st_mtime_ns if times else None)
            for name in names:
                p = os.path.join(folder, name)
                st = os.lstat(p)
                with open(p, 'rb') as f:
                    digest = hashlib.sha256(f.read()).hexdigest()
                out[p] = ('file', st.st_mode, st.st_size, st.st_mtime_ns if times else None, digest)
    return out


class Home:
    """A mixin: a fresh throwaway home (Claude Code's folder with a settings file, Codex's folder with its own
    config) and a fresh Switchboard data folder, named in SWITCHBOARD_HOME for the length of one test."""

    def setUp(self):
        super().setUp()
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix='sb-router-wire-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        self.claude = os.path.join(self.home, '.claude')
        self.codex = os.path.join(self.home, '.codex')
        os.makedirs(self.claude)
        os.makedirs(self.codex)
        with open(os.path.join(self.claude, 'settings.json'), 'w', encoding='utf-8') as f:
            f.write(SETTINGS_TEXT)
        with open(os.path.join(self.codex, 'config.toml'), 'w', encoding='utf-8') as f:
            f.write('model = "gpt-6-luna"\n')
        self.data = os.path.join(self.tmp, 'data')
        env = mock.patch.dict(os.environ, {'SWITCHBOARD_HOME': self.data, 'CLAUDE_CONFIG_DIR': self.claude,
                                           'CODEX_HOME': self.codex})
        env.start()
        self.addCleanup(env.stop)
        wire._SHOWN.clear()

    def settings_raw(self):
        with open(os.path.join(self.claude, 'settings.json'), 'rb') as f:
            return f.read()

    def kit(self, **kw):
        r = self.rig(**kw)
        return dataclasses.replace(r.kit, routes=_Routes())

    def on(self, k=None, **kw):
        args = dict(kit=k or self.kit(), claude_dir=self.claude, codex_home=self.codex, versions=NEW, now=NOW)
        args.update(kw)
        return wire.turn_on_steps(**args)

    def off(self, **kw):
        args = dict(claude_dir=self.claude, codex_home=self.codex, versions=NEW)
        args.update(kw)
        return wire.turn_off(**args)

    def outside_router(self):
        """The data folder's files outside router/, with their bytes."""
        snap = snapshot(self.data, times=False)
        router = os.path.join(self.data, 'router')
        return {p: v for p, v in snap.items() if p != router and not p.startswith(router + os.sep)
                and p != self.data}


class NeverTurnedOn(Home, kit.KitCase, unittest.TestCase):
    """Nothing of the router is written for a person who never turned it on."""

    def look_a_hundred_times(self):
        r = self.rig()
        for i in range(100):
            got = wire.pass_(NOW + i * 30, kit=r.kit)
            self.assertFalse(got['on'])
            wire.lines(NOW + i * 30)
            wire.status(NOW + i * 30)
            wire.history(NOW + i * 30, 0)
            self.assertEqual(wire.trial(True, kit=r.kit, now=NOW), [W.TRIAL_ROUTING_OFF])
            self.assertEqual(wire.trial(False, kit=r.kit, now=NOW), [W.TRIAL_ROUTING_OFF])
            self.assertEqual(wire.apply_for_real(kit=r.kit, codex_home=self.codex, now=NOW), [W.TRIAL_ROUTING_OFF])
            plugin_setting.status(claude_dir=self.claude, versions=NEW)
            plugin_setting.plan(claude_dir=self.claude, versions=NEW)

    def test_a_hundred_passes_with_no_data_folder_write_nothing(self):
        before = snapshot(self.home, self.data)
        self.assertEqual(before[self.data], 'missing')
        self.look_a_hundred_times()
        self.assertEqual(snapshot(self.home, self.data), before)

    def test_a_hundred_passes_beside_switchboards_own_files_write_nothing(self):
        os.makedirs(self.data)
        with open(os.path.join(self.data, 'settings.json'), 'w', encoding='utf-8') as f:
            f.write('{"auto": true}')
        before = snapshot(self.home, self.data)
        self.look_a_hundred_times()
        self.assertEqual(snapshot(self.home, self.data), before)
        self.assertFalse(os.path.exists(os.path.join(self.data, 'router')))

    def test_a_router_folder_another_switch_made_gets_nothing_more(self):
        # The helpers' cache switch keeps its record in the router folder; routing itself was never on.
        os.makedirs(os.path.join(self.data, 'router'))
        with open(os.path.join(self.data, 'router', 'cache-setting.json'), 'w') as f:
            f.write('{"version": 1, "dirs": {}}\n')
        before = snapshot(self.home, self.data)
        self.look_a_hundred_times()
        self.assertEqual(snapshot(self.home, self.data), before)
        self.assertEqual(wire.lines(NOW), [W.OFF])

    def test_a_plan_left_behind_is_taken_away_and_nothing_else_written(self):
        os.makedirs(os.path.join(self.data, 'router'))
        with open(plan.path(), 'w') as f:
            f.write('{"v": 1}')
        before = snapshot(self.home, self.data)
        del before[plan.path()]
        before.pop(os.path.join(self.data, 'router'))
        self.look_a_hundred_times()
        after = snapshot(self.home, self.data)
        after.pop(os.path.join(self.data, 'router'))      # its time changes as the plan goes
        self.assertEqual(after, before)
        self.assertFalse(os.path.exists(plan.path()))

    def test_routing_off_is_one_line(self):
        self.assertEqual(wire.lines(NOW), ['Routing is off.'])



class OnAndOff(Home, kit.KitCase, unittest.TestCase):
    def test_on_then_off_leaves_every_file_as_it_was(self):
        os.makedirs(self.data)
        with open(os.path.join(self.data, 'settings.json'), 'w', encoding='utf-8') as f:
            f.write('{"auto": true}')
        before = snapshot(self.home, times=False)
        data_before = self.outside_router()
        got = self.on()
        self.assertEqual(got[0], W.TURNED_ON)
        self.assertEqual(got[1], W.PLAN_WRITTEN)
        self.assertEqual(got[2], W.DID_ADD + ' ' + W.NEW_SESSIONS)
        self.assertEqual(got[3], AW.HOOKS_ON.format(path=os.path.join(self.codex, 'hooks.json')) + ' ' + AW.TRUST)
        # One helper file: handing work on is on with routing, so a lookup typed in Codex goes to Claude (first on
        # its route) and only reviews stay in Codex.
        self.assertEqual(got[4], W.CODEX_FILES_WROTE[1])
        self.assertEqual(json.loads(self.settings_raw())['env'][KEY], plugin_setting.plugin_dir())
        self.assertTrue(os.path.exists(os.path.join(self.codex, 'hooks.json')))
        p = plan.read()
        self.assertEqual((p['on'], p['dry']), (True, False))
        got = self.off()
        self.assertEqual(got[:3], [W.TURNED_OFF, W.PLAN_REMOVED, W.DID_REMOVE])
        self.assertEqual(got[3], AW.HOOKS_OFF.format(path=os.path.join(self.codex, 'hooks.json')))
        self.assertEqual(got[4], W.CODEX_FILES_REMOVED[1])
        self.assertEqual(snapshot(self.home, times=False), before)
        self.assertEqual(self.outside_router(), data_before)
        self.assertIsNone(plan.read())
        self.assertEqual(wire.lines(NOW), [W.OFF])
        # What stays is Switchboard's own record in its router folder: the switch, saved off, and the records of
        # the two undos, which name nothing that is still written.
        self.assertEqual(settings.read()['on'], False)
        self.assertFalse(os.path.exists(os.path.join(self.data, 'router', 'plugin-backups')) and
                         os.listdir(os.path.join(self.data, 'router', 'plugin-backups')))

    def test_on_then_apply_for_real_then_off_leaves_every_file_as_it_was(self):
        before = snapshot(self.home, times=False)
        k = self.kit()
        self.on(k)
        got = wire.apply_for_real(kit=k, codex_home=self.codex, now=NOW)
        self.assertEqual(got, [W.TRIAL_OFF, W.PLAN_WRITTEN, W.CODEX_FILES_SAME])
        self.assertFalse(os.path.exists(os.path.join(self.codex, 'agents', 'switchboard-find.toml')))
        self.assertTrue(os.path.exists(os.path.join(self.codex, 'agents', 'switchboard-review.toml')))
        self.assertFalse(plan.read()['dry'])
        got = self.off()
        self.assertIn(W.CODEX_FILES_REMOVED[1], got)
        self.assertEqual(snapshot(self.home, times=False), before)

    def test_on_twice_changes_nothing_the_second_time(self):
        k = self.kit()
        self.on(k)
        before = snapshot(self.home)
        got = self.on(k)
        self.assertEqual(got[0], W.TURNED_ON)
        self.assertIn(W.ALREADY, got)
        self.assertIn(AW.HOOKS_ALREADY.format(path=os.path.join(self.codex, 'hooks.json')), got)
        self.assertEqual(snapshot(self.home), before)

    def test_off_twice_changes_nothing_the_second_time(self):
        self.on()
        self.off()
        before = snapshot(self.home)
        router_before = snapshot(os.path.join(self.data, 'router'), times=False)
        got = self.off()
        self.assertEqual(got[:3], [W.ALREADY_OFF, W.PLAN_NONE, W.NOTHING_OURS])
        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(snapshot(os.path.join(self.data, 'router'), times=False), router_before)

    def test_off_after_a_half_way_off_says_routing_is_off_not_that_nothing_changed(self):
        self.on()
        settings.set_switch('on', False)        # the switch saved off, the rest still there
        got = self.off()
        self.assertEqual(got[:3], [W.TURNED_OFF, W.PLAN_REMOVED, W.DID_REMOVE])
        self.assertNotIn(W.ALREADY_OFF, got)

    def test_off_when_never_on_writes_nothing(self):
        before = snapshot(self.home, self.data)
        self.assertEqual(self.off(), [W.ALREADY_OFF])
        self.assertEqual(snapshot(self.home, self.data), before)

    def test_on_again_after_off_keeps_the_trial_as_the_person_left_it(self):
        k = self.kit()
        self.on(k)
        wire.trial(True, kit=k, now=NOW)
        self.off()
        got = self.on(k)
        self.assertEqual(got[0], W.ON_TRIAL)
        self.assertTrue(plan.read()['dry'])
        wire.apply_for_real(kit=k, codex_home=self.codex, now=NOW)
        self.off()
        got = self.on(k)
        self.assertEqual(got[0], W.TURNED_ON)
        self.assertFalse(plan.read()['dry'])
        self.off()

    def test_the_first_turn_on_is_for_real_not_a_trial(self):
        settings.set_default('defaults.workflow.effort', 'high', 'claude')    # a switches file before the first on
        got = self.on()
        self.assertEqual(got[0], W.TURNED_ON)
        self.assertIs(settings.read()['dry'], False)
        self.assertIs(plan.read()['dry'], False)
        self.assertFalse(hasattr(W, 'TURNED_ON_TRIAL'))
        self.assertFalse(os.path.exists(os.path.join(self.data, 'router', 'was-on')))

    def test_a_settings_file_that_is_not_json(self):
        with open(os.path.join(self.claude, 'settings.json'), 'w') as f:
            f.write('{"model": ')
        got = self.on()
        self.assertEqual(got[1], W.PLAN_WRITTEN)
        self.assertEqual(got[2], W.BROKEN)
        self.assertTrue(os.path.exists(os.path.join(self.codex, 'hooks.json')))     # the other steps stand
        self.assertEqual(self.settings_raw(), b'{"model": ')
        got = self.off()
        self.assertIn(W.NOTHING_OURS, got)
        self.assertEqual(self.settings_raw(), b'{"model": ')

    def test_an_off_that_cannot_edit_the_file_says_the_folder_is_still_named(self):
        self.on()
        with open(os.path.join(self.claude, 'settings.json'), 'w') as f:
            f.write('{"env": {"%s": "%s"}, ' % (KEY, plugin_setting.plugin_dir()))
        got = self.off()
        self.assertEqual(got[2], W.BROKEN + ' ' + W.STILL_THERE)
        self.assertNotIn(W.NOTHING_OURS, got)

    def test_the_persons_own_plugin_folders(self):
        text = '{"env": {"%s": "/opt/their-mods"}}\n' % KEY
        with open(os.path.join(self.claude, 'settings.json'), 'w') as f:
            f.write(text)
        got = self.on()
        self.assertEqual(got[2], W.DID_APPEND + ' ' + W.NEW_SESSIONS)
        self.assertEqual(json.loads(self.settings_raw())['env'][KEY],
                         '/opt/their-mods:' + plugin_setting.plugin_dir())
        got = self.off()
        self.assertIn(W.DID_TAKE_OUT, got)
        self.assertEqual(self.settings_raw(), text.encode('utf-8'))

    def test_the_line_after_on_keeps_what_is_not_known(self):
        both = NEW + [{'where': '/opt/old/claude', 'version': '2.1.100', 'primary': False}]
        with mock.patch.dict(os.environ, {KEY: '/opt/their-own'}):
            got = self.on(versions=both)
        self.assertEqual(got[2], ' '.join([W.DID_ADD, W.VERSION_OTHER_OLD.format(version='2.1.100'), W.ENV_TOO,
                                           W.NEW_SESSIONS]))

    def test_an_old_claude_code_gets_nothing_and_the_rest_stands(self):
        got = self.on(versions=[{'where': 'c', 'version': '2.1.200', 'primary': True}])
        self.assertEqual(got[2], W.VERSION_OLD_NO_WRITE.format(version='2.1.200', need='2.1.287'))
        self.assertEqual(self.settings_raw(), SETTINGS_TEXT.encode('utf-8'))
        self.assertTrue(os.path.exists(os.path.join(self.codex, 'hooks.json')))

    def test_a_plan_that_cannot_be_made_is_said_and_the_rest_stands(self):
        with mock.patch.object(plan, 'gather', side_effect=RuntimeError('no')):
            got = self.on()
        self.assertEqual(got[1], W.PLAN_FAILED.format(why='no'))
        self.assertEqual(got[2], W.DID_ADD + ' ' + W.NEW_SESSIONS)
        self.assertEqual(got[3], W.CODEX_UNKNOWN)
        self.assertFalse(os.path.exists(os.path.join(self.codex, 'hooks.json')))

    def test_off_that_cannot_save_the_switch_takes_nothing_back(self):
        self.on()
        before = snapshot(self.home, times=False)
        with mock.patch.object(settings, 'set_switch', side_effect=OSError('No space left on device')):
            got = self.off()
        self.assertEqual(got, [W.OFF_NOT_SAVED.format(why='No space left on device')])
        self.assertIs(settings.read()['on'], True)
        self.assertIsNotNone(plan.read())
        self.assertEqual(snapshot(self.home, times=False), before)
        self.off()

    def test_off_with_a_switches_file_gone_bad_while_saving_counts_as_off(self):
        self.on()
        with mock.patch.object(settings, 'set_switch', side_effect=settings.SettingsError('unreadable')):
            got = self.off()
        self.assertEqual(got[:3], [W.OFF_SWITCH_FAILED.format(why='unreadable'), W.PLAN_REMOVED, W.DID_REMOVE])

    def test_a_switches_file_that_cannot_be_read_stops_turn_on(self):
        os.makedirs(os.path.join(self.data, 'router'))
        with open(settings.path(), 'w') as f:
            f.write('nope')
        got = self.on()
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith(W.SWITCH_FAILED.format(why='')[:30]))
        self.assertEqual(self.settings_raw(), SETTINGS_TEXT.encode('utf-8'))


class Cache(Home, kit.KitCase, unittest.TestCase):
    """The helpers' cache lifetime: set by turn_on only when the person's own count says so, taken back by
    turn_off only when turn_on set it."""

    def test_no_count_leaves_the_key_alone(self):
        for cache in (None, lambda: None):
            got = self.on(cache=cache)
            self.assertFalse([x for x in got if 'subagentPromptCacheTtl' in x])
            self.assertNotIn('subagentPromptCacheTtl', json.loads(self.settings_raw()))
            self.off()

    def test_a_count_that_calls_for_an_hour_sets_it_in_one_line_and_off_takes_it_back(self):
        got = self.on(cache=lambda: '1h')
        self.assertEqual(got[-1], CW.DID_ADD.format(value='1h'))
        self.assertEqual(json.loads(self.settings_raw())['subagentPromptCacheTtl'], '1h')
        got = self.off()
        self.assertIn(CW.DID_REMOVE, got)
        self.assertEqual(self.settings_raw(), SETTINGS_TEXT.encode('utf-8'))

    def test_off_leaves_a_cache_key_turn_on_did_not_set(self):
        from switchboard.router import cache_setting
        self.on()
        cache_setting.apply('1h', claude_dir=self.claude, versions=NEW)     # the cache's own switch
        got = self.off()
        self.assertNotIn(CW.DID_REMOVE, got)
        self.assertEqual(json.loads(self.settings_raw())['subagentPromptCacheTtl'], '1h')

    def test_a_count_that_fails_is_said_and_the_rest_stands(self):
        def boom():
            raise RuntimeError('no records')
        got = self.on(cache=boom)
        self.assertEqual(got[-1], W.STEP_FAILED.format(why='no records'))
        self.assertEqual(got[1], W.PLAN_WRITTEN)

    def test_turn_on_says_at_most_six_lines(self):
        os.makedirs(os.path.join(self.codex, 'agents'))
        with open(os.path.join(self.codex, 'agents', 'switchboard-find.toml'), 'w') as f:
            f.write('# the person\'s own\n')
        got = self.on(cache=lambda: '1h')
        self.assertLessEqual(len(got), 6)
        self.assertEqual(got[-1], CW.DID_ADD.format(value='1h'))
        self.assertTrue(all('\n' not in x for x in got))


class HalfWay(Home, kit.KitCase, unittest.TestCase):
    def test_off_after_an_on_whose_plugin_step_failed_leaves_every_file_as_it_was(self):
        before = snapshot(self.home, times=False)
        with mock.patch.object(plugin_setting, 'apply', side_effect=RuntimeError('stuck')):
            got = self.on()
        self.assertEqual(got[2], W.STEP_FAILED.format(why='stuck'))
        self.off()
        self.assertEqual(snapshot(self.home, times=False), before)

    def test_off_after_an_on_whose_codex_step_failed_leaves_every_file_as_it_was(self):
        before = snapshot(self.home, times=False)
        with mock.patch.object(wire.codex_files, 'apply', side_effect=RuntimeError('stuck')):
            got = self.on()
        self.assertIn(W.STEP_FAILED.format(why='stuck'), got)
        self.off()
        self.assertEqual(snapshot(self.home, times=False), before)


class BuiltLines(Home, kit.KitCase, unittest.TestCase):
    def test_every_line_the_switch_and_the_list_build_keeps_the_word_rules(self):
        from test_providers_words import problems
        k = self.kit()
        seen = []
        seen += self.on(k, cache=lambda: '1h')
        write_decision(NOW - 30, 'a1b2c3d4')
        write_decision(NOW - 20, 'a1b2c3d5', did='kept', where='workflow')
        record.fold(NOW)
        seen += wire.lines(NOW + 30, claude_dir=self.claude, versions=NEW)
        seen += [t for _, t in wire.history(NOW, None)]
        seen += wire.trial(True, kit=k, now=NOW, codex_home=self.codex)
        seen += wire.lines(NOW + 600, claude_dir=self.claude, versions=NEW)
        seen += wire.apply_for_real(kit=k, codex_home=self.codex, now=NOW)
        seen += self.off()
        seen += self.off()
        seen += wire.trial(True, kit=k, now=NOW)
        seen += wire.lines(NOW)
        self.assertGreater(len(seen), 15)
        for text in seen:
            with self.subTest(text=text):
                self.assertIsInstance(text, str)
                self.assertTrue(text)
                self.assertNotIn('\n', text)
                self.assertEqual(problems(text), [])
                self.assertNotRegex(text, r'(?i)\bbest\b|\bnever run\b|guarantee|\{|\}|%s')


class Codex(Home, kit.KitCase, unittest.TestCase):
    def test_not_added(self):
        before = snapshot(self.codex)
        got = self.on(self.kit(codex_added=False))
        self.assertEqual(got[3:], [W.CODEX_NOT_ADDED])
        self.assertEqual(snapshot(self.codex), before)
        self.assertIn(W.CODEX_NOTHING, self.off())

    def test_not_installed(self):
        before = snapshot(self.codex)
        got = self.on(self.kit(codex_found_=Found('codex', installed=False)))
        self.assertEqual(plan.read()['agents']['codex']['why_not'], 'not_installed')
        self.assertEqual(got[3:], [W.CODEX_NOT_INSTALLED])
        self.assertEqual(snapshot(self.codex), before)

    def test_its_folder_missing(self):
        shutil.rmtree(self.codex)
        got = self.on()
        self.assertEqual(got[3:], [W.CODEX_NO_FOLDER.format(path=self.codex)])
        self.assertFalse(os.path.exists(self.codex))
        self.off()
        self.assertFalse(os.path.exists(self.codex))


class Passes(Home, kit.KitCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        wire._GATHER['thread'] = None

    def test_a_pass_with_the_switch_on_writes_a_usable_plan_and_the_next_a_newer_one(self):
        k = self.kit()
        self.on(k)
        os.remove(plan.path())
        got = wire.pass_(NOW + 10, kit=k)
        self.assertEqual((got['on'], got['plan'], got['errors'], got['stopped_early']), (True, 'written', [], False))
        first = plan.read()
        self.assertEqual(first['made_at'], NOW + 10)
        from switchboard.router import resolve
        self.assertTrue(resolve.usable(first, NOW + 20))
        got = wire.pass_(NOW + 70, kit=k)
        self.assertEqual(plan.read()['made_at'], NOW + 70)

    def test_a_pass_with_the_switch_off_takes_the_plan_away(self):
        k = self.kit()
        self.on(k)
        settings.set_switch('on', False)
        got = wire.pass_(NOW + 10, kit=k)
        self.assertEqual((got['on'], got['plan']), (False, 'removed'))
        self.assertIsNone(plan.read())
        got = wire.pass_(NOW + 20, kit=k)
        self.assertEqual(got['plan'], None)

    def test_a_pass_writes_no_plan_when_routing_is_turned_off_while_it_makes_one(self):
        k = self.kit()
        self.on(k)
        real = plan.gather

        def gather_then_off(now, kit=None):
            got = real(now, kit)
            self.assertEqual(self.off()[:2], [W.TURNED_OFF, W.PLAN_REMOVED])    # the person, in a terminal
            return got
        with mock.patch.object(plan, 'gather', side_effect=gather_then_off):
            got = wire.pass_(NOW + 10, kit=k)
        self.assertEqual((got['on'], got['plan'], got['errors']), (False, None, []))
        self.assertIsNone(plan.read())

    def test_a_pass_keeps_codex_helper_files_in_step_with_the_routes(self):
        self.on()
        review = os.path.join(self.codex, 'agents', 'switchboard-review.toml')
        find = os.path.join(self.codex, 'agents', 'switchboard-find.toml')
        self.assertTrue(os.path.exists(review))
        r = self.rig()
        # Codex first for lookups: with handing on, a route led by Claude would send a lookup typed in Codex to Claude.
        k = dataclasses.replace(r.kit, routes=_Routes(Table({'find': tuple(reversed(TABLE.routes['find']))})))
        got = wire.pass_(NOW + 10, kit=k, codex_home=self.codex)
        self.assertEqual((got['plan'], got['codex_files'], got['errors']), ('written', 2, []))    # one out, one in
        self.assertFalse(os.path.exists(review))
        self.assertTrue(os.path.exists(find))
        state = codex_files.default_state_path()
        mtime = os.stat(state).st_mtime_ns
        got = wire.pass_(NOW + 20, kit=k, codex_home=self.codex)    # nothing to change: nothing is written
        self.assertEqual(got['codex_files'], 0)
        self.assertEqual(os.stat(state).st_mtime_ns, mtime)

    def test_a_pass_in_a_trial_leaves_codex_helper_files_alone(self):
        k = self.kit()
        self.on(k)
        wire.trial(True, kit=k, now=NOW, codex_home=self.codex)
        before = snapshot(self.codex)
        got = wire.pass_(NOW + 10, kit=k, codex_home=self.codex)
        self.assertEqual(got['codex_files'], 0)
        self.assertEqual(snapshot(self.codex), before)

    def test_a_pass_folds_decisions_and_reads_signals(self):
        k = self.kit()
        self.on(k)
        write_decision(NOW - 30, 'a1b2c3d4')
        write_signal(NOW - 20)
        got = wire.pass_(NOW, kit=k)
        self.assertEqual((got['folded'], got['fold_removed']), (1, 0))
        self.assertEqual(got['signals'], [{'t': NOW - 20, 'agent': 'claude', 'what': 'at_line', 'window': 'week',
                                           'pct': 91}])
        self.assertEqual(len(record.read()[0]), 1)

    def test_a_pass_with_routing_off_still_folds_what_is_left(self):
        k = self.kit()
        self.on(k)
        self.off()
        write_decision(NOW - 30, 'a1b2c3d4')
        got = wire.pass_(NOW, kit=k)
        self.assertEqual((got['on'], got['folded']), (False, 1))

    def test_a_pass_never_raises_when_every_inner_call_does(self):
        k = self.kit()
        self.on(k)
        boom = RuntimeError('boom')
        with mock.patch.object(plan, 'gather', side_effect=boom), \
                mock.patch.object(record, 'fold', side_effect=boom), \
                mock.patch.object(record, 'path', side_effect=boom), \
                mock.patch.object(record, 'signals', side_effect=boom):
            got = wire.pass_(NOW, kit=k)
        self.assertEqual(got['errors'], ['plan', 'fold', 'trim', 'signals'])
        self.assertEqual(got['plan'], 'failed')
        with mock.patch.object(plan, 'write', side_effect=OSError('disk')):
            self.assertEqual(wire.pass_(NOW, kit=k)['errors'], ['plan'])
        with mock.patch.object(settings, 'read', side_effect=boom):
            self.assertEqual(wire.pass_(NOW, kit=k)['errors'], ['pass'])
        with mock.patch.object(wire, 'made', side_effect=boom):
            self.assertEqual(wire.pass_(NOW, kit=k)['errors'], ['pass'])
        settings.set_switch('on', False)
        with mock.patch.object(wire, '_remove_plan', side_effect=boom), \
                mock.patch.object(record, 'fold', side_effect=boom):
            self.assertEqual(wire.pass_(NOW, kit=k)['errors'], ['plan', 'fold'])

    def test_a_pass_stops_within_two_seconds(self):
        k = self.kit()
        self.on(k)
        os.remove(plan.path())
        release = threading.Event()
        self.addCleanup(lambda: (release.set(), wire._GATHER['thread'] and wire._GATHER['thread'].join(10)))

        def slow(now, kit=None):
            release.wait(20)
            raise RuntimeError('released')
        with mock.patch.object(plan, 'gather', side_effect=slow):
            t0 = time.monotonic()
            got = wire.pass_(NOW, kit=k)
            took = time.monotonic() - t0
            self.assertLess(took, wire.BOUND_S + 1.0)
            self.assertEqual((got['plan'], got['stopped_early']), ('late', True))
            self.assertIsNone(plan.read())
            t0 = time.monotonic()
            got = wire.pass_(NOW + 30, kit=k)           # the first is still running: not started again
            self.assertLess(time.monotonic() - t0, 1.0)
            self.assertEqual(got['plan'], 'late')

    def test_a_slow_step_leaves_the_rest_for_the_next_pass(self):
        k = self.kit()
        self.on(k)

        def slow_fold(now=None):
            time.sleep(wire.BOUND_S + 0.2)
            return 0, 0
        with mock.patch.object(record, 'fold', side_effect=slow_fold), \
                mock.patch.object(record, 'signals') as sig:
            got = wire.pass_(NOW, kit=k)
        self.assertTrue(got['stopped_early'])
        sig.assert_not_called()


class TurnOnInThreeLines(Home, kit.KitCase, unittest.TestCase):
    """turn_on: the bare list's own sentence, then each change with its undo; never an app that is not here."""

    def brief(self, k=None, **kw):
        args = dict(kit=k or self.kit(), claude_dir=self.claude, codex_home=self.codex, versions=NEW, now=NOW)
        args.update(kw)
        return wire.turn_on(**args)

    def test_no_codex_here_names_no_codex(self):
        got = self.brief(self.kit(codex_added=False))
        self.assertEqual(got[0], wire.lines(NOW, claude_dir=self.claude, versions=NEW)[0])
        self.assertTrue(got[1].startswith(W.BRIEF_PLUGIN), got)
        self.assertLessEqual(len(got), wire.TURN_ON_BRIEF_MAX)
        self.assertNotIn('Codex', ' '.join(got))
        got = self.brief(self.kit(codex_found_=Found('codex', installed=False)))
        self.assertNotIn('Codex', ' '.join(got))

    def test_codex_on_the_list_is_named_with_its_undo(self):
        got = self.brief()
        self.assertLessEqual(len(got), wire.TURN_ON_BRIEF_MAX)
        said = ' '.join(got)
        self.assertIn("Codex now has Switchboard's two hooks", said)
        self.assertIn('(undo: switchboard routing off)', said)

    def test_a_second_time_says_only_the_sentence(self):
        self.brief()
        got = self.brief()
        self.assertEqual(got, [wire.lines(NOW, claude_dir=self.claude, versions=NEW)[0]])

    def test_no_route_says_the_bare_list_s_sentence(self):
        k = dataclasses.replace(self.kit(codex_added=False), routes=_Routes(Table({})))
        got = self.brief(k)
        self.assertEqual(got[0], W.ON_NOTHING_SET)
        self.assertEqual(len(got), 2)

    def test_at_most_three_lines_with_everything_and_a_failure(self):
        with mock.patch.object(codex_files, 'apply', mock.Mock(side_effect=RuntimeError('made up'))):
            got = self.brief(cache=lambda: '1h')
        self.assertLessEqual(len(got), wire.TURN_ON_BRIEF_MAX)
        for x in got:
            self.assertEqual(problems(x), [], x)


class Lines(Home, kit.KitCase, unittest.TestCase):
    def test_on_with_nothing_set_is_one_line(self):
        # No route and no default: nothing can change, so the list says that in one line, not four.
        k = dataclasses.replace(self.kit(), routes=_Routes(Table({})))
        self.on(k)
        got = wire.lines(NOW + 40, claude_dir=self.claude, versions=NEW)
        self.assertEqual(got, [W.ON_NOTHING_SET])
        self.assertTrue(wire.status(NOW + 40, claude_dir=self.claude, versions=NEW)['nothing_set'])

    def test_a_default_alone_is_something_set(self):
        k = dataclasses.replace(self.kit(), routes=_Routes(Table({})))
        self.on(k)
        settings.set_default('defaults.workflow.effort', 'xhigh', 'claude')
        wire.pass_(NOW + 5, kit=k)
        got = wire.lines(NOW + 40, claude_dir=self.claude, versions=NEW)
        self.assertEqual(got[0], W.ON)
        self.assertGreater(len(got), 1)

    def test_on_is_at_most_four_lines(self):
        self.on()
        got = wire.lines(NOW + 40, claude_dir=self.claude, versions=NEW)
        self.assertEqual(got, [W.ON, W.PLAN_AGE.format(age='40 seconds'), W.LAST_NONE,
                               "Helpers' cache: not set, so Claude Code uses its own default."])

    def test_an_old_plan_and_the_last_decision(self):
        k = self.kit()
        self.on(k)
        wire.apply_for_real(kit=k, codex_home=self.codex, now=NOW)
        write_decision(NOW - 30, 'a1b2c3d4')
        record.fold(NOW)
        got = wire.lines(NOW + 600, claude_dir=self.claude, versions=NEW)
        self.assertEqual(got[0], W.ON)
        self.assertEqual(got[1], W.PLAN_OLD.format(age='10 minutes'))
        self.assertEqual(got[2], 'Seen in the routing record: 1 helper, on Haiku (1).')
        self.assertEqual(got[3], W.LAST.format(line='A lookup helper in Claude ran on Haiku at medium: your route '
                                                    'for lookups.'))
        self.assertLessEqual(len(got), 4)

    def test_on_without_the_plugin_key_says_so(self):
        self.on(versions=[{'where': 'c', 'version': '2.1.200', 'primary': True}])
        got = wire.lines(NOW, claude_dir=self.claude, versions=NEW)
        self.assertEqual(got[0], W.ON_NO_PLUGIN)

    def test_no_plan_yet(self):
        self.on()
        os.remove(plan.path())
        self.assertEqual(wire.lines(NOW, claude_dir=self.claude, versions=NEW)[1], W.PLAN_MISSING)

    def test_status_is_data(self):
        self.on()
        st = wire.status(NOW + 5, claude_dir=self.claude, versions=NEW)
        self.assertEqual((st['on'], st['dry'], st['plan_age_s'], st['plan_usable'], st['plugin']),
                         (True, False, 5.0, True, {'state': 'ours', 'loaded': True}))

    def test_what_was_seen_is_counted_from_the_record(self):
        k = self.kit()
        self.on(k)
        write_decision(NOW - 40, 'a1b2c3d4')
        write_decision(NOW - 30, 'a1b2c3d5')
        write_decision(NOW - 20, 'a1b2c3d6', did='kept', where='workflow')
        write_decision(NOW - 10, 'a1b2c3d7', where='chat')
        record.fold(NOW)
        got = wire.lines(NOW, claude_dir=self.claude, versions=NEW)
        self.assertEqual(got[2], 'Seen in the routing record: 2 helpers and 1 workflow agent, on Haiku (2), Opus (1).')
        self.assertTrue(got[3].startswith('Last: '))
        self.assertEqual(len(got), 4)
        st = wire.status(NOW, claude_dir=self.claude, versions=NEW)
        self.assertEqual(st['seen'], {'helper': 2, 'workflow': 1, 'models': [('Haiku', 2), ('Opus', 1)]})

    def test_ages(self):
        for s, want in ((0, '0 seconds'), (1, '1 second'), (59, '59 seconds'), (60, '1 minute'), (3599, '59 minutes'),
                        (3600, '1 hour'), (7200, '2 hours'), (86400, '1 day'), (3 * 86400, '3 days')):
            self.assertEqual(wire._age(s), want)


class History(Home, kit.KitCase, unittest.TestCase):
    def test_new_decisions_once_each(self):
        self.on()
        write_decision(NOW - 30, 'a1b2c3d4')
        record.fold(NOW)
        got = wire.history(NOW, NOW - 3600)
        self.assertEqual(got, [(NOW - 30, 'A lookup helper in Claude ran on Haiku at medium: your route for '
                                          'lookups.')])
        self.assertEqual(wire.history(NOW, NOW - 30), [])
        write_decision(NOW - 31, 'a1b2c3d5')            # folded late, with a time before the last one shown
        record.fold(NOW)
        self.assertEqual(len(wire.history(NOW, NOW - 30)), 1)

    def test_a_decision_whose_sentence_fails_is_given_at_the_next_call(self):
        self.on()
        write_decision(NOW - 30, 'a1b2c3d4')
        record.fold(NOW)
        with mock.patch.object(record, 'lines', side_effect=RuntimeError('odd')):
            self.assertEqual(wire.history(NOW, None), [])
        self.assertEqual(len(wire.history(NOW, None)), 1)

    def test_one_odd_record_does_not_break_the_list(self):
        self.on()
        write_decision(NOW - 30, 'a1b2c3d4')
        record.fold(NOW)
        with mock.patch.object(record, 'line', side_effect=RuntimeError('odd')):
            got = wire.lines(NOW, claude_dir=self.claude, versions=NEW)
        self.assertEqual(got[0], W.ON)
        self.assertLessEqual(len(got), 4)

    def test_at_most_twenty_and_the_rest_counted(self):
        self.on()
        for i in range(25):
            write_decision(NOW - 100 + i, '%08x' % i)
        record.fold(NOW)
        got = wire.history(NOW, None)
        self.assertEqual(len(got), 21)
        self.assertEqual(got[0], (NOW - 96, W.MORE_N.format(n=5)))
        self.assertEqual(got[1][0], NOW - 95)
        self.assertEqual(wire.history(NOW, None), [])

    def test_the_history_lines_are_the_records_own(self):
        self.on()
        write_decision(NOW - 30, 'a1b2c3d4', did='would')
        record.fold(NOW)
        got = wire.history(NOW, None)
        self.assertTrue(got[0][1].startswith('Trial run, nothing changed: '))
        self.assertIn(got[0][1], [t for _, t in record.lines(record.read()[0])])


class Trial(Home, kit.KitCase, unittest.TestCase):
    def test_the_trial_reaches_the_plan_at_once(self):
        k = self.kit()
        self.on(k)
        self.assertEqual(wire.trial(False, kit=k, now=NOW, codex_home=self.codex),
                         [W.TRIAL_OFF, W.PLAN_WRITTEN, W.CODEX_FILES_SAME])
        self.assertFalse(plan.read()['dry'])
        self.assertEqual(wire.trial(True, kit=k, now=NOW, codex_home=self.codex),
                         [W.TRIAL_ON, W.PLAN_WRITTEN, W.CODEX_FILES_REMOVED[1]])     # review only: handing is on
        self.assertTrue(plan.read()['dry'])

    def test_a_trial_takes_codex_helper_files_out_and_for_real_puts_them_back(self):
        k = self.kit()
        self.on(k)
        find = os.path.join(self.codex, 'agents', 'switchboard-review.toml')     # the one helper file with handing on
        self.assertTrue(os.path.exists(find))
        wire.trial(True, kit=k, now=NOW, codex_home=self.codex)
        self.assertFalse(os.path.exists(find))
        self.assertTrue(os.path.exists(os.path.join(self.codex, 'hooks.json')))      # the hooks stay
        got = wire.apply_for_real(kit=k, codex_home=self.codex, now=NOW)
        self.assertEqual(got, [W.TRIAL_OFF, W.PLAN_WRITTEN, W.CODEX_FILES_WROTE[1]])
        self.assertTrue(os.path.exists(find))

    def test_a_trial_with_routing_off_changes_nothing_and_says_so(self):
        k = self.kit()
        self.on(k)
        self.off()
        before = snapshot(self.home, self.data)
        self.assertEqual(wire.trial(True, kit=k, now=NOW), [W.TRIAL_ROUTING_OFF])
        self.assertEqual(wire.trial(False, kit=k, now=NOW), [W.TRIAL_ROUTING_OFF])
        self.assertEqual(wire.apply_for_real(kit=k, codex_home=self.codex, now=NOW), [W.TRIAL_ROUTING_OFF])
        self.assertEqual(snapshot(self.home, self.data), before)
        self.assertIsNone(plan.read())


class Words(unittest.TestCase):
    def test_the_off_line_and_no_sentence_says_more_than_was_counted(self):
        self.assertEqual(W.OFF, 'Routing is off.')
        from test_providers_words import problems
        for name in dir(W):
            v = getattr(W, name)
            if name.isupper() and isinstance(v, str):
                self.assertEqual(problems(v), [], name)
                self.assertNotRegex(v, r'(?i)\bbest\b|\bnever run\b|guarantee')


def write_decision(t, hex8, did='model', where='helper'):
    """One decision file as the Claude Code plugin writes it, old enough to be folded."""
    folder = os.path.join(os.environ['SWITCHBOARD_HOME'], 'router', 'decisions')
    os.makedirs(folder, exist_ok=True)
    rec = {'t': t, 'agent': 'claude', 'where': where, 'type': 'Explore', 'kind': 'find', 'did': did,
           'from_model': 'opus', 'model': 'haiku', 'effort': 'medium', 'to': None, 'reason': 'route',
           'why': words.ROUTE_WHY['find'], 'passed': [], 'plan': NOW - 60}
    path = os.path.join(folder, '%013d-%s.json' % (int(t * 1000), hex8))
    with open(path, 'w') as f:
        json.dump(rec, f)
    os.utime(path, (t, t))


def write_signal(t):
    folder = os.path.join(os.environ['SWITCHBOARD_HOME'], 'router', 'signals')
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, '%013d-%s.json' % (int(t * 1000), 'abcdef01')), 'w') as f:
        json.dump({'t': t, 'agent': 'claude', 'what': 'at_line', 'window': 'week', 'pct': 91}, f)


if __name__ == '__main__':
    unittest.main()
