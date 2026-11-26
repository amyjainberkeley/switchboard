import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The ledger of tasks and hops (switchboard/providers/tasks.py, DESIGN 14): ids, one task across a chain of moves,
# the open move in a folder, the cap, pruning, and two processes writing at once. Chats and folders are made up.
import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core
from switchboard.providers import contract, tasks
from switchboard.providers.contract import Hop
import fakes_providers as fp

F = '/Users/maya/site'
G = '/Users/maya/other'
A = 'aaaaaaaa-0000-4000-8000-00000000000a'
B = 'bbbbbbbb-0000-4000-8000-00000000000b'
C = 'cccccccc-0000-4000-8000-00000000000c'
NOW = 1795600000.0


def hop(task, kind='continue', at=NOW, folder=F, frm='claude', frm_chat=A, to='codex', **more):
    return Hop(id=tasks.new_hop_id(), task=task, kind=kind, at=at, folder=folder, from_provider=frm,
               from_chat=frm_chat, to_provider=to, **more)


class Base(unittest.TestCase):
    def setUp(self):
        self.path = core.data_path(contract.LEDGER_FILE)
        if os.path.exists(self.path):
            os.remove(self.path)

    def saved(self):
        with open(self.path) as f:
            return json.load(f)


class Ids(Base):
    def test_shapes(self):
        seen = {tasks.new_hop_id() for _ in range(200)}
        self.assertEqual(len(seen), 200)
        self.assertTrue(all(tasks.HOP_ID_RE.match(h) for h in seen))
        t = tasks.task_for(F, 'claude', A)
        self.assertTrue(tasks.TASK_ID_RE.match(t))
        self.assertRegex(t, r'^t-[0-9a-f]{8}$')

    def test_the_same_chat_is_the_same_task_and_another_is_not(self):
        t = tasks.task_for(F, 'claude', A)
        self.assertEqual(tasks.task_for(F, 'claude', A), t)
        self.assertNotEqual(tasks.task_for(F, 'claude', B), t)
        self.assertNotEqual(tasks.task_for(F, 'codex', A), t)      # a chat is known by its provider and id

    def test_the_file_is_owner_only_and_holds_no_chat_text(self):
        t = tasks.task_for(F, 'claude', A)
        tasks.add_hop(hop(t, handoff='/x/.switchboard-notes/%s/h-00000000.md' % t, sha256='0' * 64, bytes=10))
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        data = self.saved()
        self.assertEqual(data['v'], 1)
        self.assertEqual(set(data['tasks'][t]), {'id', 'folder', 'made_at', 'origin'})
        self.assertEqual(data['tasks'][t]['origin'], {'provider': 'claude', 'chat': A})
        self.assertEqual(set(data['hops'][0]), {f for f in Hop.__dataclass_fields__})


class Chain(Base):
    def test_continue_then_back_then_continue_is_one_task(self):
        t = tasks.task_for(F, 'claude', A)
        h1 = tasks.add_hop(hop(t))
        tasks.update_hop(h1.id, to_chat=B, state='started')
        self.assertEqual(tasks.task_for(F, 'codex', B), t)          # the chat a hop led to
        h2 = tasks.add_hop(hop(t, kind='back', at=NOW + 10, frm='codex', frm_chat=B, to='claude', to_chat=A))
        tasks.update_hop(h1.id, state='returned')
        self.assertEqual(tasks.task_for(F, 'claude', A), t)
        h3 = tasks.add_hop(hop(t, at=NOW + 20))
        self.assertEqual(len(self.saved()['tasks']), 1)
        self.assertEqual([h.id for h in tasks.hops(F)], [h3.id, h2.id, h1.id])

    def test_a_chat_a_hop_left_from_belongs_to_that_task(self):
        t = tasks.task_for(F, 'claude', A)
        tasks.add_hop(hop(t, frm='claude', frm_chat=A))
        data = self.saved()
        data['tasks'][t]['origin'] = {'provider': 'claude', 'chat': C}      # an older origin
        core.write_json(self.path, data)
        self.assertEqual(tasks.task_for(F, 'claude', A), t)

    def test_a_hop_for_a_task_the_ledger_lacks_makes_it(self):
        h = tasks.add_hop(hop('t-0000000a'))
        self.assertEqual(self.saved()['tasks']['t-0000000a']['origin'], {'provider': 'claude', 'chat': A})
        self.assertEqual(h.task, 't-0000000a')


class OpenMoves(Base):
    def test_the_newest_open_continue_in_this_folder(self):
        t = tasks.task_for(F, 'claude', A)
        h1 = tasks.add_hop(hop(t, at=NOW, to_chat=B, state='started'))
        h2 = tasks.add_hop(hop(t, at=NOW + 5, to_chat=C, state='in_app'))
        tasks.add_hop(hop(t, kind='hand', at=NOW + 6, state='answered'))
        tasks.add_hop(hop(tasks.task_for(G, 'claude', B), folder=G, at=NOW + 7))
        self.assertEqual(tasks.open_continue(F).id, h2.id)
        self.assertEqual(tasks.open_continue(F, to_chat=B).id, h1.id)
        self.assertIsNone(tasks.open_continue(F, to_chat=A))
        tasks.update_hop(h2.id, state='returned')
        self.assertEqual(tasks.open_continue(F).id, h1.id)
        tasks.update_hop(h1.id, state='failed')
        self.assertIsNone(tasks.open_continue(F))

    def test_continued_from_the_chat_that_was_left(self):
        t = tasks.task_for(F, 'claude', A)
        self.assertIsNone(tasks.continued_from(F, A))
        h = tasks.add_hop(hop(t, frm_chat=A, state='printed'))
        tasks.add_hop(hop(t, frm_chat=B, at=NOW + 1))
        self.assertEqual(tasks.continued_from(F, A).id, h.id)
        self.assertIsNone(tasks.continued_from(G, A))
        tasks.update_hop(h.id, state='returned')
        self.assertIsNone(tasks.continued_from(F, A))

    def test_hops_newest_first_with_a_limit_and_a_folder(self):
        t = tasks.task_for(F, 'claude', A)
        made = [tasks.add_hop(hop(t, at=NOW + i, folder=F if i % 2 else G)) for i in range(8)]
        self.assertEqual([h.id for h in tasks.hops(limit=3)], [made[7].id, made[6].id, made[5].id])
        self.assertEqual([h.id for h in tasks.hops(F)], [made[i].id for i in (7, 5, 3, 1)])


class Update(Base):
    def test_only_a_hops_own_fields_change(self):
        t = tasks.task_for(F, 'claude', A)
        h = tasks.add_hop(hop(t))
        got = tasks.update_hop(h.id, state='printed', bytes=123, removed=4, program_version='0.169.1')
        self.assertEqual((got.state, got.bytes, got.removed, got.program_version), ('printed', 123, 4, '0.169.1'))
        self.assertEqual(tasks.hops()[0], got)
        with self.assertRaises(ValueError):
            tasks.update_hop(h.id, text='a chat word')
        with self.assertRaises(ValueError):
            tasks.update_hop(h.id, id='h-00000000')
        with self.assertRaises(ValueError):
            tasks.update_hop(h.id, state='lost')
        with self.assertRaises(contract.ProviderError):
            tasks.update_hop('h-ffffffff', state='printed')

    def test_a_hop_of_another_shape_is_refused(self):
        t = tasks.task_for(F, 'claude', A)
        for bad in (hop(t, kind='teleport'), hop(t, state='lost'), hop('task-1'),
                    Hop(id='h-1', task=t, kind='continue', at=NOW, folder=F, from_provider='claude', from_chat=A,
                        to_provider='codex')):
            with self.assertRaises(ValueError):
                tasks.add_hop(bad)
        h = tasks.add_hop(hop(t))
        with self.assertRaises(ValueError):
            tasks.add_hop(h)                                       # the same id twice


class CapAndPrune(Base):
    def test_at_most_500_hops_the_oldest_go_first(self):
        t = tasks.task_for(F, 'claude', A)
        data = self.saved()
        data['hops'] = [dict(hop(t, at=NOW + i).__dict__) for i in range(500)]
        core.write_json(self.path, data)
        first = data['hops'][0]['id']
        for i in range(5):
            tasks.add_hop(hop(t, at=NOW + 1000 + i))
        kept = self.saved()['hops']
        self.assertEqual(len(kept), 500)
        self.assertNotIn(first, [h['id'] for h in kept])
        self.assertEqual(kept[-1]['at'], NOW + 1004)

    def test_prune_drops_hops_past_thirty_days_and_their_empty_tasks(self):
        old = tasks.task_for(F, 'claude', A)
        new = tasks.task_for(F, 'claude', B)
        data = self.saved()
        data['tasks'][old]['made_at'] = NOW - 40 * 86400
        core.write_json(self.path, data)
        tasks.add_hop(hop(old, at=NOW - 31 * 86400))
        keep = tasks.add_hop(hop(new, at=NOW - 29 * 86400, frm_chat=B))
        gone = tasks.prune(NOW)
        self.assertEqual(gone, 1)
        self.assertEqual([h.id for h in tasks.hops()], [keep.id])
        self.assertEqual(set(self.saved()['tasks']), {new})


class TwoWriters(Base):
    CODE = ('from switchboard.providers import tasks\n'
            'from switchboard.providers.contract import Hop\n'
            'for i in range(40):\n'
            '    tasks.add_hop(Hop(id=tasks.new_hop_id(), task=%r, kind="continue", at=float(i), folder="/x",\n'
            '                      from_provider="claude", from_chat=None, to_provider="codex"))\n')

    def test_two_processes_adding_at_once_lose_nothing(self):
        root = tempfile.mkdtemp(prefix='sb-ledger-')
        results = []
        threads = [threading.Thread(target=lambda t=t: results.append(
            fp.child_python(self.CODE % t, root, timeout=120.0))) for t in ('t-0000000a', 't-0000000b')]
        t0 = time.monotonic()
        for th in threads:
            th.start()
        for th in threads:
            th.join(150)
        self.assertEqual([r.code for r in results], [0, 0], [r.err[-500:] for r in results])
        hops = self.saved()['hops']
        self.assertEqual(len(hops), 80)
        self.assertEqual(sorted({h['task'] for h in hops}), ['t-0000000a', 't-0000000b'])
        self.assertLess(time.monotonic() - t0, 120)



class AnUnreadableLedger(Base):
    """A ledger that is there and cannot be read is never written over; reading it gives nothing."""
    BROKEN = '{"v": 1, "tasks": {}, "hops": [{"id": "h-0000000a"'

    def test_every_change_refuses_and_writes_nothing(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w') as f:
            f.write(self.BROKEN)
        changes = (lambda: tasks.task_for(F, 'claude', A), lambda: tasks.add_hop(hop('t-0000000a')),
                   lambda: tasks.update_hop('h-0000000a', state='returned'), lambda: tasks.prune(NOW))
        for change in changes:
            with self.assertRaises(contract.ProviderError) as cm:
                change()
            self.assertEqual(cm.exception.kind, 'unreadable')
            with open(self.path) as f:
                self.assertEqual(f.read(), self.BROKEN)
        self.assertEqual(tasks.hops(), [])
        self.assertIsNone(tasks.open_continue(F))

    def test_a_missing_ledger_is_still_empty(self):
        self.assertRegex(tasks.task_for(F, 'claude', A), r'^t-[0-9a-f]{8}$')


if __name__ == '__main__':
    unittest.main()
