import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# One read-only job (DESIGN 12.5): `codex exec` in its read-only mode, run on a stand-in fake-codex. The argument list
# is exact and holds no word of the job; the job goes in on standard input only; the program gets only the allow list
# of environment names; the answer is read from the one file the command made, and nothing else is written anywhere;
# a time-out ends the program and its child; a non-zero exit says the program's last words with secrets taken out; and
# Ctrl-C (KeyboardInterrupt) goes on to the command with the program and its child ended.
import os
import signal
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from fakes_codex import Rig
from fakes_providers import run_real
from switchboard.providers import codex_facts, contract, words
from switchboard.providers.contract import Ran
from switchboard.providers.run import child_env

KEY = 'made-up-key-0123456789'
CANARY = 'job-canary-8d1f'


def gone(pid, within=5.0):
    """True once pid is no longer a running process (a zombie of ours is reaped first)."""
    end = time.monotonic() + within
    while time.monotonic() < end:
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        time.sleep(0.05)
    return False


class Base(unittest.TestCase):

    def setUp(self, **reach_args):
        env = {'CODEX_API_KEY': KEY, 'DATABASE_URL': 'postgres://maya:made-up@db.example/x', 'GH_PAT': 'made-up-pat',
               'SENTRY_DSN': 'https://made-up@sentry.example/1', 'LC_ALL': 'en_US.UTF-8', 'TMPDIR': '/tmp/made-up/',
               'MADE_UP_PLAIN': 'not-for-a-job'}
        self.rig = Rig(self, env=env, **reach_args)
        self.fake = self.rig.app('ChatGPT.app', '0.169.1', exec={'answer': 'The made-up answer.\n'})
        self.folder = os.path.join(self.rig.root, 'Users', 'maya', 'site')
        os.makedirs(self.folder)
        notes = os.path.join(self.rig.root, 'notes', 't-0123abcd')
        os.makedirs(notes, mode=0o700)
        self.out = os.path.join(notes, 'h-0123abcd.answer.txt')
        fd = os.open(self.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)     # as handoff.answer_path makes it
        os.close(fd)
        self.prompt = (words.JOB_WORDS.format(folder=self.folder) +
                       words.JOB_GIVEN.format(job='review the made-up change %s' % CANARY))
        self.p = self.rig.provider
        self.p.detect()                 # the version probes (Switchboard's own cache) happen before the job

    def job(self, timeout=30.0):
        return self.p.job(self.folder, self.prompt, self.out, timeout)

    def exec_runs(self):
        return [(argv, stdin, env, cwd) for argv, stdin, env, cwd in self.rig.calls.runs if argv[1:2] == ('exec',)]


class AJob(Base):

    def test_argv_exact_and_no_job_word_in_it(self):
        self.job()
        (argv, stdin, env, cwd), = self.exec_runs()
        self.assertEqual(argv, (self.fake.path, 'exec', '-C', self.folder, '-s', 'read-only', '-c',
                                'approval_policy="never"', '--skip-git-repo-check', '--ephemeral', '--color', 'never',
                                '-o', self.out, '-'))
        self.assertEqual(cwd, self.folder)
        self.assertFalse(any(CANARY in a for a in argv))
        self.assertEqual(stdin, self.prompt)
        self.assertIn({'stdin': self.prompt}, self.fake.records())
        self.assertEqual(self.fake.forbidden(), [])
        self.assertEqual(self.fake.violations(), [])

    def test_the_environment_is_the_allow_list_only(self):
        self.job()
        (_, _, env, _), = self.exec_runs()
        self.assertEqual(env, child_env(self.rig.reach.env, job=True))
        for name in ('CODEX_API_KEY', 'DATABASE_URL', 'GH_PAT', 'SENTRY_DSN', 'MADE_UP_PLAIN'):
            self.assertNotIn(name, env)
        for name in ('HOME', 'PATH', 'LANG', 'LC_ALL', 'TMPDIR'):
            self.assertIn(name, env)
        start = [r for r in self.fake.records() if 'argv' in r and r['argv'][:1] == ['exec']][0]
        seen = [n for n in start['env'] if not n.startswith('__CF')]     # macOS adds its own text-encoding name
        for name in seen:
            self.assertTrue(name in contract.JOB_ENV_NAMES or name.startswith(contract.JOB_ENV_PREFIXES), name)

    def test_the_answer_comes_from_the_one_file_and_nothing_else_is_written(self):
        def tree():
            out = {}
            for top in (self.rig.home, self.rig.data, self.folder, os.path.dirname(self.out)):
                for folder, _, names in os.walk(top):
                    for name in names:
                        path = os.path.join(folder, name)
                        st = os.stat(path)
                        out[path] = (st.st_size, st.st_mtime_ns)
            return out
        before = tree()
        answer = self.job()
        after = tree()
        self.assertEqual(sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k)), [self.out])
        self.assertTrue(answer.ok)
        self.assertEqual((answer.text, answer.path, answer.code, answer.provider),
                         ('The made-up answer.\n', self.out, 0, 'codex'))
        self.assertGreater(answer.took_s, 0)
        self.assertNotIn('made-up answer', repr(answer))

    def test_a_long_answer_is_cut(self):
        self.fake.spec(exec={'answer': 'y' * (codex_facts.ANSWER_MAX + 50)})
        self.assertEqual(len(self.job().text), codex_facts.ANSWER_MAX)

    def test_an_empty_answer_is_no_answer(self):
        self.fake.spec(exec={'answer': ''})
        answer = self.job()
        self.assertFalse(answer.ok)
        self.assertTrue(answer.why.startswith('Codex stopped without an answer (exit 0).'))

    def test_a_non_zero_exit_says_its_last_words_without_a_secret(self):
        self.fake.spec(exec={'exit': 1, 'stderr': 'starting\nfatal: the key %s was refused\x1b[31m\n' % KEY})
        answer = self.job()
        self.assertFalse(answer.ok)
        self.assertEqual(answer.code, 1)
        self.assertEqual(answer.why, 'Codex stopped without an answer (exit 1). Its last words: fatal: the key '
                                     '[secret removed] was refused')
        self.assertNotIn(KEY, answer.why)

    def test_without_the_scrubber_its_words_are_left_out(self):
        class NoScrubber:
            def env_secrets(self, env):
                raise ImportError('not here')
        self.p.redact = NoScrubber()
        self.fake.spec(exec={'exit': 2, 'stderr': 'fatal: %s\n' % KEY})
        self.assertEqual(self.job().why, 'Codex stopped without an answer (exit 2).')

    def test_a_time_out_ends_the_program_and_its_child(self):
        self.fake.spec(exec={'sleep': 60, 'child': True, 'answer': 'late'})
        t = time.monotonic()
        # Six seconds, not one and a half: on a busy Mac the stand-in (a Python program at background priority) can
        # need seconds to start and to note its child, and a time-out before that proves nothing about the child.
        answer = self.job(timeout=6.0)
        self.assertLess(time.monotonic() - t, 30.0)
        self.assertFalse(answer.ok)
        self.assertIsNone(answer.code)
        self.assertEqual(answer.why, "Codex did not answer within 1 minute. It was started in Codex's read-only mode.")
        kids = [r['child'] for r in self.fake.records() if 'child' in r]
        self.assertEqual(len(kids), 1)
        self.assertTrue(gone(kids[0]))
        with open(self.out) as f:
            self.assertEqual(f.read(), '')

    def test_fifteen_minutes_is_said_so(self):
        self.assertEqual(self.p._span(900.0), '15 minutes')

    def test_too_old_runs_nothing(self):
        self.rig.chatfiles.newest = '0.170.0'
        with self.assertRaises(contract.ProviderError) as e:
            self.job()
        self.assertEqual(e.exception.kind, 'too_old')
        self.assertEqual(self.exec_runs(), [])


class Missing(Base):

    def setUp(self):
        super().setUp(run=self.run_or_missing)

    def run_or_missing(self, argv, stdin=None, timeout=30.0, env=None, cwd=None):
        if list(argv[1:2]) == ['exec']:
            return Ran(code=None, missing=True)
        return run_real(argv, stdin=stdin, timeout=timeout, env=env, cwd=cwd)

    def test_a_program_that_cannot_start(self):
        answer = self.job()
        self.assertEqual((answer.ok, answer.why), (False, codex_facts.JOB_MISSING))


class CtrlC(Base):
    """run.run_program (lane A) ends the program's process group on KeyboardInterrupt before the exception goes on;
    this run does the same, so the test proves the job lets Ctrl-C reach the command and leaves no program behind."""

    def setUp(self):
        super().setUp(run=self.run_until_interrupted)

    def run_until_interrupted(self, argv, stdin=None, timeout=30.0, env=None, cwd=None):
        import subprocess
        proc = subprocess.Popen(list(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env, cwd=cwd, start_new_session=True)
        try:
            out, err = proc.communicate((stdin or '').encode(), timeout=timeout)
        except KeyboardInterrupt:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(5)
            raise
        return Ran(code=proc.returncode, out=out.decode(), err=err.decode())

    def test_keyboard_interrupt_ends_the_child_and_goes_on(self):
        self.fake.spec(exec={'sleep': 60, 'child': True})

        def interrupt_when_started():
            end = time.monotonic() + 10
            while time.monotonic() < end and not [r for r in self.fake.records() if 'child' in r]:
                time.sleep(0.05)
            os.kill(os.getpid(), signal.SIGINT)
        threading.Thread(target=interrupt_when_started, daemon=True).start()
        with self.assertRaises(KeyboardInterrupt):
            self.job(timeout=30.0)
        kid = [r['child'] for r in self.fake.records() if 'child' in r][0]
        self.assertTrue(gone(kid))


if __name__ == '__main__':
    unittest.main()
