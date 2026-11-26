import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# switchboard hand PROVIDER [--chat NAME] [--] JOB (DESIGN 12.5, D7 narrowed, K6), word for word with its exit codes,
# on a Kit of stand-ins. The jobs that run are stand-in programs this test writes (fake-codex), never Codex.
#
# - Not at a terminal (an assistant's own shell tool) it prints the line to run and starts nothing, exit 0.
# - At a terminal: added; stay; too old; the folder's yes (the same yes as continue's: the hand question says so, and
#   with --chat the note is made first and the question is the one for a chat, with its size); the question about
#   OpenAI's terms, once per Mac; one job at a time (the lock held elsewhere is refused).
# - The job's words go to the adapter as the prompt (fixed words, the folder, the job; with --chat a note's path),
#   never in an argument list; its answer is printed through words.safe and kept in a file for 7 days.
# - The time limit, a program that could not start, a job that ends without an answer (its last words, one line):
#   the adapter's own sentence, said once; the History line tells the three apart by the adapter's answer. The same
#   over the real Codex adapter and a stand-in fake-codex. Ctrl-C, SIGTERM and SIGHUP each stop the stand-in child
#   (it is gone after) and exit 130 with "Stopped."; the signal handlers are put back.
# - {billing}: the plan when the numbers name one, else the app's own sign-in.
import os
import signal
import sys
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

from fakes_provider_kit import (CHAT_A, NOW, KitCase, chat, codex_found, codex_limits, codex_seat,  # noqa: E402
                                run_ending_group)
from fakes_providers import run_real, stand_in  # noqa: E402
from switchboard.providers import contract, words  # noqa: E402
from switchboard.providers.contract import Ran  # noqa: E402

QUESTION = ("Hand one job to Codex? Your words, and the files Codex reads, go to OpenAI. Read-only here means Codex "
            "cannot change\nfiles; it can still read files outside this folder. The same yes lets switchboard "
            "continue send this folder's\nchats to Codex later. {work}You are asked once for this folder (site).\n"
            "  y      yes, for this folder from now on\n  n      not now\n  never  never from this folder\n")
TERMS = ("switchboard hand runs Codex's exec mode on your ChatGPT plan. Switchboard makes no claim either way about\n"
         "what OpenAI's terms allow: read them first (openai.com/policies/terms-of-use). Go on? [y/N] ")
ON_IT = 'Codex is on it: read-only, up to 15 minutes. Ctrl-C stops it.'


class Base(KitCase):
    def rig(self, ready=True, **kw):
        """ready: the folder's yes and the terms question already answered."""
        r = super().rig(**kw)
        r.codex.job_answer = 'The footer is fine.'
        if ready:
            r.choices.set_consent(r.folder, 'codex', 'yes', NOW - 60)
            r.choices.set_hand_ack(NOW - 60, 'codex')
        return r

    def job_program(self, body):
        return stand_in(os.path.join(self.root, 'jobs'), 'fake-codex', body)

    def prompt(self, r, job, note=None):
        text = words.JOB_WORDS.format(folder=r.folder)
        if note is not None:
            text += words.JOB_BACKGROUND.format(path=note.path)
        return text + words.JOB_GIVEN.format(job=job)


class NotAtATerminal(Base, unittest.TestCase):
    def test_it_prints_the_line_and_runs_nothing(self):
        r = self.rig(ready=False)
        self.assertEqual(r('hand', 'codex', '--', 'review the footer; then say why'), 0)
        self.assertEqual(r.lines(), ['To hand this job to Codex, run this in a terminal:',
                                     "  switchboard hand codex -- 'review the footer; then say why'"])
        self.assertEqual((r.asked, r.run.locks, r.tasks.all, r.said), ([], [], [], []))
        self.assertFalse(any(isinstance(c, tuple) and c[0] == 'job' for c in r.codex.calls))

    def test_even_with_every_yes_given(self):
        r = self.rig()
        self.assertEqual(r('hand', 'codex', '--chat', 'Fix it', 'review', 'it'), 0)
        self.assertEqual(r.lines()[1], "  switchboard hand codex --chat 'Fix it' -- 'review it'")
        self.assertFalse(any(isinstance(c, tuple) for c in r.codex.calls))

    def test_claude_takes_no_job(self):
        for tty in (False, True):
            r = self.rig(tty=tty)
            self.assertEqual(r('hand', 'claude', '--', 'review it'), 1)
            self.assertEqual(r.text(), 'Handing a job to Claude is not in this version.\n')

    def test_wrong_words(self):
        for args in (['hand'], ['hand', 'codex'], ['hand', 'codex', '--'], ['hand', 'codex', '--chat'],
                     ['hand', 'codex', '--chat', 'x']):
            with self.subTest(args=args):
                r = self.rig()
                self.assertEqual(r(*args), 2)
                self.assertEqual(r.text(), words.HELP['hand'] + '\n')
        r = self.rig()
        self.assertEqual(r('hand', 'gemini', '--', 'x'), 2)
        self.assertTrue(r.text().startswith('Switchboard does not know an assistant called "gemini".'))


class Questions(Base, unittest.TestCase):
    def test_the_hand_question_then_the_terms_once(self):
        r = self.rig(ready=False, tty=True, answers=['y', 'y'])
        self.assertEqual(r('hand', 'codex', '--', 'review the footer'), 0)
        self.assertEqual(r.asked, ['> ', TERMS])
        self.assertTrue(r.text().startswith(QUESTION.format(work='')))
        self.assertEqual(r.choices.consent(r.folder, 'codex'), 'yes')
        self.assertTrue(r.choices.hand_ack('codex'))
        self.assertEqual(r.said[0], ('Chats in site may go to Codex', 'setting', {'to': 'Codex'}))
        r.answers = []
        self.assertEqual(r('hand', 'codex', '--', 'again'), 0)          # no question the second time
        self.assertEqual(r.asked, ['> ', TERMS])

    def test_with_a_chat_the_note_is_made_first_and_the_chat_question_asked(self):
        seen = {}

        def look(prompt):
            seen['notes'] = list(r.handoff.notes)
            return 'y'
        r = self.rig(ready=False, tty=True, answers=[look, 'y'],
                     claude_chats=(lambda f: chat('claude', f, cid=CHAT_A, name='Fix it'),))
        self.assertEqual(r('hand', 'codex', '--chat', 'fix it', '--', 'review'), 0)
        note = r.handoff.notes[0]
        self.assertEqual(seen['notes'], [note])
        self.assertEqual(r.asked, ['> ', TERMS])
        text = r.text()
        self.assertIn('Making the note from `Fix it` ...\nCarry this chat on in Codex? A note of it goes to OpenAI',
                      text)
        self.assertIn('(%s, 2 taken out).' % words.size(note.bytes), text)
        self.assertNotIn('Hand one job to Codex?', text)
        prompt = [c for c in r.codex.calls if isinstance(c, tuple)][0][2]
        self.assertEqual(prompt, self.prompt(r, 'review', note))
        self.assertEqual(r.choices.consent(r.folder, 'codex'), 'yes')

    def test_with_a_chat_no_removes_the_note(self):
        for answers in (['n'], ['y', 'n']):
            with self.subTest(answers=answers):
                r = self.rig(ready=False, tty=True, answers=list(answers),
                             claude_chats=(lambda f: chat('claude', f, cid=CHAT_A, name='Fix it'),))
                self.assertEqual(r('hand', 'codex', '--chat', 'fix it', '--', 'review'), 1)
                note = r.handoff.notes[0]
                self.assertEqual(r.lines()[-1], 'Nothing was sent; the note was removed.')
                self.assertFalse(os.path.exists(note.path))
                self.assertFalse(any(isinstance(c, tuple) for c in r.codex.calls))
                self.assertEqual(r.tasks.all, [])

    def test_a_yes_given_for_continue_counts(self):
        r = self.rig(ready=False, tty=True, answers=['y'])
        r.choices.set_consent(r.folder, 'codex', 'yes', NOW - 60)
        self.assertEqual(r('hand', 'codex', '--', 'review'), 0)
        self.assertEqual(r.asked, [TERMS])

    def test_no_and_never(self):
        r = self.rig(ready=False, tty=True, answers=['n'])
        self.assertEqual(r('hand', 'codex', '--', 'review'), 1)
        self.assertEqual(r.lines()[-1], 'Nothing was sent.')
        self.assertIsNone(r.choices.consent(r.folder, 'codex'))
        r = self.rig(ready=False, tty=True, answers=['never'])
        self.assertEqual(r('hand', 'codex', '--', 'review'), 1)
        self.assertEqual(r.choices.consent(r.folder, 'codex'), 'never')
        self.assertEqual(r.said, [('Chats in site never go to Codex', 'setting', {'to': 'Codex'})])
        self.assertFalse(any(isinstance(c, tuple) for c in r.codex.calls))

    def test_no_to_the_terms(self):
        r = self.rig(ready=False, tty=True, answers=['y', ''])
        self.assertEqual(r('hand', 'codex', '--', 'review'), 1)
        self.assertEqual(r.lines()[-1], 'Nothing was sent.')
        self.assertFalse(r.choices.hand_ack('codex'))
        self.assertFalse(any(isinstance(c, tuple) for c in r.codex.calls))

    def test_a_work_plan_is_named(self):
        r = self.rig(ready=False, tty=True, answers=['n'])
        r.codex.limits_ = codex_limits(plan='edu')
        r('hand', 'codex', '--', 'review')
        self.assertIn(QUESTION.format(work="This is a work plan: your employer's terms apply. "), r.text())

    def test_refusals_before_any_question(self):
        r = self.rig(ready=False, tty=True, codex_added=False)
        self.assertEqual(r('hand', 'codex', '--', 'x'), 1)
        self.assertEqual(r.text(), 'Codex is not added. switchboard add codex adds it.\n')
        r = self.rig(ready=False, tty=True)
        r.choices.set_stay(r.folder, 'claude', NOW)
        self.assertEqual(r('hand', 'codex', '--', 'x'), 1)
        self.assertEqual(r.text(), 'This folder stays on Claude. switchboard stay off lifts that.\n')
        r = self.rig(ready=False, tty=True)
        r.choices.set_consent(r.folder, 'codex', 'never', NOW)
        self.assertEqual(r('hand', 'codex', '--', 'x'), 1)
        self.assertEqual(r.text(), "You said this folder's chats never go to Codex. switchboard allow codex lifts "
                                   "that.\n")
        self.assertEqual(r.asked, [])

    def test_too_old(self):
        r = self.rig(tty=True, codex_found_=codex_found(self.root, version='0.164.0', seen='0.169.1'))
        self.assertEqual(r('hand', 'codex', '--', 'x'), 1)
        self.assertEqual(r.lines()[-1], 'The newest Codex here (0.164.0) is older than the one that wrote your Codex '
                                        'chats (0.169.1). Update Codex, then run this again.')

    def test_one_job_at_a_time(self):
        r = self.rig(tty=True)
        r.run.held_elsewhere = True
        self.assertEqual(r('hand', 'codex', '--', 'x'), 1)
        self.assertEqual(r.text(), 'Another job is running for Codex now; wait for it, or stop it with Ctrl-C there.'
                                   '\n')
        self.assertEqual(r.run.locks, [contract.HAND_LOCK_NAME])
        self.assertFalse(any(isinstance(c, tuple) for c in r.codex.calls))


class TheJob(Base, unittest.TestCase):
    def test_the_answer(self):
        r = self.rig(tty=True)
        self.assertEqual(r('hand', 'codex', '--', 'review the footer --help'), 0)
        job = [c for c in r.codex.calls if isinstance(c, tuple)]
        self.assertEqual(len(job), 1)
        _, folder, prompt, out, timeout = job[0]
        self.assertEqual((folder, prompt, timeout), (r.folder, self.prompt(r, 'review the footer --help'), 900.0))
        self.assertTrue(out.startswith(r.run.notes_root(r.reach) + os.sep) and out.endswith('.answer.txt'), out)
        self.assertEqual(r.lines(), [ON_IT, "Codex's answer (42 s, read-only, on your ChatGPT Pro plan):", '',
                                     'The footer is fine.', '', 'Kept in %s for 7 days.' % out])
        hop = r.tasks.all[0]
        self.assertEqual((hop.kind, hop.to_provider, hop.state), ('hand', 'codex', 'answered'))
        self.assertEqual(r.said, [('Handed one job to Codex in site: answered in 42 s', 'info',
                                   {'to': 'Codex', 'reason': 'hand'})])
        self.assertEqual(r.run.locks, [contract.HAND_LOCK_NAME])

    def test_with_a_note_of_a_chat(self):
        r = self.rig(tty=True, claude_chats=(lambda f: chat('claude', f, cid=CHAT_A, name='Fix it'),))
        self.assertEqual(r('hand', 'codex', '--chat', 'fix it', '--', 'review'), 0)
        note = r.handoff.notes[0]
        self.assertEqual(r.handoff.made[0]['budget'], 48000)
        prompt = [c for c in r.codex.calls if isinstance(c, tuple)][0][2]
        self.assertEqual(prompt, self.prompt(r, 'review', note))
        self.assertEqual(r.tasks.all[0].from_chat, CHAT_A)

    def test_the_answer_never_prints_an_escape(self):
        r = self.rig(tty=True)
        r.codex.job_answer = 'all \x1b]52;c;bWFkZQ==\x07good\x1b[2J\x0d\x0adone'
        self.assertEqual(r('hand', 'codex', '--', 'x'), 0)
        self.assertNotIn('\x1b', r.text())
        self.assertNotIn('\r', r.text())
        self.assertIn('all good\ndone\n', r.text())

    def test_billing_both_ways(self):
        cases = ((codex_limits(), codex_seat(), 'on your ChatGPT Pro plan'),
                 (codex_limits(plan='edu_plus'), codex_seat(label=words.PLAN_OTHER, plan='edu_plus'),
                  'on your ChatGPT plan'),
                 ((), codex_seat(), "through Codex's own sign-in"),
                 (codex_limits(plan=None, ask=contract.ASK_API_KEY), codex_seat(), "through Codex's own sign-in"))
        for limits, seats, billing in cases:
            with self.subTest(billing=billing, limits=limits):
                r = self.rig(tty=True)
                r.codex.limits_, r.codex.seats_ = limits, seats
                self.assertEqual(r('hand', 'codex', '--', 'x'), 0)
                self.assertEqual(r.lines()[1], "Codex's answer (42 s, read-only, %s):" % billing)

    def test_the_time_limit(self):
        r = self.rig(tty=True)
        r.codex.job_program = self.job_program('import time\ntime.sleep(30)\n')
        r.codex.job_timeout = 1.0
        start = time.monotonic()
        self.assertEqual(r('hand', 'codex', '--', 'x'), 1)
        self.assertLess(time.monotonic() - start, 10)
        self.assertEqual(r.lines()[-1], "Codex did not answer within 15 minutes. It was started in Codex's read-only "
                                        "mode.")
        self.assertEqual(r.tasks.all[0].state, 'failed')
        self.assertEqual(r.said[-1], ('Handed one job to Codex in site: no answer (time limit)', 'info',
                                      {'to': 'Codex', 'reason': 'hand'}))

    def test_ended_without_an_answer(self):
        r = self.rig(tty=True)
        r.codex.job_program = self.job_program(
            'import sys\nsys.stderr.write("reading the folder\\nerror: \\x1b[31mno model\\x1b[0m here\\n")\n'
            'sys.exit(3)\n')
        self.assertEqual(r('hand', 'codex', '--', 'x'), 1)
        self.assertEqual(r.lines()[-1], 'Codex stopped without an answer (exit 3). Its last words: error: no model '
                                        'here')
        self.assertEqual(r.text().count('stopped without an answer'), 1)
        self.assertEqual(r.said[-1][0], 'Handed one job to Codex in site: no answer (exit 3)')

    def test_a_program_that_could_not_start(self):
        r = self.rig(tty=True)
        r.codex.job_program = os.path.join(self.root, 'jobs', 'not-there')
        self.assertEqual(r('hand', 'codex', '--', 'x'), 1)
        self.assertEqual(r.lines()[-1], 'Codex could not be started.')
        self.assertNotIn('did not answer within', r.text())
        self.assertEqual(r.said[-1], ('Handed one job to Codex in site: no answer (could not start)', 'info',
                                      {'to': 'Codex', 'reason': 'hand'}))

    def test_an_adapter_that_gives_no_reason(self):
        r = self.rig(tty=True)
        for answer, line, why in ((contract.Answer('codex', False, code=4), 'Codex stopped without an answer (exit 4).',
                                   'exit 4'),
                                  (contract.Answer('codex', False, took_s=9.0), "Codex did not answer within 15 "
                                   "minutes. It was started in Codex's read-only mode.", 'time limit'),
                                  (contract.Answer('codex', False), 'Codex could not be started.', 'could not start')):
            with self.subTest(why=why):
                r.codex.job = lambda folder, prompt, out, timeout, a=answer: a
                self.assertEqual(r('hand', 'codex', '--', 'x'), 1)
                self.assertEqual(r.lines()[-1], line)
                self.assertEqual(r.said[-1][0], 'Handed one job to Codex in site: no answer (%s)' % why)


class OverTheRealCodex(Base, unittest.TestCase):
    """hand over the real Codex adapter (switchboard/providers/codex.py) and a stand-in fake-codex in a made-up
    ChatGPT.app: what it prints is the adapter's own sentence, once, and the History line says which ending it was."""

    def real(self, run=None, **exec_spec):
        from fakes_codex import Rig as CodexRig
        crig = CodexRig(self, run=run) if run else CodexRig(self)
        crig.facts['probe_seconds'] = 60.0          # a busy Mac can take seconds to start the stand-in
        self.fake = crig.app('ChatGPT.app', '0.169.1', exec=exec_spec)
        self.assertIsNotNone(crig.provider.detect().program)     # its version asked once, before the command
        r = self.rig(tty=True)
        r.codex = r.providers['codex'] = crig.provider
        return r

    def test_an_answer(self):
        r = self.real(answer='The made-up answer.\n')
        self.assertEqual(r('hand', 'codex', '--', 'review it'), 0)
        self.assertEqual(r.lines()[2:4], ['', 'The made-up answer.'])
        self.assertTrue(r.said[-1][0].startswith('Handed one job to Codex in site: answered in '))

    def test_exit_3_with_its_last_words(self):
        r = self.real(exit=3, stderr='reading\nerror: no model here\n')
        self.assertEqual(r('hand', 'codex', '--', 'x'), 1)
        self.assertEqual(r.lines()[-1], 'Codex stopped without an answer (exit 3). Its last words: error: no model '
                                        'here')
        self.assertEqual(r.text().count('stopped without an answer'), 1)
        self.assertEqual(r.said[-1][0], 'Handed one job to Codex in site: no answer (exit 3)')

    def test_the_time_limit(self):
        r = self.real(sleep=30, answer='late')
        with mock.patch.object(words, 'HAND_TIMEOUT_S', 1.5):
            self.assertEqual(r('hand', 'codex', '--', 'x'), 1)
        self.assertEqual(r.lines()[-1], "Codex did not answer within 1 minute. It was started in Codex's read-only "
                                        "mode.")
        self.assertEqual(r.said[-1][0], 'Handed one job to Codex in site: no answer (time limit)')

    def test_a_missing_program(self):
        def run(argv, stdin=None, timeout=30.0, env=None, cwd=None):
            if list(argv[1:2]) == ['exec']:
                return Ran(code=None, missing=True)
            return run_real(argv, stdin=stdin, timeout=timeout, env=env, cwd=cwd)
        r = self.real(run=run, answer='never written')
        self.assertEqual(r('hand', 'codex', '--', 'x'), 1)
        self.assertEqual(r.lines()[-1], 'Codex could not be started.')
        self.assertNotIn('did not answer within', r.text())
        self.assertEqual(r.said[-1][0], 'Handed one job to Codex in site: no answer (could not start)')


class Stopping(Base, unittest.TestCase):
    """The stand-in job writes its pid, then sends this process the signal, then waits. The test's own handlers for
    SIGTERM and SIGHUP stand in place while hand runs, so a hand that failed to set its own fails the test instead
    of ending the run."""

    def setUp(self):
        super().setUp()
        self.got = []
        for sig in (signal.SIGTERM, signal.SIGHUP):
            old = signal.signal(sig, self._mine)
            self.addCleanup(signal.signal, sig, old)

    def _mine(self, signum, frame):
        self.got.append(signum)

    def stop_with(self, sig):
        r = self.rig(tty=True, run=run_ending_group)
        r.codex.job_program = self.job_program(
            'import os, sys, time\n'
            'with open(sys.argv[1] + ".pid", "w") as f:\n'
            '    f.write(str(os.getpid()))\n'
            'os.kill(os.getppid(), %d)\n'
            'time.sleep(30)\n' % sig)
        start = time.monotonic()
        code = r('hand', 'codex', '--', 'x')
        took = time.monotonic() - start
        out = [c for c in r.codex.calls if isinstance(c, tuple)][0][3]
        with open(out + '.pid') as f:
            pid = int(f.read())
        return r, code, took, pid

    def assert_stopped(self, r, code, took, pid):
        self.assertEqual(code, 130)
        self.assertLess(took, 15)
        self.assertEqual(r.lines()[-1], 'Stopped.')
        with self.assertRaises(ProcessLookupError):         # the child is gone
            os.kill(pid, 0)
        self.assertEqual(r.tasks.all[0].state, 'failed')
        self.assertEqual(r.said[-1], ('Handed one job to Codex in site: no answer (stopped)', 'info',
                                      {'to': 'Codex', 'reason': 'hand'}))
        self.assertEqual(self.got, [])                       # hand's own handlers took it
        self.assertEqual(signal.getsignal(signal.SIGTERM), self._mine)      # and were put back
        self.assertEqual(signal.getsignal(signal.SIGHUP), self._mine)

    def test_sigterm(self):
        self.assert_stopped(*self.stop_with(signal.SIGTERM))

    def test_sighup(self):
        self.assert_stopped(*self.stop_with(signal.SIGHUP))

    def test_ctrl_c(self):
        self.assert_stopped(*self.stop_with(signal.SIGINT))


if __name__ == '__main__':
    unittest.main()
