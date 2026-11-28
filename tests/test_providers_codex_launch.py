import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# How Codex's app is started on a note (DESIGN 12.3 and 12.4): start() and resume() only say how (a Launch), they never
# run anything. The terminal argument list, the app link (encoded) with `open -a` for the app that holds the chosen
# program, and the printed line (with `env -u` for a made-up key in the Reach's environment) are exact; no flag that
# loosens Codex's sandbox or approvals, or joins its shared server, is ever passed; the environment is the Reach's
# without the billing names, and os.environ is never read; a chat id of another shape is never put in an argument
# list. Each argument list is also handed to a stand-in of each copied version, which takes only what that version's
# help has.
import json
import os
import shlex
import sys
import unittest
from unittest import mock
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from fakes_codex import VERSIONS, Rig
from fakes_providers import run_real
from switchboard.providers import codex_facts, contract, words
from switchboard.providers.contract import IN_APP, IN_TERMINAL, PRINT, ChatRef, Handoff
from switchboard.providers.run import child_env

HOP = 'h-0123abcd'
CHAT = 'aaaaaaaa-0000-4000-8000-00000000000a'
KEY = 'made-up-key-0123456789'


class Base(unittest.TestCase):
    version = '0.169.1'

    def setUp(self):
        self.rig = Rig(self, env={'CODEX_API_KEY': KEY, 'MADE_UP_PLAIN': 'kept'})
        self.fake = self.rig.app('ChatGPT.app', self.version)
        self.folder = os.path.join(self.rig.root, 'Users', 'maya', 'my site')
        os.makedirs(self.folder)
        notes = os.path.join(self.rig.root, 'notes', 't-0123abcd')
        os.makedirs(notes)
        self.note = os.path.join(notes, HOP + '.md')
        meta = os.path.join(notes, HOP + '.json')
        with open(meta, 'w') as f:
            json.dump({'v': 1, 'hop': HOP, 'from': {'provider': 'claude', 'chat': CHAT}, 'to': {'provider': 'codex'}},
                      f)
        self.handoff = Handoff(hop=HOP, path=self.note, meta_path=meta, bytes=10, sha256='0' * 64)
        self.p = self.rig.provider

    def first(self):
        return words.FIRST_WORDS.format(hop=HOP, path=self.note, from_name='Claude')

    def back(self):
        return words.BACK_WORDS.format(hop=HOP, path=self.note, other_name='Claude')

    def chat(self, **changes):
        fields = dict(provider='codex', id=CHAT, folder=self.folder, path=os.path.join(self.rig.root, 'c.jsonl'),
                      last_at=self.rig.clock() - 3600, version=self.version)
        fields.update(changes)
        return ChatRef(**fields)

    def assert_nothing_started(self):
        self.assertEqual(self.rig.calls.execs, [])
        self.assertEqual(self.rig.calls.links, [])
        self.assertEqual(self.rig.calls.pipes, [])
        self.assertEqual([r for r in self.fake.runs() if r != ['--version']], [])

    def assert_never_loosened(self, argv):
        for word in argv:
            self.assertNotIn(word, codex_facts.NEVER_FLAGS)
            self.assertFalse(word.startswith(codex_facts.NEVER_PREFIX), word)
            self.assertFalse(word.startswith('--dangerously'), word)


class Start(Base):

    def test_terminal_argv_env_and_folder_exact(self):
        launch = self.p.start(self.folder, self.handoff, IN_TERMINAL)
        self.assertEqual(launch.argv, (self.fake.path, '-C', self.folder, self.first()))
        self.assertEqual((launch.provider, launch.how, launch.cwd, launch.first_words),
                         ('codex', IN_TERMINAL, self.folder, self.first()))
        self.assertEqual(launch.program.path, self.fake.path)
        self.assertNotIn('CODEX_API_KEY', launch.env)
        self.assertEqual(launch.env['MADE_UP_PLAIN'], 'kept')
        self.assert_never_loosened(launch.argv)
        self.assert_nothing_started()

    def test_the_environment_is_the_reachs_never_os_environ(self):
        with mock.patch.dict(os.environ, {'SWITCHBOARD_TEST_CANARY_ENV': 'from-os-environ'}):
            launch = self.p.start(self.folder, self.handoff, IN_TERMINAL)
        self.assertEqual(dict(launch.env), child_env(self.rig.reach.env))
        self.assertNotIn('SWITCHBOARD_TEST_CANARY_ENV', launch.env)

    def test_the_printed_line_exact(self):
        launch = self.p.start(self.folder, self.handoff, PRINT)
        self.assertEqual(launch.how, PRINT)
        q = shlex.quote
        self.assertEqual(launch.line, 'cd %s && env -u CODEX_API_KEY %s -C %s %s'
                         % (q(self.folder), q(self.fake.path), q(self.folder), q(self.first())))
        self.assertEqual(launch.argv, (self.fake.path, '-C', self.folder, self.first()))
        self.assert_nothing_started()

    def test_no_line_for_a_folder_with_a_control_character(self):
        odd = os.path.join(self.rig.root, 'odd\x1b[31mname')
        os.makedirs(odd)
        self.assertEqual(self.p.start(odd, self.handoff, PRINT).line, '')

    def test_the_app_link_exact_and_opened_with_the_app_that_holds_the_program(self):
        launch = self.p.start(self.folder, self.handoff, IN_APP)
        url = 'codex://new?prompt=%s&path=%s' % (quote(self.first(), safe=''), quote(self.folder, safe=''))
        self.assertEqual(launch.url, url)
        self.assertIn('path=' + quote(self.rig.root, safe='') + '%2FUsers%2Fmaya%2Fmy%20site', url)
        self.assertNotIn(' ', url)
        self.assertEqual(launch.app, os.path.join(self.rig.apps, 'ChatGPT.app'))
        self.assertEqual(launch.argv, ('/usr/bin/open', '-a', launch.app, url))
        self.assertEqual(launch.how, IN_APP)
        self.assertNotIn('CODEX_API_KEY', launch.env)
        self.assert_nothing_started()

    def test_the_app_link_alone_when_the_program_is_not_in_an_app(self):
        os.chmod(self.fake.path, 0o644)
        self.rig.on_path(self.version)
        launch = self.p.start(self.folder, self.handoff, IN_APP)
        self.assertIsNone(launch.app)
        self.assertEqual(launch.argv, ('/usr/bin/open', launch.url))

    def test_a_link_encodes_what_a_url_would_split_on(self):
        odd = os.path.join(self.rig.root, 'a&b#c?d=e')
        os.makedirs(odd)
        url = self.p.start(odd, self.handoff, IN_APP).url
        self.assertEqual(url.count('&'), 1)
        self.assertNotIn('#', url)
        self.assertEqual(url.count('?'), 1)

    def test_the_first_message_holds_fixed_words_and_the_path_only(self):
        launch = self.p.start(self.folder, self.handoff, IN_TERMINAL)
        opening = 'Switchboard note %s. Read the file %s first.' % (HOP, self.note)
        self.assertTrue(launch.first_words.startswith(opening))
        self.assertTrue(words.NOTE_MARK_RE.match(launch.first_words))

    def test_the_source_name_from_the_notes_own_sidecar(self):
        with open(self.handoff.meta_path, 'w') as f:
            json.dump({'from': {'provider': 'not-a-provider'}}, f)
        launch = self.p.start(self.folder, self.handoff, IN_TERMINAL)
        self.assertIn('began in another assistant in this folder', launch.first_words)

    def test_too_old_starts_nothing(self):
        self.rig.chatfiles.newest = '0.170.0'
        for how in (IN_TERMINAL, IN_APP, PRINT):
            with self.assertRaises(contract.ProviderError) as e:
                self.p.start(self.folder, self.handoff, how)
            self.assertEqual(e.exception.kind, 'too_old')
        self.assert_nothing_started()

    def test_not_installed(self):
        os.chmod(self.fake.path, 0o644)
        with self.assertRaises(contract.ProviderError) as e:
            self.p.start(self.folder, self.handoff, IN_TERMINAL)
        self.assertEqual(e.exception.kind, 'not_installed')


class Resume(Base):

    def test_terminal_argv_exact(self):
        launch = self.p.resume(self.chat(), self.handoff, IN_TERMINAL)
        self.assertEqual(launch.argv, (self.fake.path, 'resume', '-C', self.folder, CHAT, self.back()))
        self.assertEqual((launch.cwd, launch.how, launch.first_words), (self.folder, IN_TERMINAL, self.back()))
        self.assertNotIn('CODEX_API_KEY', launch.env)
        self.assertIn('env -u CODEX_API_KEY', launch.line)
        self.assert_never_loosened(launch.argv)
        self.assert_nothing_started()

    def test_a_chat_id_of_another_shape_is_refused(self):
        for bad in ('../x', 'AAAAAAAA-0000-4000-8000-00000000000A', CHAT + ' --last', '-' + CHAT[1:], ''):
            with self.assertRaises(contract.ProviderError) as e:
                self.p.resume(self.chat(id=bad), self.handoff, IN_TERMINAL)
            self.assertEqual(e.exception.kind, 'refused')
        self.assert_nothing_started()

    def test_a_chat_written_in_the_last_minute_or_live_is_busy(self):
        for changes in ({'last_at': self.rig.clock() - 30}, {'working': True}, {'live': True}):
            with self.assertRaises(contract.ProviderError) as e:
                self.p.resume(self.chat(**changes), self.handoff, IN_TERMINAL)
            self.assertEqual(e.exception.kind, 'busy')

    def test_a_chat_written_by_a_newer_codex(self):
        with self.assertRaises(contract.ProviderError) as e:
            self.p.resume(self.chat(version='0.170.0'), self.handoff, IN_TERMINAL)
        self.assertEqual(e.exception.kind, 'too_old')

    def test_no_app_link_for_a_chat(self):
        got = self.p.resume(self.chat(), self.handoff, IN_APP)
        self.assertIsInstance(got, contract.Unsupported)
        self.assertEqual(got.why, codex_facts.NO_APP_RESUME)


class OpenHere(Base):
    """A key press in the list with no chat to carry over: the terminal app in the folder, with no first message."""

    def test_terminal_argv_env_and_folder_exact(self):
        launch = self.p.open_here(self.folder, IN_TERMINAL)
        self.assertEqual(launch.argv, (self.fake.path, '-C', self.folder))
        self.assertEqual((launch.provider, launch.how, launch.cwd, launch.first_words),
                         ('codex', IN_TERMINAL, self.folder, ''))
        self.assertEqual(dict(launch.env), child_env(self.rig.reach.env))
        self.assertNotIn('CODEX_API_KEY', launch.env)
        self.assert_never_loosened(launch.argv)
        self.assert_nothing_started()

    def test_the_printed_line_exact(self):
        q = shlex.quote
        self.assertEqual(self.p.open_here(self.folder, PRINT).line,
                         'cd %s && env -u CODEX_API_KEY %s -C %s' % (q(self.folder), q(self.fake.path), q(self.folder)))

    def test_no_app_link_and_no_relative_folder(self):
        for folder, how in ((self.folder, IN_APP), ('my site', IN_TERMINAL)):
            with self.assertRaises(ValueError):
                self.p.open_here(folder, how)
        self.assert_nothing_started()

    def test_too_old_starts_nothing(self):
        self.rig.chatfiles.newest = '0.170.0'
        with self.assertRaises(contract.ProviderError) as e:
            self.p.open_here(self.folder, IN_TERMINAL)
        self.assertEqual(e.exception.kind, 'too_old')
        self.assert_nothing_started()


class TheStandInTakesEachArgumentList(unittest.TestCase):
    """Each Launch's argument list, run on a stand-in of each copied version: it accepts exactly what that version's
    help has, so these lists would not stop at the real program's parser."""

    def test_both_versions(self):
        for version in VERSIONS:
            case = Base('setUp')
            case.version = version
            case.setUp()
            try:
                for launch in (case.p.start(case.folder, case.handoff, IN_TERMINAL),
                               case.p.resume(case.chat(), case.handoff, IN_TERMINAL),
                               case.p.open_here(case.folder, IN_TERMINAL)):
                    ran = run_real(list(launch.argv), env=dict(launch.env), cwd=launch.cwd, timeout=20.0)
                    self.assertEqual(ran.code, 0, (version, ran.err))
                self.assertEqual(case.fake.forbidden(), [])
                starts = [r for r in case.fake.records() if r.get('command') in ('', 'resume')]
                self.assertEqual([r['command'] for r in starts], ['', 'resume', ''])
                self.assertEqual(starts[0]['options'], {'--cd': [case.folder]})
                self.assertEqual(starts[0]['positionals'], [case.first()])
                self.assertEqual(starts[1]['positionals'], [CHAT, case.back()])
                self.assertEqual((starts[2]['options'], starts[2]['positionals']), ({'--cd': [case.folder]}, []))
            finally:
                case.doCleanups()


if __name__ == '__main__':
    unittest.main()
