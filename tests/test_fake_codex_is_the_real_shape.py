import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The stand-in fake-codex acts as the Codex version it copies, for each version whose help and schema were copied
# (0.155.1 and 0.169.1): its --version line, every flag its help has accepted and every other refused with exit 2, a
# value-taking flag with no value refused the same way, the commands Switchboard must never start answered with exit 3
# and recorded, and its app server's answers to the four lines of the ask fitting the generated schema of that version.
# A stand-in kinder than the real program hid two bugs in earlier rounds; these tests are what keeps this one honest.
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from fakes_codex import RESULTS, VERSIONS, FakeCodex, flags, schema, schema_errors
from fakes_providers import LinePipe, run_real
from switchboard.providers import codex_facts
from switchboard.providers.codex import CodexProvider

STEP0_SOURCES = {'0.169.1': ('chatgpt-help.txt', 'help169-exec.txt', 'help169-exec-resume.txt', 'help169-resume.txt',
                             'help169-fork.txt', 'help169-app-server.txt'),
                 '0.155.1': ('help.txt', 'help-exec.txt', 'help-exec-resume.txt', 'help-resume.txt', 'help-fork.txt',
                             'help-app-server.txt')}


def every_flag(command):
    """argv words that give each flag of a command once (a value-taking one with a made-up value)."""
    words = []
    for o in command['options']:
        if o['long'] in ('--help', '--version', '--listen', '--startup-sign-out'):
            continue                    # (those two are the ones the stand-in answers with exit 3; tested below)
        name = o['long'] or o['short']
        words += [name, 'x'] if o['value'] else [name]
    return words


class Base(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='fake-codex-')
        self.addCleanup(shutil.rmtree, self.root, True)
        self.env = {'HOME': os.path.join(self.root, 'home'), 'PATH': '/usr/bin:/bin'}
        os.makedirs(self.env['HOME'])

    def fake(self, version, **spec):
        return FakeCodex(os.path.join(self.root, version), version, **spec)

    def run_fake(self, fake, *args, stdin=None, timeout=20.0):
        return run_real([fake.path] + list(args), stdin=stdin, timeout=timeout, env=self.env, cwd=self.root)


class TheFlagsFilesComeFromTheirOwnVersionsHelp(unittest.TestCase):

    def test_each_command_names_its_capture(self):
        for v in VERSIONS:
            data = flags(v)
            self.assertEqual(data['version'], v)
            self.assertEqual(sorted(data['commands']), ['', 'app-server', 'exec', 'exec resume', 'fork', 'resume'])
            self.assertEqual(sorted(c['source'] for c in data['commands'].values()), sorted(STEP0_SOURCES[v]))
            for c in data['commands'].values():
                self.assertRegex(c['sha256'], r'^[0-9a-f]{64}$')
                self.assertTrue(c['usage'].startswith('codex'))

    def test_what_switchboard_passes_is_in_both_versions(self):
        for v in VERSIONS:
            cmds = flags(v)['commands']

            def has(cmd, word):
                return any(word in (o['short'], o['long']) for o in cmds[cmd]['options'])
            for word in ('-C', '-s', '-c', '--skip-git-repo-check', '--ephemeral', '--color', '-o'):
                self.assertTrue(has('exec', word), (v, word))
            self.assertTrue(has('', '-C'), v)
            self.assertTrue(has('resume', '-C'), v)
            self.assertIn('PROMPT', cmds['']['positionals'])
            self.assertEqual(cmds['resume']['positionals'], ['SESSION_ID', 'PROMPT'])

    def test_a_flag_one_version_lacks_is_absent_there(self):
        new = flags('0.169.1')['commands']
        old = flags('0.155.1')['commands']

        def longs(cmds, cmd):
            return {o['long'] for o in cmds[cmd]['options']}
        self.assertIn('--no-daemon', longs(new, ''))
        self.assertNotIn('--no-daemon', longs(old, ''))
        self.assertIn('--cyber-access-program', longs(new, 'exec'))
        self.assertNotIn('--cyber-access-program', longs(old, 'exec'))
        self.assertIn('--startup-sign-out', longs(new, 'app-server'))


class TheCommandLine(Base):

    def test_version_prints_as_the_real_one(self):
        for v in VERSIONS:
            fake = self.fake(v)
            ran = self.run_fake(fake, '--version')
            self.assertEqual((ran.code, ran.out), (0, 'codex-cli %s\n' % v))
            m = codex_facts.VERSION_RE.search(ran.out)
            self.assertEqual(m.group(1), v)

    def test_every_flag_of_its_help_is_taken(self):
        for v in VERSIONS:
            fake = self.fake(v)
            for cmd, entry in flags(v)['commands'].items():
                words = cmd.split() + every_flag(entry) + ['--help']
                ran = self.run_fake(fake, *words)
                self.assertEqual(ran.code, 0, (v, cmd, ran.err))
            self.assertEqual(fake.forbidden(), [])

    def test_a_flag_its_help_lacks_is_a_usage_error(self):
        for v in VERSIONS:
            fake = self.fake(v)
            for words in (['exec', '--not-a-flag', '-'], ['-Z'], ['resume', '--json'], ['app-server', '--nope']):
                ran = self.run_fake(fake, *words)
                self.assertEqual(ran.code, 2, (v, words))
                self.assertTrue(ran.err.startswith("error: unexpected argument '"), ran.err)
                self.assertIn('Usage: codex', ran.err)
            self.assertEqual(fake.forbidden(), [])

    def test_the_newer_flags_are_refused_by_the_older_copy(self):
        old, new = self.fake('0.155.1'), self.fake('0.169.1')
        for words in (['--no-daemon', '--help'], ['exec', '--trust', '--help'],
                      ['exec', '--cyber-access-program', 'standard', '--help']):
            self.assertEqual(self.run_fake(old, *words).code, 2, words)
            self.assertEqual(self.run_fake(new, *words).code, 0, words)

    def test_a_missing_value_is_a_usage_error(self):
        for v in VERSIONS:
            fake = self.fake(v)
            ran = self.run_fake(fake, 'exec', '-C')
            self.assertEqual(ran.code, 2)
            self.assertIn("a value is required for '--cd <DIR>'", ran.err)
            ran = self.run_fake(fake, 'resume', 'aaaaaaaa-0000-4000-8000-00000000000a', 'words', 'third')
            self.assertEqual(ran.code, 2)

    def test_the_commands_switchboard_never_starts_exit_3_and_are_recorded(self):
        for v in VERSIONS:
            fake = self.fake(v)
            tries = [['login'], ['logout'], ['cloud'], ['remote-control'], ['app-server', 'daemon'],
                     ['app-server', 'proxy'], ['app-server', '--listen', 'ws://127.0.0.1:1']]
            if v == '0.169.1':
                tries.append(['app-server', '--startup-sign-out'])
            for words in tries:
                self.assertEqual(self.run_fake(fake, *words).code, 3, (v, words))
            self.assertEqual(len(fake.forbidden()), len(tries))

    def test_exec_reads_the_prompt_from_standard_input_and_writes_the_answer_file(self):
        for v in VERSIONS:
            fake = self.fake(v, exec={'answer': 'made-up answer\n'})
            out = os.path.join(self.root, 'answer-%s.txt' % v)
            argv = [a.format(folder=self.root, out=out) for a in codex_facts.JOB_ARGS]
            ran = self.run_fake(fake, *argv, stdin='a made-up job')
            self.assertEqual(ran.code, 0, ran.err)
            with open(out) as f:
                self.assertEqual(f.read(), 'made-up answer\n')
            self.assertIn({'stdin': 'a made-up job'}, fake.records())

    def test_it_records_environment_names_never_values(self):
        fake = self.fake('0.169.1')
        self.env['MADE_UP_NAME'] = 'made-up-value-0123'
        self.run_fake(fake, '--version')
        text = open(fake.record_path).read()
        self.assertIn('MADE_UP_NAME', text)
        self.assertNotIn('made-up-value-0123', text)


class TheAppServer(Base):

    def talk(self, fake, lines, reads):
        pipe = LinePipe([fake.path, 'app-server'], env=self.env, cwd=self.root)
        got = []
        try:
            for line in lines:
                pipe.send(line)
            for _ in range(reads):
                got.append(pipe.receive(10.0))
        finally:
            pipe.close(3.0)
        return [json.loads(x) for x in got if x is not None]

    def four_lines(self):
        m = CodexProvider.message
        return [m('initialize', 1, CodexProvider.init_params()), m('initialized'),
                m('account/read', 2, codex_facts.ASK_PARAMS['account/read']),
                m('account/rateLimits/read', 3, codex_facts.ASK_PARAMS['account/rateLimits/read'])]

    def test_the_four_lines_are_answered_in_the_schema_of_each_version(self):
        for v in VERSIONS:
            fake = self.fake(v)
            answers = self.talk(fake, self.four_lines(), 3)
            self.assertEqual([a['id'] for a in answers], [1, 2, 3])
            for a, method in zip(answers, ('initialize', 'account/read', 'account/rateLimits/read')):
                self.assertEqual(schema_errors(schema(v, 'JSONRPCResponse'), a), [])
                self.assertEqual(schema_errors(schema(v, RESULTS[method]), a['result']), [], (v, method))
            self.assertEqual(fake.violations(), [], v)
            self.assertEqual(fake.got(), self.four_lines())

    def test_nothing_before_initialized(self):
        for v in VERSIONS:
            fake = self.fake(v)
            m = CodexProvider.message
            answers = self.talk(fake, [m('account/read', 7, {'refreshToken': False}),
                                       m('initialize', 1, CodexProvider.init_params()),
                                       m('account/read', 8, {'refreshToken': False})], 3)
            self.assertEqual(answers[0], {'id': 7, 'error': {'code': -32600, 'message': 'Not initialized'}})
            self.assertEqual(answers[1]['id'], 1)
            self.assertEqual(answers[2]['error']['message'], 'Not initialized')
            for a in (answers[0], answers[2]):
                self.assertEqual(schema_errors(schema(v, 'JSONRPCError'), a), [])

    def test_a_second_initialize_and_another_method(self):
        fake = self.fake('0.169.1')
        m = CodexProvider.message
        lines = [m('initialize', 1, CodexProvider.init_params()), m('initialized'),
                 m('initialize', 2, CodexProvider.init_params()),
                 json.dumps({'method': 'thread/list', 'id': 3, 'params': {}})]
        answers = self.talk(fake, lines, 3)
        self.assertEqual(answers[1]['error']['message'], 'Already initialized')
        self.assertEqual(answers[2]['error']['code'], -32601)

    def test_it_can_send_a_server_request_and_a_notification_first(self):
        fake = self.fake('0.169.1', server_request_first=True, notify_first=True)
        answers = self.talk(fake, self.four_lines(), 5)
        self.assertEqual(answers[1]['method'], 'account/chatgptAuthTokens/refresh')
        self.assertEqual(answers[1]['id'], 900)
        self.assertEqual(schema_errors(schema('0.169.1', 'ChatgptAuthTokensRefreshParams'), answers[1]['params']), [])
        self.assertEqual(answers[3]['method'], 'account/rateLimits/updated')
        self.assertEqual(fake.violations(), [])


if __name__ == '__main__':
    unittest.main()
