import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# What a person can see of the three that put a lost sign-in back: `switchboard doctor`'s block (the sign-in watch,
# the loop that reads the accounts, the page; the last put-back when the engine can say; the newest lines of the
# sign-in record in plain words) and the page state's `watch` block. Made-up heartbeats and records, fresh, stale,
# missing, damaged, with keys nobody knows, and with email- and token-shaped strings fed through every field: none of
# those may reach a line. The watch's module is a made-up one on the path, or none.
import importlib.machinery
import io
import json
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import __main__ as cli  # noqa: E402
from switchboard import core, server  # noqa: E402
import test_cli_commands  # noqa: E402  (its Cli: a throwaway home and the fake accounts)
# The real watch's module, for its Record and PageCheck (taken now: each test puts a made-up one in its place)
from switchboard.accounts import signin_watch as real_watch  # noqa: E402
from fakes_autoswitch import FakeClock  # noqa: E402
from signin_watch_fakes import Answer, T0_MONO, make  # noqa: E402

EMAIL = 'someone@work.example'
TOKEN = 'sk-ant-oat01-' + 'Q' * 40
SECRETS = (EMAIL, TOKEN, '@', 'sk-ant', 'Q' * 24)
NOW = time.time()


def watch_module(test, there=True):
    p = mock.patch.dict(sys.modules)
    p.start()
    test.addCleanup(p.stop)
    if there:
        m = types.ModuleType(server.WATCH_MODULE)
        m.__spec__ = importlib.machinery.ModuleSpec(server.WATCH_MODULE, None)
        sys.modules[server.WATCH_MODULE] = m
    else:
        sys.modules[server.WATCH_MODULE] = None


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-watch-doctor-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data = os.path.join(self.tmp, 'data')
        self.home = os.path.join(self.tmp, 'home')
        os.makedirs(self.data)
        os.makedirs(os.path.join(self.home, 'Library', 'LaunchAgents'))
        env = mock.patch.dict(os.environ, {'HOME': self.home, 'SWITCHBOARD_HOME': self.data})
        env.start()
        self.addCleanup(env.stop)
        for d, fresh in ((server.WATCH, {'next': None, 'stale': 0, 'down_since': None, 'calls': [], 'kicked_at': None,
                                         'started_at': None, 'gave_up': False}),
                         (server.STANDIN, {'next': None, 'on': False, 'thread': None, 'started': None,
                                           'late_said': False, 'looked_at': None, 'errors': {}})):
            p = mock.patch.dict(d, fresh, clear=True)
            p.start()
            self.addCleanup(p.stop)
        watch_module(self)
        self.alive = {812}                      # the made-up heartbeats' pid is a live process (a beat counts as a
        p = mock.patch.object(server, '_alive', lambda pid: pid in self.alive)   # running watch's only then)
        p.start()
        self.addCleanup(p.stop)

    def job(self):
        with open(server.watch_plist(), 'w') as f:
            f.write('<plist/>')

    def heartbeat(self, text=None, **hb):
        with open(os.path.join(self.data, server.WATCH_BEAT), 'w') as f:
            f.write(text if text is not None else json.dumps(hb))

    def record(self, *rows):
        with open(os.path.join(self.data, server.WATCH_RECORD), 'w') as f:
            for r in rows:
                f.write((r if isinstance(r, str) else json.dumps(r)) + '\n')

    def clean(self, text):
        for s in SECRETS:
            self.assertNotIn(s, text)


class TheStateBlock(Base):
    def test_fresh(self):
        self.heartbeat(pid=812, beat_at=NOW - 4, mode='events', last_action='put_back', last_action_at=NOW - 60,
                       problem=None, looks=12)
        v = server.watch_view(wall=NOW)
        self.assertEqual(v, {'there': True, 'running': True, 'beat_age_s': 4.0, 'mode': 'events',
                             'last_action': 'put_back', 'last_action_at': NOW - 60, 'problem': None, 'line': None,
                             'page_looking': False, 'page_looked_at': None, 'signin_file': server.MIRROR_WORDS})

    def test_stale_missing_and_damaged(self):
        self.heartbeat(pid=812, beat_at=NOW - 400, mode='polling')
        v = server.watch_view(wall=NOW)
        self.assertEqual((v['there'], v['running'], v['beat_age_s'], v['mode']), (True, False, 400.0, 'polling'))
        os.remove(os.path.join(self.data, server.WATCH_BEAT))
        v = server.watch_view(wall=NOW)
        self.assertEqual((v['there'], v['running'], v['beat_age_s']), (True, False, None))
        self.heartbeat('{"pid": 81')
        v = server.watch_view(wall=NOW)
        self.assertEqual((v['running'], v['beat_age_s'], v['mode']), (False, None, None))
        self.heartbeat('[1, 2]')
        self.assertFalse(server.watch_view(wall=NOW)['running'])

    def test_no_watch_in_this_copy(self):
        watch_module(self, there=False)
        self.heartbeat(pid=812, beat_at=NOW - 4)
        v = server.watch_view(wall=NOW)
        self.assertEqual((v['there'], v['running'], v['line']), (False, False, None))

    def test_nothing_secret_and_nothing_unknown_reaches_it(self):
        self.heartbeat(pid=812, beat_at=NOW - 4, mode=EMAIL, last_action=TOKEN, last_action_at='yesterday',
                       problem='could not read %s with %s' % (EMAIL, TOKEN), secret=TOKEN, who=EMAIL)
        v = server.watch_view(wall=NOW)
        self.clean(json.dumps(v))
        self.assertEqual((v['mode'], v['last_action'], v['last_action_at'], v['problem']), (None, None, None, None))
        self.assertEqual(sorted(v), ['beat_age_s', 'last_action', 'last_action_at', 'line', 'mode', 'page_looked_at',
                                     'page_looking', 'problem', 'running', 'signin_file', 'there'])
        self.assertIn(v['signin_file'], (None, server.MIRROR_WORDS))

    def test_plain_words_from_the_watch_are_kept(self):
        self.heartbeat(pid=812, beat_at=NOW - 4, problem='Watching by file stamps once a second (kqueue failed).')
        self.assertEqual(server.watch_view(wall=NOW)['problem'],
                         'Watching by file stamps once a second (kqueue failed).')

    def test_it_is_in_the_page_state(self):
        f = {'profiles': {}, 'now': NOW, 'auto': {}, 'links': {}, 'engine': {'installed': True}, 'browser': {},
             'setup_hidden': False, 'demo': False, 'watch': server.watch_view(wall=NOW)}
        self.assertEqual(server.render(core.load(), f)['watch'], f['watch'])


class TheRecord(Base):
    def test_the_newest_five_in_plain_words_newest_first(self):
        rows = [{'at': NOW - 600 + i, 'hint': {'keychain': True, 'login_file': None, 'settings': False,
                                                'tick': False}, 'action': 'nothing'} for i in range(10)]
        rows.append({'at': NOW - 5, 'hint': {'keychain': True, 'login_file': 'appeared', 'settings': False,
                                             'tick': False}, 'action': 'put_back', 'seen': {'had_login': False}})
        self.record(*rows)
        got = server.record_lines(5, now=NOW)
        self.assertEqual(len(got), 5)
        self.assertEqual(got[0], "%s  the Keychain changed, Claude Code's login file appeared: put the sign-in back"
                         % time.strftime('%-I:%M:%S %p', time.localtime(NOW - 5)))
        self.assertTrue(got[1].endswith('  the Keychain changed'))             # (a word with no words for a person)

    def test_damaged_and_unknown_lines_and_secrets(self):
        self.record('not json', '{"at": 1', json.dumps([1, 2]),
                    {'at': NOW - 3, 'hint': {'settings': True}, 'action': TOKEN, 'who': EMAIL,
                     'seen': {'email': EMAIL, 'token': TOKEN}},
                    {'at': NOW - 2, 'hint': {'login_file': EMAIL, 'keychain': 'yes'}, 'action': 'put back ' + EMAIL},
                    {'at': 'whenever', 'something_new': {'x': 1}, 'hint': {'tick': True}},
                    {'only': 'keys nobody knows'})
        got = server.record_lines(5, now=NOW)
        self.clean('\n'.join(got))
        self.assertEqual(got, ['--  a regular look', "%s  Claude Code's settings changed"
                               % time.strftime('%-I:%M:%S %p', time.localtime(NOW - 3))])

    def test_no_record(self):
        self.assertEqual(server.record_lines(5), [])

    def test_only_the_end_of_a_big_record_is_read(self):
        self.record(*([{'at': NOW - 100, 'hint': {'tick': True}, 'action': 'nothing'}] * 5000 +
                      [{'at': NOW - 1, 'hint': {'settings': True}, 'action': 'looked'}]))
        self.assertTrue(server.record_lines(5, now=NOW)[0].endswith("  Claude Code's settings changed"))


class TheRealRecord(Base):
    """Lines the real sign-in watch writes (signin_watch.Record, keyed `t`), from a real Watch on a fake clock with a
    made-up look, and a real PageCheck with a stand-in launchctl, in a person's words: never the engine's own words."""

    def lines(self, answer):
        w, sim, looks, beats, lines, logs = make(answer=answer)
        w.run(until=T0_MONO + 2.0)
        return [x for x in lines if '"kind":"look"' in x], sim.clock

    def page_lines(self, rc):
        clock, lines = FakeClock(), []
        page = real_watch.PageCheck(7955, '/nonexistent/page.plist', launchctl=lambda *a: rc,
                                    connect=lambda port, t: False, exists=lambda p: True, clock=clock,
                                    record=real_watch.Record(clock=clock, sink=lines.append),
                                    started_m=clock.mono() - 1000.0, uid=501)
        for _ in range(3):
            page.check()
            clock.advance(50.0)
        self.assertEqual(len(lines), 1, lines)
        return lines, clock

    def said(self, lines, clock):
        got = [server.record_line(json.loads(x), clock.wall()) for x in lines]
        at = time.strftime('%-I:%M:%S %p', time.localtime(json.loads(lines[0])['t']))
        return got, at

    def test_each_answer_of_a_look_in_plain_words(self):
        words = {'put_back': 'put the sign-in back',
                 'promote': "put the sign-in back from Claude Code's plain login file",
                 'kept': "saved Claude Code's newer login",
                 'wait': 'saw it missing; looking again',
                 'limit': 'stopped putting it back for now',
                 'failed': 'could not write it; trying again'}
        for action, said in words.items():
            with self.subTest(action=action):
                lines, clock = self.lines(lambda hint, a=action: Answer(a, seen={'had_login': False}))
                self.assertEqual(len(lines), 1, lines)
                self.assertIn('"t":', lines[0])
                got, at = self.said(lines, clock)
                self.assertEqual(got, ['%s  a regular look: %s' % (at, said)])

    def test_a_look_that_failed_and_an_answer_nobody_knows(self):
        def boom(hint):
            raise RuntimeError('made up')
        lines, clock = self.lines(boom)
        got, at = self.said(lines, clock)
        self.assertEqual(got, ['%s  a regular look: the look failed' % at])
        lines, clock = self.lines(lambda hint: Answer('something_new'))
        self.assertIn('"action":"other"', lines[0])
        got, at = self.said(lines, clock)
        self.assertEqual(got, ['%s  a regular look' % at])

    def test_what_the_watch_did_with_the_plain_login_file_in_plain_words(self):
        clock = FakeClock()
        words = {'held': "put Claude Code's plain login file back the moment it went",
                 'removed': 'took away the plain login file it had kept for Claude Code',
                 'stays': "left Claude Code's plain login file in place: Claude Code is using it",
                 'cap': "stopped putting Claude Code's plain login file back for a minute (60 times in the last one)"}
        for did, said in words.items():
            with self.subTest(did=did):
                lines = []
                real_watch.Record(clock=clock, sink=lines.append).add('hold', event='gone', did=did, made=1, failed=0)
                got, at = self.said(lines, clock)
                self.assertEqual(got, ['%s  %s' % (at, said)])
        lines = []
        real_watch.Record(clock=clock, sink=lines.append).add('hold', event='appeared', did='kept', made=0, failed=0)
        self.assertEqual(self.said(lines, clock)[0], [None])   # keeping a copy for three seconds is not told

    def test_the_watch_starting_the_page_is_not_a_look(self):
        lines, clock = self.page_lines(0)
        self.assertIn('"action":"kickstart"', lines[0])
        got, at = self.said(lines, clock)
        self.assertEqual(got, ['%s  started the page' % at])
        lines, clock = self.page_lines(113)                   # kickstart, then bootstrap: launchd said no to both
        self.assertIn('"action":"bootstrap"', lines[0])
        got, at = self.said(lines, clock)
        self.assertEqual(got, ['%s  could not start the page' % at])

    def test_no_word_of_the_engine_reaches_a_person(self):
        every = []
        for action in ('put_back', 'promote', 'kept', 'wait', 'limit', 'failed', 'something_new'):
            every += self.lines(lambda hint, a=action: Answer(a))[0]
        every += self.page_lines(0)[0] + self.page_lines(113)[0]
        self.record(*every)
        got = server.record_lines(20, now=time.time())
        self.assertEqual(len(got), len(every))
        for line in got:
            for inner in ('promote', 'kickstart', 'bootstrap', 'other', 'put_back', '_', 'a look: '):
                self.assertNotIn(inner, line.split('  ', 1)[1])


class TheDoctorBlock(Base):
    def doctor(self, page=None, auto=None, last=None):
        said = []
        out = io.StringIO()
        patches = [mock.patch.object(cli, 'page_state', lambda timeout=5: page),
                   mock.patch.object(cli.api, 'auto_status', lambda: auto or {})]
        if last is not None:
            patches.append(mock.patch.object(cli.api, 'last_put_back', last, create=True))
        elif hasattr(cli.api, 'last_put_back'):
            patches.append(mock.patch.object(cli.api, 'last_put_back', None))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        with redirect_stdout(out):
            cli._doctor_signin_looks(lambda good, text, fix='': said.append((good, text, fix)), now=NOW)
        self.clean(repr(said) + out.getvalue())
        return said, out.getvalue()

    def test_all_three_running(self):
        self.job()
        self.heartbeat(pid=812, beat_at=NOW - 4, last_look_at=NOW - 12, mode='events')
        said, out = self.doctor(page={'accounts': [], 'watch': {'page_looking': False}}, auto={'beat_age_s': 20.0})
        self.assertEqual(said, [
            (True, 'The sign-in watch: running, last looked 12 s ago (pid 812)', ''),
            (True, 'Second look at the sign-in: the loop that reads the accounts, last heartbeat 20 s ago', ''),
            (True, 'Third look at the sign-in: the page, standing by while either of them runs', '')])
        # the standing copy of the sign-in, on unless the setting says false
        self.assertEqual(out, '       %s\n' % server.MIRROR_WORDS)

    def test_the_copy_of_the_sign_in_turned_off_is_said(self):
        self.job()
        self.heartbeat(pid=812, beat_at=NOW - 4, last_look_at=NOW - 12, mode='events')
        with open(os.path.join(self.data, 'settings.json'), 'w') as f:
            json.dump({'auto_policy': {'keep_signin_file': False}}, f)
        _, out = self.doctor(page={'accounts': [], 'watch': {'page_looking': False}}, auto={'beat_age_s': 20.0})
        self.assertEqual(out, "       Keeping Claude Code's sign-in in its plain login file too is turned off "
                              '(auto_policy.keep_signin_file in %s)\n'
                         % cli._short_path(os.path.join(self.data, 'settings.json')))
        self.assertIsNone(server.watch_view(wall=NOW)['signin_file'])

    def test_the_watch_stopped_the_loop_quiet_the_page_looking(self):
        self.job()
        self.heartbeat(pid=812, beat_at=NOW - 400)
        said, _ = self.doctor(page={'accounts': [], 'watch': {'page_looking': True, 'page_looked_at': NOW - 8}},
                              auto={'beat_age_s': 900.0})
        self.assertEqual(said[0], (False, 'The sign-in watch: not running (last heartbeat 6 min ago)',
                                   'The page starts it again. Its log: %s'
                                   % cli._short_path(os.path.join(self.data, 'signin-watch.log'))))
        self.assertEqual(said[1], (None, 'Second look at the sign-in: the loop that reads the accounts, last heartbeat '
                                         '15 min ago', ''))
        self.assertEqual(said[2], (True, 'Third look at the sign-in: the page, looking itself while both are quiet, '
                                         'last 8 s ago', ''))

    def test_a_watch_that_stopped_or_died_a_moment_ago_is_not_running(self):
        self.job()
        self.heartbeat(pid=812, beat_at=NOW - 3, last_look_at=NOW - 12, stopped_at=NOW - 3)
        said, _ = self.doctor(page={'accounts': [], 'watch': {}}, auto={'beat_age_s': 20.0})
        self.assertEqual(said[0][:2], (False, 'The sign-in watch: not running (last heartbeat 3 s ago)'))
        self.heartbeat(pid=812, beat_at=NOW - 3, last_look_at=NOW - 12)
        self.alive.discard(812)
        said, _ = self.doctor(page={'accounts': [], 'watch': {}}, auto={'beat_age_s': 20.0})
        self.assertEqual(said[0][:2], (False, 'The sign-in watch: not running (last heartbeat 3 s ago)'))

    def test_the_keychain_folder_that_cannot_be_read_is_said(self):
        self.job()
        self.heartbeat(pid=812, beat_at=NOW - 3, last_look_at=NOW - 12, problem='the Keychain folder cannot be read')
        said, out = self.doctor(page={'accounts': [], 'watch': {}}, auto={'beat_age_s': 20.0})
        self.assertEqual(said[0][:2], (True, 'The sign-in watch: running, last looked 12 s ago (pid 812)'))
        self.assertEqual(out, '       the Keychain folder cannot be read\n       %s\n' % server.MIRROR_WORDS)
        self.assertEqual(server.watch_view(wall=NOW)['problem'], 'the Keychain folder cannot be read')

    def test_the_page_gave_up_says_so(self):
        self.job()
        said, _ = self.doctor(page={'accounts': [], 'watch': {'problem': server.WATCH_GAVE_UP}})
        self.assertEqual(said[0], (False, 'The sign-in watch: not running', server.WATCH_GAVE_UP))
        self.assertEqual(said[2][1], 'Third look at the sign-in: the page, standing by while either of them runs')

    def page_job(self):
        with open(os.path.join(self.home, 'Library', 'LaunchAgents', cli.AGENT + '.plist'), 'w') as f:
            f.write('<plist/>')

    def test_not_installed_and_no_page(self):
        # The page's job is there and the watch's is not (an install from before the watch): the installer adds it.
        self.page_job()
        self.heartbeat(pid=812, beat_at=NOW - 400)
        said, _ = self.doctor()
        self.assertEqual(said[0], (None, 'The sign-in watch: not installed', 'Run the installer again to add it.'))
        self.assertEqual(said[1], (None, 'Second look at the sign-in: the loop that reads the accounts, no heartbeat',
                                   ''))
        self.assertEqual(said[2], (None, 'Third look at the sign-in: the page, not running', ''))

    def test_an_install_with_no_background_jobs_says_so_with_no_step(self):
        # Made with --no-agent: no job at all, and an update keeps it so (install_choices), so running the installer
        # again adds nothing. Said as the menu bar's "off, by your choice" is: a fact, no step.
        for choices in ('--no-agent --no-menu-app\n', None):
            with self.subTest(choices=choices):
                path = os.path.join(self.data, 'app', 'choices')
                if choices is None:
                    if os.path.exists(path):
                        os.remove(path)             # no record: the page's own job is missing too
                else:
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, 'w') as f:
                        f.write(choices)
                said, _ = self.doctor()
                self.assertEqual(said[0], (None, 'The sign-in watch: no background jobs in this install (--no-agent)',
                                           ''))

    def test_this_build_has_no_watch(self):
        watch_module(self, there=False)
        said, _ = self.doctor()
        self.assertEqual(said[0], (None, 'The sign-in watch: this build has no sign-in watch', ''))

    def test_the_last_put_back_and_the_record(self):
        self.job()
        self.heartbeat(pid=812, beat_at=NOW - 4, problem='could not read %s' % EMAIL)
        self.record({'at': NOW - 30, 'hint': {'keychain': True}, 'action': 'put_back', 'seen': {'who': EMAIL}})
        said, out = self.doctor(last=lambda: {'at': NOW - 30, 'email': EMAIL, 'token': TOKEN})
        self.assertFalse([s for s in said if 'put back' in s[1]])          # the engine's health check says it, once
        self.assertEqual(out, '       %s\n       The sign-in record, newest first:\n         %s  the Keychain changed: '
                         'put the sign-in back\n' % (server.MIRROR_WORDS,
                                                      time.strftime('%-I:%M:%S %p', time.localtime(NOW - 30))))

    def test_an_engine_that_cannot_say_says_nothing(self):
        def boom():
            raise RuntimeError(EMAIL)
        said, _ = self.doctor(last=boom)
        self.assertFalse([s for s in said if 'put back' in s[1]])
        said, _ = self.doctor(last=lambda: None)
        self.assertFalse([s for s in said if 'put back' in s[1]])


class TheWholeDoctor(test_cli_commands.Cli):
    def test_doctor_has_the_block_and_without_a_watch_it_says_so(self):
        watch_module(self, there=False)
        with mock.patch.object(cli, 'page_running', lambda: False), \
                mock.patch.object(cli.linkapp, 'status', lambda fresh=False: {'installed': False, 'on': False}), \
                mock.patch.object(cli.browser, 'present', lambda: False), \
                mock.patch.object(cli.connect, 'claude_bin', lambda: None):
            code, out, err = self.run_cli('doctor')
        self.assertIn(' --  The sign-in watch: this build has no sign-in watch\n', out)
        self.assertIn(' --  Second look at the sign-in: the loop that reads the accounts, no heartbeat\n', out)
        self.assertIn(' --  Third look at the sign-in: the page, not running\n', out)


if __name__ == '__main__':
    unittest.main()
