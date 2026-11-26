import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""`switchboard chats`, GET /api/chats, each account's `chats` in GET /api/state, and the demo's
made-up chats. The chats are made up (tests/fakes_chats.py); the demo's are demo.py's.
"""
import contextlib
import io
import json
import os
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from fakes_links import Home
from fakes_chats import (A, B, CHAT, CHAT2, CHAT3, ORG_A, ORG_B, account, owner, reply, result, title, write)
from switchboard import __main__ as cli
from switchboard import chats, core, demo, server, term

H = 3600.0
DRIVE = 'mcp__claude_ai_Google_Drive__search'


class Fixture(Home):
    """B is in use. CHAT started on A five hours ago, used Google Drive there, published a page, and moved to B two
    hours ago, where Drive failed; it wrote a minute ago. CHAT2 has been on B, idle, for three hours. CHAT3 is two
    days old, on A."""

    def setUp(self):
        super().setUp()
        self.now = time.time()
        self.root = core.projects()
        self.state(active=B, **{A: {'org_id': ORG_A}, B: {'org_id': ORG_B}})
        t = self.now - 5 * H
        write(self.root, [owner(ORG_A), account(ORG_A, t), reply(t + 1, 'd1', tools=[('toolu_d1', DRIVE)]),
                          result(t + 2, 'toolu_d1'), account(ORG_B, self.now - 2 * H),
                          reply(self.now - 2 * H + 1, 'd2', tools=[('toolu_d2', DRIVE)]),
                          result(self.now - 2 * H + 2, 'toolu_d2', error=True)], mtime=self.now - 60)
        write(self.root, [owner(ORG_B, CHAT2), account(ORG_B, self.now - 3 * H, CHAT2),
                          reply(self.now - 3 * H, 'c2', CHAT2)], chat=CHAT2, mtime=self.now - 3 * H)
        write(self.root, [owner(ORG_A, CHAT3), account(ORG_A, self.now - 48 * H, CHAT3)], chat=CHAT3,
              mtime=self.now - 48 * H)
        os.makedirs(core.data_path('hooks'), exist_ok=True)
        with open(core.data_path(os.path.join('hooks', 'published.jsonl')), 'w') as fh:
            fh.write(json.dumps({'v': 1, 't': self.now - 4 * H, 'id': 'MadeUpPageUnderA0001',
                                 'url': 'https://claude.ai/artifact/MadeUpPageUnderA0001', 'how': 'published',
                                 'file': None, 'title': 'A page', 'sid': CHAT}) + '\n')

    def run_cli(self, *args):
        out = io.StringIO()
        code = chats.main(list(args), out=out, now=self.now)
        return code, out.getvalue()


class TheCommand(Fixture):
    def test_the_text_names_each_account_and_what_each_chat_holds(self):
        code, text = self.run_cli()
        self.assertEqual(code, 0)
        lines = text.splitlines()
        self.assertEqual(lines[0], '%s   Claude Code is here' % B)            # the account Claude Code is on first
        row = next(x for x in lines if CHAT[:8] in x)
        self.assertTrue(row.lstrip().startswith('working'))
        self.assertIn('started on %s · here since %s · moved 1 time' % (A, term.clock(self.now - 2 * H, self.now)),
                      row)
        self.assertIn('made 1 page as %s' % A, text)
        self.assertIn('used Google Drive as %s; %s has not used it on this Mac in the last 7 days · 1 call failed '
                      'here' % (A, B), text)
        self.assertTrue(any(x.lstrip().startswith('idle 3h') and CHAT2[:9] in x for x in lines))
        self.assertNotIn(CHAT3[:8], text)                                        # two days old: only with --all
        self.assertIn('%s\n  no chat is on it now · 1 chat that started here is on %s now' % (A, B), text)

    def test_all_and_days_show_older_chats(self):
        self.assertIn(CHAT3[:8], self.run_cli('--all')[1])
        write(self.root, [owner(ORG_A, 'old-chat-0001')], chat='old-chat-0001', mtime=self.now - 9 * 86400)
        self.assertNotIn('old-chat', self.run_cli('--all')[1])
        self.assertIn('old-chat', self.run_cli('--days', '10')[1])

    def test_json_is_the_view(self):
        code, text = self.run_cli('--json')
        v = json.loads(text)
        self.assertEqual(code, 0)
        self.assertEqual([c['id'] for c in v['chats']], [CHAT, CHAT2])
        self.assertEqual(v['chats'][0]['holds']['connectors'][0]['here'], 'no')
        self.assertEqual([a['email'] for a in v['accounts']], [A, B])

    def test_one_account(self):
        text = self.run_cli('--account', A)[1]
        self.assertNotIn(B + '   Claude Code is here', text)
        self.assertIn('no chat is on it now', text)
        self.assertNotIn('No chat in the last day', text)
        v = json.loads(self.run_cli('--account', B, '--json')[1])
        self.assertEqual([c['id'] for c in v['chats']], [CHAT, CHAT2])
        with self.assertRaises(core.SwitchError) as e:
            self.run_cli('--account', 'nobody')
        self.assertIn('No account called "nobody"', str(e.exception))

    def test_words_it_does_not_know(self):
        for args in (['--bogus'], ['--days'], ['--days', 'x'], ['--days', '400'], ['--account']):
            code, text = self.run_cli(*args)
            self.assertEqual(code, 2, args)
            self.assertIn(chats.USAGE, text)

    def test_it_says_so_when_a_first_look_is_still_reading(self):
        for i, cid in enumerate(('big-chat-0001', 'big-chat-0002')):
            write(self.root, [owner(ORG_B, cid)] + [reply(self.now - H + k, '%s-%d' % (cid, k), cid, text='x' * 9000)
                                                    for k in range(60)], chat=cid, mtime=self.now - 30 - i)
        with mock.patch.object(chats, 'COMMAND_BYTES', 100000):
            text = self.run_cli()[1]
        self.assertTrue(text.startswith('Read 0 of 5 chats so far'), text[:80])

    def test_a_chat_not_read_whole_shows_only_its_title_its_id_and_still_reading(self):
        big = 'b16c4a70-0000-4000-8000-000000000009'
        write(self.root, [owner(ORG_A, big), account(ORG_A, self.now - 3 * H, big), title('custom-title', 'Long one', big)]
              + [reply(self.now - 3 * H + k, 'big-%d' % k, big, text='x' * 9000) for k in range(60)]
              + [account(ORG_B, self.now - 2 * H, big)], chat=big, mtime=self.now - 2 * H)
        with mock.patch.object(chats, 'COMMAND_BYTES', 100000):      # CHAT is read whole; the big one only in part
            text = self.run_cli()[1]
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith('Read 1 of 4 chats so far'), lines[0])
        row = [x for x in lines if big[:8] in x]
        self.assertEqual(len(row), 1, text)
        self.assertIn('Long one', row[0])
        self.assertTrue(row[0].endswith('still reading'), row[0])
        for word in ('started', 'here since', 'moved', 'next turn', 'used ', A, B):
            self.assertNotIn(word, row[0])
        self.assertEqual(lines[lines.index(row[0]) - 1], 'Not read to the end yet')     # under no account
        self.assertIn('%s\n  no chat read so far is on it' % A, text)
        self.assertNotIn('no chat is on it now', text)
        self.assertIn('started on %s · here since' % A, next(x for x in lines if CHAT[:8] in x))   # read whole

    def test_a_no_is_said_only_over_a_window_it_names(self):
        v = json.loads(self.run_cli('--json')[1])
        held = v['chats'][0]['holds']['connectors'][0]
        self.assertEqual(held['here'], 'no')
        v['since'] = None                                       # a "no" with no window read whole behind it
        text = '\n'.join(term.chats_lines(v))
        self.assertIn('not known yet whether %s has used it on this Mac' % B, text)
        self.assertNotIn('has not used it', text)
        index, side = chats.load()
        index.pop('since')                                      # the view says "no" only with done and since
        self.assertEqual(chats.view(index, side, core.load(), now=self.now)['chats'][0]['holds']['connectors'][0]
                         ['here'], 'not known')

    def test_an_old_look_says_how_old(self):
        v = json.loads(self.run_cli('--json')[1])
        v['at'] = v['now'] - 180
        self.assertEqual(term.chats_lines(v)[0], 'As of 3 min ago.')
        v['at'] = v['now'] - 30
        self.assertNotIn('As of', term.chats_lines(v)[0])

    def test_no_line_is_wider_than_a_narrow_terminal(self):
        v = json.loads(self.run_cli('--json', '--all')[1])
        for line in term.chats_lines(v, width=80):
            self.assertLessEqual(len(line), 80, line)

    def test_the_verb_and_its_help(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(['chats', '--help']), 0)
        self.assertTrue(out.getvalue().startswith('switchboard chats [--all] [--days N] [--account NAME] [--json]'))
        with contextlib.redirect_stdout(io.StringIO()) as got, \
                mock.patch.object(chats, 'main', lambda args: got.write(repr(args)) and 0):
            cli.main(['chats', '--all'])
        self.assertEqual(got.getvalue(), "['--all']")


class ThePage(Fixture):
    def setUp(self):
        super().setUp()
        server.CHATS.update(view=None, at=None, running=False)
        self.addCleanup(server.CHATS.update, view=None, at=None, running=False)

    def facts(self):
        return {'profiles': {}, 'now': self.now, 'auto': {'enabled': False}, 'engine': {'installed': True},
                'links': {'on': False}, 'guard': None, 'browser': {'name': '', 'present': False},
                'setup_hidden': True, 'demo': False, 'connecting': [], 'chats': server.chats_counts()}

    def test_the_api_answers_reading_until_the_first_look_then_the_view(self):
        self.assertEqual(server.chats_view(), {'state': 'reading', 'accounts': [], 'chats': []})
        rows = server.render(core.load(), self.facts())['accounts']
        self.assertEqual([r['chats'] for r in rows], [None, None])            # no counts before the first look
        server.chats_look(passes=1)
        v = server.chats_view()
        self.assertEqual([c['id'] for c in v['chats']][:2], [CHAT, CHAT2])
        rows = dict((r['email'], r['chats']) for r in server.render(core.load(), self.facts())['accounts'])
        self.assertEqual(rows, {A: {'on': 0, 'working': 0, 'elsewhere': 1}, B: {'on': 2, 'working': 1, 'elsewhere': 0}})

    def test_the_loop_starts_a_look_only_when_one_is_due(self):
        started = []
        with mock.patch.object(server, 'chats_look', lambda **kw: started.append(kw) or server.CHATS.update(
                running=False, at=server.monotonic(), view={'reading': {'done': True}})):
            self.assertTrue(server.chats_tick())
            for t in range(50):
                if not server.CHATS['running']:
                    break
                time.sleep(0.05)
            self.assertFalse(server.chats_tick())                      # done a moment ago: a minute between looks
            self.assertTrue(server.chats_tick(now=server.monotonic() + server.CHATS_EVERY + 1))
            for t in range(50):
                if not server.CHATS['running']:
                    break
                time.sleep(0.05)
        self.assertEqual(len(started), 2)
        self.assertEqual((started[0]['folder'], started[0]['root']), (core.data_dir(), core.projects()))
        server.CHATS.update(running=True)
        self.assertFalse(server.chats_tick(now=server.monotonic() + 10 * server.CHATS_EVERY))   # one at a time

    def test_while_the_first_look_is_reading_the_loop_starts_the_next_at_once(self):
        server.CHATS.update(view={'reading': {'done': False}}, at=server.monotonic(), running=False)
        with mock.patch.object(threading.Thread, 'start', lambda self: None):
            self.assertTrue(server.chats_tick())

    def test_a_chat_with_a_torn_last_line_does_not_keep_the_page_reading(self):
        write(self.root, [owner(ORG_B, 'torn-chat-0001'), account(ORG_B, self.now - H, 'torn-chat-0001')],
              chat='torn-chat-0001', torn='{"type":"assistant","timestamp":"2026', mtime=self.now - 30)
        with mock.patch.object(chats, 'look', wraps=chats.look) as looks:
            server.chats_look(passes=server.CHATS_PASSES, gap=0)
        self.assertEqual(looks.call_count, 1)                          # one pass: nothing is left to read
        self.assertEqual((server.chats_view()['state'], server.chats_view()['reading']['done']), ('ready', True))
        with mock.patch.object(threading.Thread, 'start', lambda self: None):
            self.assertFalse(server.chats_tick())                      # and the next look waits its minute

    def test_a_look_that_fails_keeps_the_last_view(self):
        server.CHATS.update(view={'state': 'ready', 'chats': [], 'accounts': []})
        with mock.patch.object(chats, 'look', side_effect=OSError('disk')):
            server.chats_look(passes=1)
        self.assertEqual(server.chats_view()['state'], 'ready')
        self.assertFalse(server.CHATS['running'])


class TheDemo(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)

    def test_it_has_every_case_the_page_draws(self):
        v = server.chats_view()
        cs, acc = v['chats'], v['accounts']
        self.assertEqual(set(c['state'] for c in cs), {'working', 'idle', 'old'})
        self.assertTrue(any(c['moves'] >= 5 for c in cs))
        self.assertTrue(any(c['moves'] == 0 and c['started'] == c['on'] and c['on'] for c in cs))
        held = [c for c in cs if c['holds']['pages'] and c['holds']['connectors']]
        self.assertEqual(len(held), 2)                  # one working, one idle away from home (the page's live action)
        self.assertEqual(sorted(c['state'] for c in held), ['idle', 'working'])
        self.assertEqual([c['holds']['connectors'][0]['here'] for c in held], ['no', 'no'])
        self.assertTrue(any(a['kept'] and a['on'] == 0 for a in acc))
        self.assertTrue(any(c['own_login'] for c in cs))
        self.assertTrue(any(a['known'] and not a['kept'] for a in acc))         # an account Switchboard doesn't keep
        self.assertTrue(any(not a['known'] for a in acc))                       # one not known
        self.assertTrue(any(a['elsewhere'] for a in acc))
        self.assertEqual(v['state'], 'ready')

    def test_the_state_carries_each_accounts_counts(self):
        rows = server.view()['accounts']
        self.assertTrue(all(isinstance(r['chats'], dict) for r in rows))
        work = next(r for r in rows if r['email'] == 'maya@northwind.example')
        self.assertEqual(work['chats'], {'on': 4, 'working': 1, 'elsewhere': 0})

    def test_a_demo_state_of_other_accounts_still_draws(self):
        """Page tests put other made-up accounts in the demo's state (no organization, a short history): the
        state and the chats still come, with the made-up chats on accounts not known."""
        demo.view()
        st = core.blank_state()
        for i, name in enumerate(('atlas', 'birch')):
            a = core.ensure_account(st, '%s@made-up.example' % name)
            a.update(connected=True, slot=i + 1)
        st['active'] = 'birch@made-up.example'
        st['timeline'] = [['birch@made-up.example', time.time() - 3600]]
        demo.STATE.clear()
        demo.STATE.update(st)
        rows = server.view()['accounts']
        self.assertEqual([r['email'] for r in rows], ['atlas@made-up.example', 'birch@made-up.example'])
        self.assertEqual([r['chats'] for r in rows], [None, None])     # no organization: not known, never 0
        self.assertEqual(len(server.chats_view()['chats']), len(demo.DEMO_CHATS))

    def test_get_api_chats_over_http(self):
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        port = httpd.server_address[1]
        with mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': str(port)}):
            req = urllib.request.Request('http://127.0.0.1:%d/api/chats' % port)
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10) as r:
                v = json.load(r)
        self.assertEqual(len(v['chats']), len(demo.DEMO_CHATS))
        self.assertIn('accounts', v)

    def test_the_command_in_the_demo(self):
        out = io.StringIO()
        self.assertEqual(chats.main([], out=out), 0)
        self.assertTrue(out.getvalue().startswith('maya@northwind.example   Claude Code is here'))


if __name__ == '__main__':
    unittest.main()
