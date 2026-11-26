import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""The look at every chat (switchboard/chats.py) and what it keeps.

Each chat's own file says which account it was on (switchboard/chatfile.py). The look reads every chat written in
the last days, on from where it stopped, newest first, a bounded amount per pass, and keeps only account lines,
titles, the times and request ids of replies, and which claude.ai connector a call went to and whether it failed.
Every chat here is made up (tests/fakes_chats.py), in Claude Code's own line shapes.
"""
import ast
import json
import os
import threading
import time
import unittest
import unittest.mock

from fakes_links import Home
from fakes_chats import (A, ACCT, B, CHAT, CHAT2, CHAT3, FOLDER, ORG_A, ORG_B, ORG_C, PROJECT, account, helper,
                             owner, progress, prompt, reply, result, title, write)
from switchboard import chatfile, chats, core

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
H = 3600.0
DRIVE = 'mcp__claude_ai_Google_Drive__search'
LINEAR = 'mcp__claude_ai_Linear__list_issues'


class Look(Home):
    def setUp(self):
        super().setUp()
        self.now = time.time()
        self.root = core.projects()
        self.state(active=B, **{A: {'org_id': ORG_A}, B: {'org_id': ORG_B}})

    def look(self, **kw):
        kw.setdefault('now', self.now)
        return chats.look(**kw)

    def view(self, **kw):
        index, side = chats.load()
        kw.setdefault('now', self.now)
        return chats.view(index, side, core.load(), **kw)

    def chat(self, v, cid=CHAT):
        return next(c for c in v['chats'] if c['id'] == cid)

    def group(self, v, email):
        return next(a for a in v['accounts'] if a['email'] == email)

    def moved_chat(self, cid=CHAT, at=None, mtime=None):
        """A chat that started on A and moved to B two hours ago."""
        t = self.now - 5 * H if at is None else at
        return write(self.root, [owner(ORG_A, cid), account(ORG_A, t, cid), prompt('hello', t + 1, cid),
                                 reply(t + 2, cid[:8] + '-1', cid), account(ORG_B, t + 3 * H, cid),
                                 reply(t + 3 * H + 5, cid[:8] + '-2', cid)], chat=cid,
                     mtime=self.now - 60 if mtime is None else mtime)


class TheSavedLook(Look):
    def test_each_chat_is_on_the_account_its_own_file_says(self):
        self.moved_chat()
        write(self.root, [owner(ORG_B, CHAT2), account(ORG_B, self.now - H, CHAT2)], chat=CHAT2,
              mtime=self.now - 600)
        self.look()
        v = self.view()
        c = self.chat(v)
        self.assertEqual(c['started'], {'email': A, 'kept': True})
        self.assertEqual(c['on'], {'email': B, 'kept': True})
        self.assertAlmostEqual(c['since'], self.now - 2 * H, delta=1)
        self.assertEqual(c['moves'], 1)
        self.assertEqual([(m['from']['email'], m['to']['email']) for m in c['recent']], [(A, B)])
        self.assertEqual(c['folder'], 'billing')
        self.assertEqual(self.chat(v, CHAT2)['moves'], 0)

    def test_chats_json_has_the_shape_chatfile_documents_and_carries_on(self):
        path = self.moved_chat()
        self.look()
        doc = chatfile.load_index(chatfile.index_path(core.data_dir()))
        self.assertIsNotNone(doc)
        e = doc['chats'][CHAT]
        self.assertEqual((e['file'], e['end'], e['owner'], e['owner_account'], e['cwd']),
                         (path, os.path.getsize(path), ORG_A, ACCT, FOLDER))
        self.assertEqual([s[1] for s in e['steps']], [ORG_A, ORG_B])
        self.assertAlmostEqual(e['last'], self.now - 60, delta=1)
        self.assertEqual(doc['orgs'], {ORG_A: A, ORG_B: B})
        r = chatfile.resume_from(e)
        with open(path, 'ab') as fh:
            fh.write((account(ORG_A, self.now - 30) + '\n').encode())
        chatfile.read(path, start=r.end, into=r)
        fresh = chatfile.read(path)
        self.assertEqual((r.owner, r.owner_account, r.steps, r.end),
                         (fresh.owner, fresh.owner_account, fresh.steps, fresh.end))

    def test_what_the_look_read_is_what_chatfile_reads(self):
        lines = [owner(ORG_A)]
        for i in range(40):
            lines += [account((ORG_A, ORG_B)[i % 2], self.now - 40 * H + i * H), prompt('x' * 3000, self.now - 40 * H + i * H + 1),
                      reply(self.now - 40 * H + i * H + 2, 'r%d' % i)]
        path = write(self.root, lines, mtime=self.now - 60)
        while not self.look(max_bytes=20000)[1]['look']['left'] == 0:
            pass
        e = chatfile.load_index(chatfile.index_path(core.data_dir()))['chats'][CHAT]
        whole = chatfile.read(path)
        self.assertEqual((e['owner'], [tuple(s) for s in e['steps']], e['end'], e['cwd']),
                         (whole.owner, [tuple(s) for s in whole.steps], whole.end, whole.cwd))

    def test_a_damaged_saved_look_starts_over(self):
        self.moved_chat()
        for name in ('chats.json', chats.VIEW_NAME):
            with open(core.data_path(name), 'w') as fh:
                fh.write('{"v": 1, "chats": {')
        self.look()
        self.assertEqual(self.chat(self.view())['on']['email'], B)

    def test_a_chat_whose_two_records_disagree_is_read_again(self):
        self.moved_chat()
        self.look()
        with open(core.data_path(chats.VIEW_NAME)) as fh:
            side = json.load(fh)
        side['chats'][CHAT]['end'] = 5                     # a write of the pair cut short between the two files
        core.write_json(core.data_path(chats.VIEW_NAME), side)
        self.look()
        v = self.view()
        self.assertEqual(self.chat(v)['on']['email'], B)
        self.assertEqual(self.group(v, B)['turns'], 1)    # read again, counted once

    def test_a_chat_file_that_is_gone_leaves_the_saved_look(self):
        path = self.moved_chat()
        self.look()
        os.unlink(path)
        index, _ = self.look()
        self.assertNotIn(CHAT, index['chats'])


class Incremental(Look):
    def test_a_second_look_reads_only_what_was_added(self):
        path = self.moved_chat()
        _, side = self.look()
        self.assertEqual(side['look']['bytes'], os.path.getsize(path))
        _, side = self.look()
        self.assertEqual(side['look']['bytes'], 0)
        more = (reply(self.now - 20, 'later') + '\n').encode()
        with open(path, 'ab') as fh:
            fh.write(more)
        _, side = self.look()
        self.assertEqual(side['look']['bytes'], len(more))

    def test_a_bounded_pass_stops_and_the_next_carries_on_newest_first(self):
        big = 'y' * 20000
        for i, cid in enumerate((CHAT, CHAT2, CHAT3)):
            write(self.root, [owner(ORG_A, cid), account(ORG_A, self.now - 9 * H, cid)]
                  + [prompt(big, self.now - 8 * H + k, cid) for k in range(10)]
                  + [account(ORG_B, self.now - H, cid), reply(self.now - H + 1, '%d-r' % i, cid)],
                  chat=cid, mtime=self.now - 100 * (i + 1))
        _, side = self.look(max_bytes=150000)
        self.assertEqual(side['look']['read'], 0)
        self.assertEqual(side['look']['chats'], 3)
        v = self.view()
        self.assertEqual(v['reading'], {'done': False, 'read': 0, 'of': 3})
        index, side = self.look(max_bytes=150000)
        self.assertEqual(side['look']['read'], 1)
        self.assertEqual(index['chats'][CHAT]['end'], os.path.getsize(os.path.join(self.root, PROJECT, CHAT + '.jsonl')))
        self.assertLess(index['chats'][CHAT3]['end'], 100)      # the oldest waits its turn
        passes = 2
        while side['look']['left']:
            _, side = self.look(max_bytes=150000)
            passes += 1
        self.assertGreaterEqual(passes, 4)
        v = self.view()
        self.assertTrue(v['reading']['done'])
        self.assertEqual([self.chat(v, c)['on']['email'] for c in (CHAT, CHAT2, CHAT3)], [B, B, B])

    def test_a_pass_stops_at_its_time_too(self):
        for i, cid in enumerate((CHAT, CHAT2)):
            write(self.root, [owner(ORG_A, cid)] + [prompt('z' * 5000, self.now - H + k, cid) for k in range(4000)],
                  chat=cid, mtime=self.now - 10 * (i + 1))
        ticks = iter(range(10 ** 6))
        _, side = self.look(max_seconds=3, clock=lambda: next(ticks))
        self.assertGreater(side['look']['left'], 0)
        self.assertLess(side['look']['bytes'], 2 * os.path.getsize(os.path.join(self.root, PROJECT, CHAT + '.jsonl')))

    def test_a_line_still_being_written_waits_for_the_next_look(self):
        half = account(ORG_B, self.now - 120)
        path = write(self.root, [owner(ORG_A), account(ORG_A, self.now - H)], torn=half[:50], mtime=self.now - 100)
        index, _ = self.look()
        self.assertEqual(index['chats'][CHAT]['end'], os.path.getsize(path) - 50)
        self.assertEqual(self.chat(self.view())['on']['email'], A)
        with open(path, 'ab') as fh:
            fh.write((half[50:] + '\n').encode())
        self.look()
        self.assertEqual(self.chat(self.view())['on']['email'], B)

    def test_a_torn_last_line_counts_as_read_and_the_look_settles(self):
        # A chat mid-write (or a Claude Code that was killed) ends without a line end: it is read up to its last
        # whole line and counts as read, so the look is done, and the torn line is not read again each look.
        half = account(ORG_B, self.now - 120)
        path = write(self.root, [owner(ORG_A), account(ORG_A, self.now - H)], torn=half[:50], mtime=self.now - 100)
        index, side = self.look()
        self.assertEqual((side['look']['left'], side['look']['read'], side['look']['chats']), (0, 1, 1))
        self.assertIs(index.get('done'), True)
        v = self.view()
        self.assertEqual((v['state'], v['reading']), ('ready', {'done': True, 'read': 1, 'of': 1}))
        self.assertTrue(self.chat(v)['read'])
        with unittest.mock.patch.object(chats, '_read', wraps=chats._read) as reads:
            _, side = self.look()
        self.assertEqual((reads.call_count, side['look']['left']), (0, 0))
        with open(path, 'ab') as fh:                                  # the line is finished: the next look takes it
            fh.write((half[50:] + '\n').encode())
        index, _ = self.look()
        self.assertEqual(index['chats'][CHAT]['end'], os.path.getsize(path))
        self.assertEqual(self.chat(self.view())['on']['email'], B)

    def test_a_file_replaced_by_a_shorter_one_is_read_from_its_start(self):
        write(self.root, [owner(ORG_A), account(ORG_A, self.now - 9 * H)]
              + [reply(self.now - 2 * H + i, 'old-%d' % i) for i in range(30)] + [account(ORG_B, self.now - H)])
        self.look()
        write(self.root, [owner(ORG_C), account(ORG_C, self.now - 600), reply(self.now - 500, 'old-1')],
              mtime=self.now - 30)
        self.look()
        v = self.view()
        c = self.chat(v)
        self.assertEqual((c['started'], c['on']), ({'email': None, 'kept': False, 'org': ORG_C[:8]},) * 2)
        self.assertEqual(c['moves'], 0)
        self.assertEqual(self.group(v, A)['turns'] + self.group(v, B)['turns'], 30)   # 'old-1' is not counted again

    def test_only_chats_written_in_the_last_days_are_read(self):
        self.moved_chat(mtime=self.now - 9 * 86400)
        write(self.root, [owner(ORG_B, CHAT2)], chat=CHAT2, mtime=self.now - 60)
        index, side = self.look()
        self.assertEqual(list(index['chats']), [CHAT2])
        index, _ = self.look(days=10)
        self.assertEqual(sorted(index['chats']), [CHAT, CHAT2])


class WhatIsKept(Look):
    SECRET = ('PROMPT-WORDS-77', 'REPLY-WORDS-88', 'TOOL-INPUT-99', 'TOOL-OUTPUT-66', 'CONNECTOR-ANSWER-55')

    def test_no_message_no_tool_input_and_no_tool_output_is_kept(self):
        p, r, i, o, c = self.SECRET
        write(self.root, [owner(ORG_A), account(ORG_A, self.now - H), prompt(p, self.now - H + 1),
                          reply(self.now - H + 2, 'r1', text=r), reply(self.now - H + 3, 'r2', tools=[('t1', 'Bash')],
                                                                         inp={'command': i}),
                          result(self.now - H + 4, 't1', text=o),
                          reply(self.now - H + 5, 'r3', tools=[('t2', DRIVE)], inp={'query': i}),
                          result(self.now - H + 6, 't2', text=c), title('ai-title', 'Billing export fix')],
              mtime=self.now - 60)
        self.look()
        kept = ''
        for name in ('chats.json', chats.VIEW_NAME):
            with open(core.data_path(name)) as fh:
                kept += fh.read()
        for word in self.SECRET + ('req_r1', 'msg_r1', 'toolu', 't1"', 'Bash'):
            self.assertNotIn(word, kept)
        self.assertIn('Billing export fix', kept)           # the title and the folder are kept, as the docs say
        self.assertIn(FOLDER, kept)
        self.assertIn('Google Drive', kept)

    def test_the_two_files_are_only_yours_to_read(self):
        self.moved_chat()
        self.look()
        for name in ('chats.json', chats.VIEW_NAME):
            self.assertEqual(os.stat(core.data_path(name)).st_mode & 0o777, 0o600)

    def test_the_engine_never_imports_this_and_this_never_imports_the_engine(self):
        folder = os.path.join(ROOT, 'switchboard', 'accounts')
        for name in os.listdir(folder):
            if name.endswith('.py'):
                with open(os.path.join(folder, name)) as fh:
                    tree = ast.parse(fh.read())
                for n in ast.walk(tree):
                    if isinstance(n, (ast.Import, ast.ImportFrom)):
                        names = [a.name for a in n.names] + [getattr(n, 'module', None) or '']
                        self.assertFalse(any('chats' in x for x in names), name)
        with open(os.path.join(ROOT, 'switchboard', 'chats.py')) as fh:
            tree = ast.parse(fh.read())
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                self.assertNotIn('accounts', n.module or '')
                self.assertFalse(any(a.name == 'accounts' for a in n.names))

    def test_working_means_what_the_engine_means(self):
        from switchboard.accounts import sessions
        self.assertEqual(chats.IDLE_AFTER, sessions.IDLE_AFTER)


class Titles(Look):
    def test_the_last_name_you_gave_wins_over_claude_codes_own(self):
        write(self.root, [owner(ORG_A), title('ai-title', 'First words'), title('custom-title', 'Billing export'),
                          title('ai-title', 'Later words'), title('custom-title', 'Billing export fix')],
              mtime=self.now - 60)
        write(self.root, [owner(ORG_A, CHAT2), title('ai-title', 'Only the AI title', CHAT2)], chat=CHAT2,
              mtime=self.now - 60)
        write(self.root, [owner(ORG_A, CHAT3)], chat=CHAT3, mtime=self.now - 60)
        self.look()
        v = self.view()
        self.assertEqual([self.chat(v, c)['title'] for c in (CHAT, CHAT2, CHAT3)],
                         ['Billing export fix', 'Only the AI title', None])


class Accounts(Look):
    def test_a_chat_on_an_organization_switchboard_does_not_keep_is_shown_never_guessed(self):
        write(self.root, [owner(ORG_C), account(ORG_C, self.now - H)], mtime=self.now - 60)
        self.look()
        v = self.view()
        c = self.chat(v)
        self.assertEqual(c['on'], {'email': None, 'kept': False, 'org': ORG_C[:8]})
        g = [a for a in v['accounts'] if not a['kept']]
        self.assertEqual([(a['email'], a['org'], a['on']) for a in g], [(None, ORG_C[:8], 1)])

    def test_a_chat_from_an_older_claude_code_is_on_an_account_not_known(self):
        write(self.root, [prompt('hello', self.now - H), reply(self.now - H + 1, 'r1')], mtime=self.now - 60)
        self.look()
        v = self.view()
        c = self.chat(v)
        self.assertEqual((c['started'], c['on'], c['moves']), (None, None, 0))
        self.assertIn(None, [a.get('email') for a in v['accounts'] if a.get('known') is False])

    def test_an_organization_two_kept_accounts_share_names_neither(self):
        self.state(active=B, **{A: {'org_id': ORG_A}, B: {'org_id': ORG_A}})
        write(self.root, [owner(ORG_A), account(ORG_A, self.now - H)], mtime=self.now - 60)
        self.look()
        self.assertEqual(self.chat(self.view())['on'], {'email': None, 'kept': True, 'org': ORG_A[:8]})

    def test_a_flutter_is_no_move_and_its_raw_steps_stay_in_chats_json(self):
        t = self.now - 3 * H
        write(self.root, [owner(ORG_A), account(ORG_A, t), account(ORG_B, t + 600), account(ORG_A, t + 610),
                          account(ORG_B, t + 620), account(ORG_A, t + 625)], mtime=self.now - 60)
        index, _ = self.look()
        self.assertEqual(len(index['chats'][CHAT]['steps']), 5)
        c = self.chat(self.view())
        self.assertEqual((c['moves'], c['recent'], c['on']['email']), (0, [], A))

    def test_a_chat_with_hundreds_of_moves_keeps_its_newest_steps_and_counts_them_all(self):
        orgs = (ORG_A, ORG_B)
        t0 = self.now - 6 * 86400
        lines = [owner(ORG_A)] + [account(orgs[i % 2], t0 + i * 300) for i in range(1, 601)]
        path = write(self.root, lines, mtime=self.now - 600)
        self.publish_at(t0 + 30)                              # a page made before the steps it keeps
        self.look()
        e = chatfile.load_index(chatfile.index_path(core.data_dir()))['chats'][CHAT]
        self.assertLessEqual(len(e['steps']), 300)                 # a few hundred, newest kept
        whole = chatfile.read(path)
        c = self.chat(self.view(published=chats.published_by_chat(self.now)))
        self.assertEqual(c['moves'], chatfile.moves(whole.steps, whole.owner))
        self.assertEqual((c['moves'], c['on']['email']), (600, A))
        self.assertEqual([(m['from']['email'], m['to']['email']) for m in c['recent']], [(B, A), (A, B), (B, A)])
        self.assertEqual(c['pages_not_known'], 1)             # which account it was on then is no longer kept
        with open(path, 'ab') as fh:                          # more moves later: still every one counted
            fh.write(''.join(account(orgs[i % 2], t0 + i * 300) + '\n' for i in range(601, 700)).encode())
        self.look()
        whole = chatfile.read(path)
        c = self.chat(self.view())
        self.assertEqual((c['moves'], c['on']['email']), (chatfile.moves(whole.steps, whole.owner), B))
        self.assertEqual(c['moves'], 699)

    def test_a_chat_that_flutters_without_a_pause_is_still_kept_small_and_its_move_count_is_not_known(self):
        orgs = (ORG_A, ORG_B)
        t0 = self.now - 86400
        write(self.root, [owner(ORG_A)] + [account(orgs[i % 2], t0 + i * 20) for i in range(1, 701)],
              mtime=self.now - 600)                       # a change every 20 s: settle() folds all of it
        self.look()
        e = chatfile.load_index(chatfile.index_path(core.data_dir()))['chats'][CHAT]
        self.assertLessEqual(len(e['steps']), 300)
        c = self.chat(self.view())
        self.assertIsNone(c['moves'])                     # where a flutter was cut, its count is not known
        self.assertEqual(c['on']['email'], A)

    def publish_at(self, t, aid='MadeUpPageBeforeKept01'):
        os.makedirs(core.data_path('hooks'), exist_ok=True)
        with open(core.data_path(os.path.join('hooks', 'published.jsonl')), 'a') as fh:
            fh.write(json.dumps({'v': 1, 't': t, 'id': aid, 'url': 'https://claude.ai/artifact/' + aid,
                                 'how': 'published', 'file': None, 'title': 'A page', 'sid': CHAT}) + '\n')

    def test_many_moves_show_the_last_few(self):
        steps = [account((ORG_A, ORG_B)[i % 2], self.now - 20 * H + i * H) for i in range(12)]
        write(self.root, [owner(ORG_A)] + steps, mtime=self.now - 60)
        self.look()
        c = self.chat(self.view())
        self.assertEqual(c['moves'], 11)
        self.assertEqual(len(c['recent']), chats.RECENT_MOVES)
        self.assertAlmostEqual(c['recent'][-1]['t'], self.now - 9 * H, delta=1)

    def test_numbers_per_account(self):
        self.moved_chat()                                                              # A -> B, 1 min ago
        write(self.root, [owner(ORG_B, CHAT2), account(ORG_B, self.now - 3 * H, CHAT2),
                          reply(self.now - 3 * H + 1, 'c2-1', CHAT2), reply(self.now - 3 * H + 2, 'c2-2', CHAT2)],
              chat=CHAT2, mtime=self.now - 3 * H)                                     # idle 3 h on B
        write(self.root, [owner(ORG_A, CHAT3), account(ORG_A, self.now - 50 * H, CHAT3),
                          reply(self.now - 50 * H, 'c3-1', CHAT3)],
              chat=CHAT3, mtime=self.now - 50 * H)                                    # two days old, on A
        self.look()
        v = self.view()
        a, b = self.group(v, A), self.group(v, B)
        self.assertEqual((b['on'], b['working'], b['started'], b['elsewhere']), (2, 1, 1, 0))
        self.assertEqual((a['on'], a['working'], a['started'], a['elsewhere']), (0, 0, 1, 1))
        self.assertEqual(a['moved_to'], [{'account': {'email': B, 'kept': True}, 'n': 1}])
        self.assertEqual((a['turns'], b['turns']), (1, 3))
        self.assertAlmostEqual(a['last'], self.now - 5 * H + 2, delta=1)
        self.assertAlmostEqual(b['last'], self.now - 2 * H + 5, delta=1)
        self.assertTrue(b['active'])
        self.assertEqual([c['id'] for c in v['chats']], [CHAT, CHAT2, CHAT3])        # working first, then newest

    def test_states(self):
        self.moved_chat(mtime=self.now - 60)
        write(self.root, [owner(ORG_B, CHAT2)], chat=CHAT2, mtime=self.now - 3 * H)
        write(self.root, [owner(ORG_B, CHAT3)], chat=CHAT3, mtime=self.now - 3 * 86400)
        self.look()
        v = self.view()
        self.assertEqual([(c['id'], c['state']) for c in v['chats']],
                         [(CHAT, 'working'), (CHAT2, 'idle'), (CHAT3, 'old')])
        self.assertEqual([c['id'] for c in self.view(days=1)['chats']], [CHAT, CHAT2])

    def test_a_helper_writing_makes_its_chat_working_and_helpers_are_counted(self):
        self.moved_chat(mtime=self.now - 2 * H)
        helper(self.root, name='agent-1', mtime=self.now - 30)
        helper(self.root, name='agent-2', mtime=self.now - 3 * H, workflow='wf_1')
        self.look()
        c = self.chat(self.view())
        self.assertEqual((c['state'], c['helpers']), ('working', 2))

    def test_quiet_helper_folders_are_not_looked_at_file_by_file_each_time(self):
        self.moved_chat(mtime=self.now - 2 * H)
        for i in range(30):
            helper(self.root, name='agent-%d' % i, mtime=self.now - 2 * H, workflow='wf_1')
        self.look()
        stats = []
        real = os.stat

        def counting(path, *a, **k):
            if os.path.basename(str(path)).startswith('agent-'):
                stats.append(path)
            return real(path, *a, **k)
        with unittest.mock.patch.object(chats.os, 'stat', counting):
            self.look(now=self.now + 60)
        self.assertEqual(stats, [])
        self.assertEqual(self.chat(self.view(now=self.now + 60))['helpers'], 30)
        late = os.path.join(self.root, PROJECT, CHAT, 'subagents', 'workflows', 'wf_1', 'agent-3.jsonl')
        os.utime(late, (self.now + 100, self.now + 100))         # an old helper written again: the folder is the same
        self.look(now=self.now + 120)
        self.assertEqual(self.chat(self.view(now=self.now + 120))['state'], 'idle')
        self.look(now=self.now + chats.HELPERS_FULL + 1)           # but it is looked at again within HELPERS_FULL
        self.assertEqual(self.chat(self.view(now=self.now + chats.HELPERS_FULL + 1))['last'], self.now + 100)
        helper(self.root, name='agent-new', mtime=self.now + 200, workflow='wf_1')   # a new one: the folder changed
        self.look(now=self.now + 230)
        c = self.chat(self.view(now=self.now + 230))
        self.assertEqual((c['helpers'], c['state']), (31, 'working'))

    def test_a_chat_on_its_own_login_says_so(self):
        self.moved_chat()
        os.makedirs(core.data_path('live'))
        core.write_json(core.data_path(os.path.join('live', CHAT + '.json')),
                        {'v': 1, 'session_id': CHAT, 'at': self.now, 'rate_limits': {}, 'own_profile': True})
        os.makedirs(core.data_path('hooks'))
        core.write_json(core.data_path(os.path.join('hooks', CHAT2 + '.json')), {'v': 1, 'own': True})
        self.assertEqual(chats.own_logins(), {CHAT: True, CHAT2: True})
        self.look()
        v = self.view(own=chats.own_logins())
        c = self.chat(v)
        self.assertTrue(c['own_login'])
        self.assertIsNone(c['next_on'])

    def test_a_chat_not_read_whole_is_on_an_account_not_known_yet(self):
        # It started on A and moved to B late in its file; the look stopped before that line. What it is on, since
        # when and how often it moved are not known yet, and it counts on no account (nor as "account not known").
        pad = [prompt('x' * 500, self.now - 9000 + i) for i in range(200)]
        path = write(self.root, [owner(ORG_A), account(ORG_A, self.now - 9500)] + pad
                     + [account(ORG_B, self.now - 2000), prompt('y', self.now - 1000)], mtime=self.now - 60)
        self.look(max_bytes=20000)
        v = self.view()
        c = self.chat(v)
        self.assertFalse(c['read'])
        self.assertEqual((c['on'], c['since'], c['moves'], c['recent'], c['next_on']), (None, None, None, [], None))
        self.assertEqual(c['started'], {'email': A, 'kept': True})            # its first line says where it began
        self.assertEqual(c['holds'], {'pages': [], 'connectors': []})
        a, b = self.group(v, A), self.group(v, B)
        self.assertEqual((a['on'], a['started'], a['elsewhere'], b['on']), (0, 1, 0, 0))
        self.assertEqual([x['email'] for x in v['accounts']], [A, B])          # no "account not known" group
        while self.look(max_bytes=20000)[1]['look']['left']:
            pass
        c = self.chat(self.view())
        self.assertEqual((c['read'], c['on']['email'], c['moves']), (True, B, 1))
        self.assertTrue(os.path.getsize(path) > 20000)

    def test_a_chat_that_follows_claude_codes_login_says_where_its_next_turn_goes(self):
        write(self.root, [owner(ORG_A), account(ORG_A, self.now - 3 * H)], mtime=self.now - 3 * H)
        self.look()
        self.assertEqual(self.chat(self.view())['next_on'], B)

    def test_short_ids_are_unique_and_at_least_eight(self):
        for cid in (CHAT, CHAT2, CHAT3):
            write(self.root, [owner(ORG_A, cid)], chat=cid, mtime=self.now - 60)
        self.look()
        shorts = {c['id']: c['short'] for c in self.view()['chats']}
        self.assertEqual(shorts[CHAT3], CHAT3[:8])
        self.assertEqual(len(set(shorts.values())), 3)
        for cid, s in shorts.items():
            self.assertTrue(cid.startswith(s) and len(s) >= 8)


class Turns(Look):
    def test_turns_in_the_last_day_count_by_the_account_at_the_time(self):
        t = self.now - 30 * H
        write(self.root, [owner(ORG_A), account(ORG_A, t), reply(t + 1, 'old'),                 # over a day ago
                          reply(self.now - 5 * H, 'a1'), reply(self.now - 5 * H, 'a1'),         # one reply, two blocks
                          reply(self.now - 5 * H + 9, 'a2'),
                          reply(self.now - 5 * H + 10, 'notice', model='<synthetic>'),            # Claude Code's own
                          progress(self.now - 5 * H + 11, 'helper-copy'),                         # a helper's
                          account(ORG_B, self.now - 2 * H), reply(self.now - 2 * H + 1, 'b1')], mtime=self.now - 60)
        write(self.root, [owner(ORG_A, CHAT2), account(ORG_A, t, CHAT2), reply(self.now - 5 * H, 'a1', CHAT2),
                          reply(self.now - 5 * H + 9, 'a2', CHAT2)], chat=CHAT2, mtime=self.now - 60)   # a fork's copy
        self.look()
        v = self.view()
        self.assertEqual((self.group(v, A)['turns'], self.group(v, B)['turns']), (2, 1))


class Connectors(Look):
    def drive_chat(self, error_after_move=True):
        t = self.now - 6 * H
        return write(self.root, [owner(ORG_A), account(ORG_A, t), reply(t + 1, 'd1', tools=[('toolu_d1', DRIVE)]),
                                 result(t + 2, 'toolu_d1'), account(ORG_B, self.now - H),
                                 reply(self.now - H + 1, 'd2', tools=[('toolu_d2', DRIVE)]),
                                 result(self.now - H + 2, 'toolu_d2', error=error_after_move,
                                        text='MCP server "claude.ai Google Drive" session expired')],
                     mtime=self.now - 60)

    def test_a_connector_that_answered_under_another_account_is_held(self):
        self.drive_chat()
        self.look()
        v = self.view()
        c = self.chat(v)
        self.assertEqual(len(c['holds']['connectors']), 1)
        h = c['holds']['connectors'][0]
        self.assertEqual((h['name'], h['as'], h['here'], h['failed']),
                         ('Google Drive', {'email': A, 'kept': True}, 'no', 1))
        self.assertAlmostEqual(h['at'], self.now - 6 * H + 2, delta=1)
        self.assertAlmostEqual(v['since'], self.now - chats.DAYS * 86400, delta=5)
        index, _ = chats.load()
        self.assertEqual(list(index['connectors']), [ORG_A])          # an error is no answer
        self.assertEqual(list(index['connectors'][ORG_A]), ['Google Drive'])
        self.assertEqual(self.group(v, A)['connectors'][0]['name'], 'Google Drive')
        self.assertEqual(self.group(v, B)['connectors'], [])

    def test_yes_when_the_account_it_is_on_has_had_it_answer(self):
        self.drive_chat()
        write(self.root, [owner(ORG_B, CHAT2), account(ORG_B, self.now - 4 * H, CHAT2),
                          reply(self.now - 4 * H, 'x1', CHAT2, tools=[('toolu_x', DRIVE)]),
                          result(self.now - 4 * H + 1, 'toolu_x', chat=CHAT2)], chat=CHAT2, mtime=self.now - 600)
        self.look()
        h = self.chat(self.view())['holds']['connectors'][0]
        self.assertEqual(h['here'], 'yes')
        self.assertAlmostEqual(h['here_at'], self.now - 4 * H + 1, delta=1)

    def test_not_known_until_the_first_look_is_done(self):
        self.drive_chat()
        write(self.root, [owner(ORG_B, CHAT2)] + [prompt('w' * 20000, self.now - 9 * H + i, CHAT2) for i in range(20)],
              chat=CHAT2, mtime=self.now - 9 * H)
        self.look(max_bytes=5000)
        h = self.chat(self.view())['holds']['connectors'][0]
        self.assertEqual(h['here'], 'not known')
        self.assertIsNone(chatfile.load_index(chatfile.index_path(core.data_dir())).get('done'))

    def test_done_holds_only_while_every_chat_in_the_window_is_read_and_since_is_that_window(self):
        self.drive_chat()
        index, _ = self.look(days=10)
        self.assertIs(index['done'], True)
        self.assertAlmostEqual(index['since'], self.now - 10 * 86400, delta=1)
        index, _ = self.look()                              # the page's own window: its start, not an older one
        self.assertAlmostEqual(index['since'], self.now - chats.DAYS * 86400, delta=1)
        self.assertEqual(self.chat(self.view())['holds']['connectors'][0]['here'], 'no')
        write(self.root, [owner(ORG_B, CHAT2)] + [prompt('w' * 20000, self.now - H + i, CHAT2) for i in range(20)],
              chat=CHAT2, mtime=self.now - 30)              # a new chat, not read whole yet: it may have used Drive
        index, _ = self.look(max_bytes=5000)
        self.assertNotIn('done', index)
        self.assertNotIn('since', index)
        v = self.view()
        self.assertEqual((v['state'], v['since']), ('reading', None))
        self.assertEqual(self.chat(v)['holds']['connectors'][0]['here'], 'not known')

    def test_a_result_read_in_a_later_pass_still_finds_its_call(self):
        t = self.now - 2 * H
        path = write(self.root, [owner(ORG_A), account(ORG_A, t), reply(t + 1, 'l1', tools=[('toolu_l', LINEAR)])],
                     mtime=self.now - 60)
        self.look()
        with open(path, 'ab') as fh:
            fh.write((result(t + 2, 'toolu_l') + '\n').encode())
        index, _ = self.look()
        self.assertIn('Linear', index['connectors'][ORG_A])

    def test_each_connector_a_chat_used_is_listed_with_every_account_it_answered_under(self):
        self.drive_chat(error_after_move=False)
        self.look()
        c = self.chat(self.view())
        self.assertEqual([h['name'] for h in c['holds']['connectors']], ['Google Drive'])   # it answered under A too
        names = [(x['name'], sorted((u['as'] or {}).get('email') for u in x['answered'])) for x in c['connectors']]
        self.assertEqual(names, [('Google Drive', [A, B])])
        self.assertEqual(c['holds']['connectors'][0]['here'], 'yes')


class Pages(Look):
    def publish(self, aid, t, sid=CHAT):
        os.makedirs(core.data_path('hooks'), exist_ok=True)
        with open(core.data_path(os.path.join('hooks', 'published.jsonl')), 'a') as fh:
            fh.write(json.dumps({'v': 1, 't': t, 'id': aid, 'url': 'https://claude.ai/artifact/' + aid,
                                 'how': 'published', 'file': None, 'title': 'Made-up page', 'sid': sid}) + '\n')

    def test_a_page_a_chat_made_belongs_to_the_account_the_chat_was_on_then(self):
        self.moved_chat()                                   # A from 5 h ago, B from 2 h ago
        self.publish('PageMadeUnderA00001', self.now - 4 * H)
        self.publish('PageMadeUnderA00001', self.now - 3 * H)          # updated: still one page
        self.publish('PageMadeUnderB00002', self.now - H)
        self.publish('PageFromAnotherChat3', self.now - H, sid=CHAT2)
        self.look()
        found = chats.published_by_chat(now=self.now)
        self.assertEqual(sorted(found), [CHAT, CHAT2])
        c = self.chat(self.view(published=found))
        self.assertEqual(c['pages'], 2)
        self.assertEqual(c['holds']['pages'], [{'owner': {'email': A, 'kept': True}, 'n': 1}])

    def test_a_page_made_after_what_was_read_has_an_owner_not_known(self):
        write(self.root, [owner(ORG_A), account(ORG_A, self.now - 5 * H)]
              + [prompt('p' * 20000, self.now - 4 * H + i) for i in range(10)], mtime=self.now - 60)
        self.publish('PageMadeLaterThan0001', self.now - 30)
        self.look(max_bytes=30000)
        c = self.chat(self.view(published=chats.published_by_chat(now=self.now)))
        self.assertEqual((c['pages'], c['holds']['pages']), (1, []))
        self.assertEqual(c['pages_not_known'], 1)


class TwoWriters(Look):
    def test_the_page_and_a_command_at_once_never_tear_or_count_twice(self):
        for i, cid in enumerate((CHAT, CHAT2, CHAT3)):
            write(self.root, [owner(ORG_A, cid), account(ORG_A, self.now - 9 * H, cid)]
                  + [reply(self.now - 8 * H + k, '%d-%d' % (i, k), cid) for k in range(300)]
                  + [account(ORG_B, self.now - H, cid), reply(self.now - H + 1, '%d-last' % i, cid)],
                  chat=cid, mtime=self.now - 60 * (i + 1))
        errors = []

        def writer():
            try:
                for _ in range(200):
                    _, side = chats.look(now=self.now, max_bytes=40000)
                    if not side['look']['left']:
                        break
            except Exception as e:  # noqa: BLE001
                errors.append(e)
        threads = [threading.Thread(target=writer) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertEqual(errors, [])
        doc = chatfile.load_index(chatfile.index_path(core.data_dir()))
        self.assertEqual(sorted(doc['chats']), sorted((CHAT, CHAT2, CHAT3)))
        v = self.view()
        self.assertEqual((self.group(v, A)['turns'], self.group(v, B)['turns']), (900, 3))
        leftovers = [n for n in os.listdir(core.data_dir()) if n.endswith(core.TMP_SUFFIX)]
        self.assertEqual(leftovers, [])


    def test_one_look_at_a_time_across_the_page_and_a_command(self):
        self.moved_chat()
        inside, overlaps, real = [0], [], chats._scan

        def slow_scan(*a, **k):
            inside[0] += 1
            if inside[0] > 1:
                overlaps.append(inside[0])
            time.sleep(0.05)
            try:
                return real(*a, **k)
            finally:
                inside[0] -= 1
        with unittest.mock.patch.object(chats, '_scan', slow_scan):
            threads = [threading.Thread(target=chats.look, kwargs={'now': self.now}) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
        self.assertEqual(overlaps, [])


class Speed(Look):
    def test_a_large_chat_is_read_within_a_cpu_time_bound_and_a_second_look_is_near_instant(self):
        out = 'o' * 110000
        lines = [owner(ORG_A), account(ORG_A, self.now - 20 * H)]
        for i in range(300):
            t = self.now - 20 * H + i * 60
            lines += [prompt('do the next thing %d' % i, t), reply(t + 1, 'big-%d' % i, tools=[('toolu_%d' % i, 'Read')]),
                      result(t + 2, 'toolu_%d' % i, text=out), reply(t + 3, 'big-%d-b' % i, text='w' * 2000)]
            if i == 150:
                lines.append(account(ORG_B, t + 4))
        path = write(self.root, lines, mtime=self.now - 60)
        size = os.path.getsize(path)
        self.assertGreater(size, 60 << 20)
        cpu = time.process_time()
        _, side = self.look(max_bytes=1 << 40, max_seconds=1e9)
        took = time.process_time() - cpu
        self.assertEqual(side['look']['left'], 0)
        self.assertLess(took, 2.0, 'read %d MB in %.2f s of CPU' % (size >> 20, took))
        cpu = time.process_time()
        _, side = self.look()
        again = time.process_time() - cpu
        self.assertEqual(side['look']['bytes'], 0)
        self.assertLess(again, 0.2)
        v = self.view()
        self.assertEqual((self.group(v, A)['turns'], self.group(v, B)['turns']), (302, 298))


if __name__ == '__main__':
    unittest.main()
