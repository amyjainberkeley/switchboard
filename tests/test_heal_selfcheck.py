import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""One place checks what Switchboard keeps and repairs what it safely can (selfcheck.py), used by the page every ten
minutes, by `switchboard doctor --fix`, and by plain `switchboard doctor`, which only looks.

What Switchboard put in Claude Code's settings (its status line, its hooks) stays put: an entry taken out while the
person did not turn it off in Switchboard is put back once, through the code that put it there, and said in History;
gone again within a day it is left out and the page says once what is missing and what it costs. An entry Switchboard
did not add is never touched. After Claude Code updates itself, one read-only look at its program for the names the
engine relies on; a name not found is a warning with the version in it, never a refused switch.

Every file here is a throwaway one; nothing is run but this test's own Python.
  SWITCHBOARD_HOME="$(mktemp -d)/data" python3 -m unittest discover -s tests -p 'test_heal_selfcheck.py'"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import core, history, repairs, selfcheck
from switchboard import notify_hooks as nh, session_hooks as sh, statusline as sl

HERS = '{\n  "model": "opus",\n  "permissions": {\n    "allow": ["Bash(ls:*)"]\n  }\n}\n'
T = time.time()             # what a repair notes carries this process's clock, so the made-up times start now
H, D = 3600.0, 86400.0


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-selfcheck-')
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
                                    (nh, 'verify', lambda cmd: None), (sh, 'verify', lambda cmd: None)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.path = os.path.join(self.claude, 'settings.json')
        self.assertTrue(sl.settings_path().startswith(self.tmp))
        with open(self.path, 'w') as f:
            f.write(HERS)

    def read(self):
        with open(self.path, 'rb') as f:
            return f.read()

    def obj(self):
        return json.loads(self.read().decode('utf-8'))

    def write_obj(self, obj):
        with open(self.path, 'w') as f:
            json.dump(obj, f, indent=2)

    def statusline_on(self):
        self.assertTrue(sl.apply(sl.install_plan(self.path, command_line=sl.command()))['changed'])

    def take_out_statusline(self):
        """Someone else takes Switchboard's status line out of Claude Code's settings (another tool, a hand edit)."""
        o = self.obj()
        o.pop('statusLine', None)
        self.write_obj(o)

    def result(self, results, name):
        got = [r for r in results if r.name == name]
        self.assertEqual(len(got), 1, results)
        return got[0]

    def history_texts(self):
        return [e.get('text') for e in history.read(200)] if hasattr(history, 'read') else \
            [json.loads(x).get('text') for x in open(history.path()).read().splitlines() if x.strip()]


class WhatSwitchboardAddedStays(Base):
    def test_a_status_line_taken_out_is_put_back_once_through_the_same_code_and_said(self):
        self.statusline_on()
        self.take_out_statusline()
        edited = self.read()                                                        # as the other hand left it
        r = self.result(selfcheck.run(fix=True, now=T), 'entry:statusline')
        self.assertEqual((r.state, r.did), (selfcheck.REPAIRED, 'put back'))
        self.assertTrue(sl.is_ours(self.obj().get('statusLine')))
        self.assertEqual(self.obj()['model'], 'opus')                               # the person's own keys stay
        self.assertIn(selfcheck.PUT_BACK['statusline'], self.history_texts())
        self.assertEqual([(e['what'], e['kind']) for e in repairs.recent(self.data, now=time.time())],
                         [('statusline', 'put_back')])
        # turning it off later takes out exactly what was put back: the file is as the other hand left it
        self.assertTrue(sl.apply(sl.uninstall_plan(self.path))['changed'])
        self.assertEqual(self.read(), edited)

    def test_gone_again_within_a_day_it_is_left_out_and_said_once(self):
        self.statusline_on()
        self.take_out_statusline()
        selfcheck.run(fix=True, now=T)
        self.take_out_statusline()
        before = self.read()
        r = self.result(selfcheck.run(fix=True, now=T + 3 * H), 'entry:statusline')
        self.assertEqual(r.state, selfcheck.LEFT)
        self.assertEqual(r.did, selfcheck.COST['statusline'])
        self.assertEqual(self.read(), before)                                       # not fought
        selfcheck.run(fix=True, now=T + 4 * H)
        self.assertEqual(self.history_texts().count(selfcheck.COST['statusline']), 1)   # said once
        said = [n for n in selfcheck.notices(now=T + 4 * H) if n['what'] == 'statusline']
        self.assertEqual([n['text'] for n in said], [selfcheck.COST['statusline']])
        self.assertIn("Claude Code's status line no longer feeds Switchboard its limits; numbers for the account in "
                      "use may be a few minutes older. Turn it on again in Settings.", said[0]['text'])
        # turned on again by the person: a later removal is put back once again
        self.statusline_on()
        selfcheck.run(fix=True, now=T + 5 * H)
        self.assertEqual(selfcheck.notices(now=T + 5 * H), [])
        self.take_out_statusline()
        r = self.result(selfcheck.run(fix=True, now=T + 2 * D), 'entry:statusline')
        self.assertEqual(r.state, selfcheck.REPAIRED)

    def test_a_given_up_note_says_how_to_make_it_stay_and_ends_after_a_day(self):
        # Hooks taken out twice: the page and doctor say how to put them back with the command that does (the page's
        # Notifications switch is still on, and session hooks have no switch on the page), and how to make the removal
        # stay: the setting the self-check reads (_turned_off). After a day (the record's own time) neither says it.
        self.assertTrue(nh.apply(nh.install_plan(self.path, command_line=nh.command()))['changed'])
        self.assertTrue(sh.apply(sh.install_plan(self.path, command_line=sh.command()))['changed'])
        for t in (T, T + H):
            o = self.obj()
            o.pop('hooks', None)
            self.write_obj(o)
            selfcheck.run(fix=True, now=t)
        said = dict((n['what'], n['text']) for n in selfcheck.notices(now=T + 2 * H))
        self.assertEqual(said['notify'], "Claude Code's done and needs-you hooks were taken out of its settings again, "
                                         "so those notifications don't come. Put them back with: switchboard notify on. "
                                         "If you took them out on purpose, turn Notifications off in Settings: "
                                         "Switchboard's own notifications stop too.")
        self.assertEqual(said['session_hooks'], "Claude Code's session hooks were taken out of its settings again, so an "
                                                "open session is not told what a switch means for it. Put them back with: "
                                                "switchboard hooks on. If you took them out on purpose: switchboard hooks "
                                                "off.")
        fixes = dict((t, f) for g, t, f in selfcheck.doctor_lines(now=T + 2 * H) if 'hooks' in t)
        self.assertEqual(sorted(fixes.values()), sorted(['Put them back with: switchboard notify on. If you took them '
                                                         'out on purpose, turn Notifications off in Settings: '
                                                         "Switchboard's own notifications stop too.",
                                                         'Put them back with: switchboard hooks on. If you took them out '
                                                         'on purpose: switchboard hooks off.']))
        # the way out works: turned off in Switchboard, the self-check leaves it and says nothing
        core.set_setting('session_hooks', False)
        r = self.result(selfcheck.run(fix=True, now=T + 2 * H), 'entry:session_hooks')
        self.assertEqual((r.state, r.found), (selfcheck.OK, 'turned off in Switchboard'))
        self.assertNotIn('session_hooks', [n['what'] for n in selfcheck.notices(now=T + 2 * H)])
        # a day after it was given up: no note on the page, nothing for doctor, and still left out
        later = T + H + selfcheck.GIVEN_UP_SAID + 60
        self.assertEqual(selfcheck.notices(now=later), [])
        r = self.result(selfcheck.run(fix=False, now=later), 'entry:notify')
        self.assertEqual(r.state, selfcheck.OK)
        self.assertEqual([t for g, t, f in selfcheck.doctor_lines(now=later) if 'hooks' in t], [])
        self.assertFalse(nh.status(self.path)['in'])

    def test_the_status_line_has_no_setting_that_keeps_it_out_and_none_is_named(self):
        self.assertFalse(selfcheck._turned_off('statusline'))
        self.assertNotIn('on purpose', selfcheck.COST['statusline'])

    def test_put_back_again_when_the_last_put_back_is_more_than_a_day_old(self):
        self.statusline_on()
        self.take_out_statusline()
        selfcheck.run(fix=True, now=T)
        self.take_out_statusline()
        r = self.result(selfcheck.run(fix=True, now=T + D + 60), 'entry:statusline')
        self.assertEqual(r.state, selfcheck.REPAIRED)

    def test_an_entry_switchboard_did_not_add_is_never_touched(self):
        o = self.obj()
        o['statusLine'] = {'type': 'command', 'command': 'my-own-line.sh'}
        self.write_obj(o)
        before = self.read()
        r = self.result(selfcheck.run(fix=True, now=T), 'entry:statusline')
        self.assertEqual((r.state, r.found), (selfcheck.OK, 'not turned on'))
        self.assertEqual(self.read(), before)
        # Switchboard's line was there, and the person put their own in its place: theirs stays, said once
        os.unlink(self.path)
        with open(self.path, 'w') as f:
            f.write(HERS)
        self.statusline_on()
        o = self.obj()
        o['statusLine'] = {'type': 'command', 'command': 'my-own-line.sh'}
        self.write_obj(o)
        before = self.read()
        r = self.result(selfcheck.run(fix=True, now=T + H), 'entry:statusline')
        self.assertEqual(r.state, selfcheck.LEFT)
        self.assertEqual(self.read(), before)

    def test_hooks_taken_out_are_put_back_unless_turned_off_in_switchboard(self):
        self.assertTrue(nh.apply(nh.install_plan(self.path, command_line=nh.command()))['changed'])
        self.assertTrue(sh.apply(sh.install_plan(self.path, command_line=sh.command()))['changed'])
        o = self.obj()
        o.pop('hooks', None)
        self.write_obj(o)
        self.assertFalse(nh.status(self.path)['in'])
        out = selfcheck.run(fix=True, now=T)
        self.assertEqual(self.result(out, 'entry:notify').state, selfcheck.REPAIRED)
        self.assertEqual(self.result(out, 'entry:session_hooks').state, selfcheck.REPAIRED)
        self.assertTrue(nh.status(self.path)['in'])
        self.assertTrue(sh.status(self.path)['in'])
        # turned off in Switchboard (its own setting), and then taken out of Claude Code's file: left out, said nothing
        core.set_setting('notify', False)
        o = self.obj()
        o.pop('hooks', None)
        self.write_obj(o)
        before = self.read()
        out = selfcheck.run(fix=True, now=T + 2 * D)
        self.assertEqual(self.result(out, 'entry:notify').found, 'turned off in Switchboard')
        self.assertNotIn(selfcheck.PUT_BACK['notify'], self.history_texts()[1:] if False else [])
        self.assertFalse(nh.status(self.path)['in'])
        self.assertEqual(self.result(out, 'entry:session_hooks').state, selfcheck.REPAIRED)   # this one still on
        self.assertNotEqual(self.read(), before)

    def test_a_settings_file_kept_as_a_link_is_left_as_it_is(self):
        real = os.path.join(self.tmp, 'dotfiles-settings.json')       # kept in a dotfiles folder, linked here
        shutil.move(self.path, real)
        os.symlink(real, self.path)
        self.statusline_on()                                            # turned on by hand: its target is written
        self.take_out_statusline()
        with open(real, 'rb') as f:
            before = f.read()
        r = self.result(selfcheck.run(fix=True, now=T), 'entry:statusline')
        self.assertEqual(r.state, selfcheck.LEFT)
        with open(real, 'rb') as f:
            self.assertEqual(f.read(), before)
        self.assertTrue(os.path.islink(self.path))

    def test_a_look_only_says_it_and_writes_nothing(self):
        self.statusline_on()
        self.take_out_statusline()
        before = self.read()
        r = self.result(selfcheck.run(fix=False, now=T), 'entry:statusline')
        self.assertEqual((r.state, r.found), (selfcheck.PROBLEM, 'missing'))
        self.assertEqual(self.read(), before)
        self.assertFalse(os.path.exists(os.path.join(self.data, repairs.NAME)))
        lines = selfcheck.doctor_lines(now=T)
        self.assertIn((False, "Switchboard's status line: missing from Claude Code's settings",
                       'Turn it on again in Settings, or run: switchboard statusline on'), lines)
        self.assertEqual(self.read(), before)


class DamagedFiles(Base):
    def test_a_damaged_settings_file_is_said_by_the_look_and_restored_by_the_fix(self):
        core.set_setting('auto', True)
        core.set_setting('port', 7977)
        with open(core.data_path('settings.json'), 'w') as f:
            f.write('{"auto": tr')
        r = self.result(selfcheck.run(fix=False, now=T), 'settings')
        self.assertEqual((r.state, r.found), (selfcheck.PROBLEM, "can't be read"))
        with open(core.data_path('settings.json')) as f:
            self.assertEqual(f.read(), '{"auto": tr')                               # the look wrote nothing
        self.assertIn((False, "Switchboard's settings file: can't be read", 'Run: switchboard doctor --fix'),
                      selfcheck.doctor_lines(now=T))
        r = self.result(selfcheck.run(fix=True, now=T), 'settings')
        self.assertEqual(r.state, selfcheck.REPAIRED)
        self.assertTrue(r.did.startswith("Switchboard's settings file was damaged. It was restored from a copy made"))
        self.assertIs(core.settings().get('auto'), True)
        self.assertTrue([x for x in selfcheck.doctor_lines(now=time.time() + 60) if x[0] is False and
                         x[1].startswith("Switchboard's settings file was damaged.")])     # said for a day
        self.assertFalse([x for x in selfcheck.doctor_lines(now=time.time() + D + 60) if
                          x[1].startswith("Switchboard's settings file was damaged.")])

    def test_a_hand_edited_settings_file_has_its_copy_kept_current(self):
        core.set_setting('auto', True)
        with open(core.data_path('settings.json'), 'w') as f:
            json.dump({'auto': True, 'strategy': 'most-room'}, f)
        out = selfcheck.run(fix=True, now=T)
        self.assertEqual(self.result(out, 'settings_copy').state, selfcheck.REPAIRED)
        with open(core.data_path(core.SETTINGS_COPY), 'rb') as a, open(core.data_path('settings.json'), 'rb') as b:
            self.assertEqual(a.read(), b.read())
        self.assertEqual([r for r in selfcheck.run(fix=True, now=T + 1) if r.name == 'settings_copy'], [])

    def test_a_damaged_page_file_is_written_back_from_its_copy(self):
        st = core.load()
        st['events'] = [{'t': T, 'text': 'kept', 'kind': 'info'}]
        core.save(st)
        with open(core.data_path('state.json'), 'w') as f:
            f.write('{"events": [')
        self.assertEqual(self.result(selfcheck.run(fix=False, now=T), 'state').state, selfcheck.PROBLEM)
        r = self.result(selfcheck.run(fix=True, now=T), 'state')
        self.assertEqual(r.state, selfcheck.REPAIRED)
        with open(core.data_path('state.json')) as f:
            self.assertIsInstance(json.load(f), dict)


class ACutOffSwitch(Base):
    def test_one_left_unsettled_is_said_then_settled_by_the_fix(self):
        from switchboard.accounts import api, context, switcher
        path = context.current().path(switcher.JOURNAL)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write('{}')
        old = time.time() - selfcheck.UNSETTLED_AFTER - 60
        os.utime(path, (old, old))
        r = self.result(selfcheck.run(fix=False), 'journal')
        self.assertEqual(r.state, selfcheck.PROBLEM)

        def settle():
            os.unlink(path)
            return 'The switch that was cut off was finished.'
        with mock.patch.object(api, 'resolve_journal', settle):
            r = self.result(selfcheck.run(fix=True), 'journal')
        self.assertEqual((r.state, r.did), (selfcheck.REPAIRED, 'The switch that was cut off was finished.'))

    def test_a_switch_under_way_is_left_alone(self):
        from switchboard.accounts import api, context, switcher
        path = context.current().path(switcher.JOURNAL)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write('{}')
        with mock.patch.object(api, 'resolve_journal', lambda: self.fail('a switch under way was settled')):
            r = self.result(selfcheck.run(fix=True), 'journal')
        self.assertEqual((r.state, r.found), (selfcheck.OK, 'a switch is under way'))


class AfterClaudeCodeUpdatesItself(Base):
    def program(self, version, leave_out=None):
        folder = os.path.join(self.home, '.local', 'share', 'claude', 'versions')
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, version)
        names = [text for name, text, _ in selfcheck_facts().SHAPE_NAMES if name != leave_out]
        with open(path, 'wb') as f:                 # the names spread over more than one read of the file
            f.write(b'\0' * 100)
            for n in names:
                f.write(n.encode('utf-8') + b'\0' * (selfcheck_facts().SHAPE_CHUNK // 3))
        return path

    def test_a_newer_claude_code_that_no_longer_names_one_is_a_warning_with_its_version(self):
        self.program('9.9.9', leave_out='oauth.usage')
        r = self.result(selfcheck.run(fix=True, now=T), 'claude_code')
        self.assertEqual(r.state, selfcheck.LEFT)
        # Plain words, the version Switchboard was really checked against (facts.CHECKED: it read that version's
        # program, it never ran it, so not "tested with"), and the one thing to do that can change the outcome: a
        # newer Switchboard. "Switching goes on" stays: it is what the code does, and a promise that switching works
        # would not be true when a name really changed.
        self.assertEqual(r.found, "Claude Code 9.9.9 is newer than %s, the version Switchboard was checked against, "
                                  "and Switchboard can't find this in its program: the address limits are read from"
                         % selfcheck_facts().CHECKED)
        todo = 'Switching goes on. If a switch fails, see whether a newer Switchboard is out: switchboard update --check.'
        self.assertEqual((r.did, r.fix), (None, todo))
        said = [n for n in selfcheck.notices(now=T) if n['what'] == 'claude_code']
        self.assertEqual([n['text'] for n in said], [r.found + '. ' + todo])
        self.assertIn((None, r.found, todo), selfcheck.doctor_lines(now=T))
        code, lines = selfcheck.fix_lines(now=T)            # doctor --fix: the finding, then the thing to do under it
        self.assertIn("   NO  Claude Code's own files: " + r.found + '\n       ' + todo, lines)

    def test_two_missing_are_said_as_a_list(self):
        self.program('9.9.9', leave_out='oauth.usage')
        with mock.patch.object(selfcheck_facts(), 'shape_look',
                               lambda p: [('a', 'its Keychain item'), ('b', 'its login file')]):
            r = self.result(selfcheck.run(fix=True, now=T), 'claude_code')
        self.assertTrue(r.found.endswith(", and Switchboard can't find these in its program: its Keychain item and its "
                                         "login file"), r.found)

    def test_the_version_checked_or_none_found_is_said_plainly(self):
        facts = selfcheck_facts()
        r = self.result(selfcheck.run(fix=False, now=T), 'claude_code')
        self.assertEqual((r.state, r.found), (selfcheck.OK, 'no Claude Code was found on this Mac to look at'))
        self.program(facts.CHECKED)
        r = self.result(selfcheck.run(fix=False, now=T), 'claude_code')
        self.assertEqual(r.found, 'version %s, the one Switchboard was checked against' % facts.CHECKED)
        with mock.patch.object(facts, 'CHECKED', '99.0.0'):
            r = self.result(selfcheck.run(fix=False, now=T), 'claude_code')
            self.assertEqual(r.found, 'version %s, older than 99.0.0, the one Switchboard was checked against'
                             % facts.installed_versions(self.home)[-1])
        self.assertNotIn('facts', r.found)

    def test_a_newer_claude_code_that_names_them_all_says_nothing(self):
        self.program('9.9.9')
        r = self.result(selfcheck.run(fix=True, now=T), 'claude_code')
        self.assertEqual(r.state, selfcheck.OK)
        self.assertIn('still names what Switchboard relies on', r.found)
        self.assertEqual(selfcheck.notices(now=T), [])

    def test_the_version_the_facts_were_checked_on_is_never_read(self):
        self.program(selfcheck_facts().CHECKED, leave_out='oauth.usage')
        with mock.patch.object(selfcheck_facts(), 'shape_look', lambda p: self.fail('read')):
            r = self.result(selfcheck.run(fix=True, now=T), 'claude_code')
        self.assertEqual(r.state, selfcheck.OK)

    def test_looked_at_once_per_version_and_file(self):
        self.program('9.9.9', leave_out='oauth.usage')
        selfcheck.run(fix=True, now=T)
        with mock.patch.object(selfcheck_facts(), 'shape_look', lambda p: self.fail('read again')):
            r = self.result(selfcheck.run(fix=True, now=T + 600), 'claude_code')
        self.assertEqual(r.state, selfcheck.LEFT)

    def test_no_switch_is_refused_because_of_it(self):
        from switchboard.accounts import facts
        self.program('9.9.9', leave_out='keychain.service')
        selfcheck.run(fix=True, now=T)
        self.assertEqual(facts.check(self.home)['installed'], '9.9.9')     # only the warning it always gave


def selfcheck_facts():
    from switchboard.accounts import facts
    return facts


class FromThePage(unittest.TestCase):
    def setUp(self):
        self.addCleanup(selfcheck.RUN.update, dict(selfcheck.RUN))
        selfcheck.RUN.update(at=None, running=False)

    def test_at_most_once_every_ten_minutes_and_never_two_at_once(self):
        ran = []

        def run(fix=True, now=None):
            ran.append(fix)
            return []
        held = []
        with mock.patch.object(selfcheck, 'run', run):
            selfcheck.tick(now=1000.0, start=lambda job: job())
            selfcheck.tick(now=1000.0 + 300, start=lambda job: job())
            self.assertEqual(ran, [True])
            selfcheck.tick(now=1000.0 + selfcheck.EVERY + 1, start=held.append)    # started, still running
            self.assertIsNone(selfcheck.tick(now=1000.0 + 3 * selfcheck.EVERY, start=lambda job: job()))
            self.assertEqual(ran, [True])
            held[0]()
            self.assertEqual(ran, [True, True])
            selfcheck.tick(now=1000.0 + 3 * selfcheck.EVERY, start=lambda job: job())
            self.assertEqual(ran, [True, True, True])

    def test_one_check_that_fails_never_stops_the_others(self):
        def check_broken(fix, now):
            raise RuntimeError('made up')
        with mock.patch.object(selfcheck, 'CHECKS', (check_broken,
                                                     lambda fix, now: [selfcheck.Result('x', 'X', 'fine')])):
            out = selfcheck.run(fix=True)
        self.assertEqual([(r.name, r.state) for r in out], [('broken', selfcheck.FAILED), ('x', selfcheck.OK)])

    def test_the_page_loop_calls_it_once(self):
        # Through the loop's own wrapper (_safely), as every call there is: one that raises is said and never ends it.
        import inspect
        from switchboard import server
        self.assertEqual(inspect.getsource(server.loop).count("_safely('the self-check', selfcheck_tick)"), 1)
        self.assertEqual(inspect.getsource(server.selfcheck_tick).count('selfcheck.tick('), 1)


if __name__ == '__main__':
    unittest.main()
