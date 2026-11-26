import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Old notes are removed by someone (lane D second pass, D7): continue, back and hand call handoff.prune(reach, now)
# and tasks.prune(now) once each, after the move is recorded (before an exec, which never comes back), each inside its
# own guard. A prune that raises changes neither the exit code nor a word of the output, and a prune prints nothing.
# A command that stops before any move is recorded prunes nothing. On a Kit of stand-ins.
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

from fakes_provider_kit import CHAT_A, CHAT_B, NOW, KitCase, chat  # noqa: E402
from switchboard.providers import contract, words  # noqa: E402
from switchboard.providers.contract import Event, Hop, ReadInfo, Transcript  # noqa: E402

HOP = 'h-0000000a'


def left():
    return lambda folder: chat('claude', folder, cid=CHAT_A, ago=4000.0)


def away():
    return lambda folder: chat('codex', folder, cid=CHAT_B, name='Ship it', ago=60.0, started_ago=3500.0)


class Base(KitCase):
    def watch(self, r, fail=False):
        """Record each prune with the ledger as it was then; with fail, each raises after it is recorded."""
        seen = []

        def handoff_prune(reach, now):
            seen.append(('handoff', reach is r.reach, now, [(h.kind, h.state) for h in r.tasks.all]))
            if fail:
                raise RuntimeError('made-up failure in handoff.prune')
            return 0

        def tasks_prune(now):
            seen.append(('tasks', now, [(h.kind, h.state) for h in r.tasks.all]))
            if fail:
                raise OSError(28, 'made-up full disk in tasks.prune')
            return 0

        r.handoff.prune = handoff_prune
        r.tasks.prune = tasks_prune
        r.tasks.new_hop_id = lambda: 'h-0000000f'
        return seen

    def continue_rig(self, **kw):
        r = self.rig(claude_chats=(lambda f: chat('claude', f),), **kw)
        r.choices.set_consent(r.folder, 'codex', 'yes', NOW - 3600)
        return r

    def back_rig(self, **kw):
        r = self.rig(claude_chats=(left(),), codex_chats=(away(),), **kw)
        r.tasks.add_hop(Hop(id=HOP, task='t-0000000a', kind='continue', at=NOW - 3600, folder=r.folder,
                            from_provider='claude', from_chat=CHAT_A, to_provider='codex', to_chat=CHAT_B,
                            state='started'))
        r.codex.transcripts[CHAT_B] = Transcript(chat('codex', r.folder, cid=CHAT_B), (
            Event(contract.ASSISTANT, 'Changed the footer.'),), ReadInfo(reader='fake'))
        return r

    def hand_rig(self, **kw):
        r = self.rig(claude_chats=(), tty=True, **kw)
        r.choices.set_consent(r.folder, 'codex', 'yes', NOW - 3600)
        r.choices.set_hand_ack(NOW - 3600, 'codex')
        r.codex.job_answer = 'The footer is in site/footer.html.'
        return r


class EachCommandPrunesOnce(Base, unittest.TestCase):
    def test_continue_printed(self):
        r = self.continue_rig()
        seen = self.watch(r)
        self.assertEqual(r('continue'), 0)
        self.assertEqual(seen, [('handoff', True, NOW, [('continue', 'printed')]),
                                ('tasks', NOW, [('continue', 'printed')])])

    def test_continue_in_a_terminal_prunes_before_the_exec(self):
        at_exec = []

        def execve(path, argv, env, cwd):
            at_exec.append(list(seen))

        r = self.continue_rig(tty=True, execve=execve)
        seen = self.watch(r)
        self.assertEqual(r('continue'), 0)
        self.assertEqual(at_exec, [[('handoff', True, NOW, [('continue', 'started')]),
                                    ('tasks', NOW, [('continue', 'started')])]])
        self.assertEqual(len(seen), 2)

    def test_continue_whose_exec_fails_prunes_once(self):
        def execve(path, argv, env, cwd):
            raise FileNotFoundError(2, 'No such file or directory')

        r = self.continue_rig(tty=True, execve=execve)
        seen = self.watch(r)
        self.assertEqual(r('continue'), 1)
        self.assertEqual([s[0] for s in seen], ['handoff', 'tasks'])

    def test_back_printed(self):
        r = self.back_rig()
        seen = self.watch(r)
        self.assertEqual(r('back'), 0)
        self.assertEqual(seen, [('handoff', True, NOW, [('continue', 'returned'), ('back', 'printed')]),
                                ('tasks', NOW, [('continue', 'returned'), ('back', 'printed')])])

    def test_back_in_a_terminal_prunes_before_the_exec(self):
        at_exec = []

        def execve(path, argv, env, cwd):
            at_exec.append([s[0] for s in seen])

        r = self.back_rig(tty=True, answers=['y'], execve=execve)
        seen = self.watch(r)
        self.assertEqual(r('back'), 0)
        self.assertEqual((at_exec, [s[0] for s in seen]), ([['handoff', 'tasks']], ['handoff', 'tasks']))

    def test_hand(self):
        r = self.hand_rig()
        seen = self.watch(r)
        self.assertEqual(r('hand', 'codex', '--', 'Where is the footer?'), 0)
        self.assertEqual(seen, [('handoff', True, NOW, [('hand', 'answered')]),
                                ('tasks', NOW, [('hand', 'answered')])])

    def test_a_refusal_before_any_move_prunes_nothing(self):
        r = self.rig(claude_chats=())
        r.choices.set_consent(r.folder, 'codex', 'yes', NOW - 3600)
        seen = self.watch(r)
        self.assertEqual(r('continue'), 1)
        r2 = self.rig(claude_chats=(), codex_chats=())
        seen2 = self.watch(r2)
        self.assertEqual(r2('back'), 1)
        r3 = self.rig(claude_chats=())
        seen3 = self.watch(r3)
        self.assertEqual(r3('hand', 'codex', '--', 'Where is the footer?'), 0)       # not at a terminal: the line
        self.assertEqual((seen, seen2, seen3), ([], [], []))


class APruneThatRaises(Base, unittest.TestCase):
    def same(self, make, *args):
        """The command twice on the same made-up Mac: once with prunes that work, once with prunes that raise."""
        r = make()
        self.watch(r)
        code = r(*args)
        r2 = make()
        seen = self.watch(r2, fail=True)
        self.assertEqual((r2(*args), r2.text(), r2.said), (code, r.text(), r.said))
        self.assertEqual([s[0] for s in seen], ['handoff', 'tasks'])      # the second runs when the first raised
        return r2

    def test_continue(self):
        self.same(self.continue_rig, 'continue')

    def test_continue_in_a_terminal(self):
        r2 = self.same(lambda: self.continue_rig(tty=True), 'continue')
        self.assertEqual(len(r2.calls.execs), 1)

    def test_back(self):
        self.same(self.back_rig, 'back')

    def test_hand(self):
        self.same(self.hand_rig, 'hand', 'codex', '--', 'Where is the footer?')

    def test_a_prune_prints_nothing(self):
        r = self.continue_rig()
        self.watch(r, fail=True)
        r('continue')
        self.assertNotIn('made-up', r.text())
        self.assertNotIn('prune', r.text())
        self.assertEqual(r.lines()[-1][:2], '  ')
        self.assertTrue(r.lines()[-2].startswith(words.PRINTED_RUN.format(to='Codex')[:20]))


if __name__ == '__main__':
    unittest.main()
