import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""A move that isn't needed waits while a chat is using a claude.ai connector.

A chat used a claude.ai connector (a tool call named mcp__claude_ai_*) under the account in use in the last 30
minutes: that is an obligation of kind 'connector', with the connector's name, the time and the account, and nothing
about files or projects. Like every obligation, it belongs to the account the chat's own file says it was on at that
time (switchboard/chatfile.py), else to the one the timeline says. An optional move waits for it, in its own
words ("a chat used Google Drive as X at 3:02 PM"); a forced move goes at once. A Claude Docs call that made a doc is
the doc's obligation only, never counted twice.
  SWITCHBOARD_HOME="$(mktemp -d)/data" taskpolicy -b python3 -m unittest discover -s tests -p 'test_autoswitch_connector_hold*.py'"""
import dataclasses
import json
import os
import unittest

from fakes_autoswitch import T0, FakeClock, Home, account
from fakes_hooks import (DOCS, DRIVE, LINEAR, ORG_A, ORG_B, ORG_C, ORG_X, doc_born, org_line, owner_line, said,
                             tool_result, tool_use)
from test_autoswitch_policy import CALM, reading

from switchboard.accounts import autoloop, policy, sessions

H, D = 3600.0, 86400.0
DOC = 'https://claude.ai/artifact/DocDocDocDocDocDoc5678'
ACCTS = (dataclasses.replace(account('a', 1), org_id=ORG_A), dataclasses.replace(account('b', 2), org_id=ORG_B),
         dataclasses.replace(account('c', 3), org_id=ORG_C))


class Scan(Home):
    def setUp(self):
        super().setUp()
        self.now = T0
        self.root = os.path.join(os.environ['CLAUDE_CONFIG_DIR'], 'projects')
        self.scanner = sessions.Scanner(root=self.root, procs=lambda: 0, clock=None)

    def write(self, rel, lines, age=30):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a') as f:
            for d in lines:
                f.write(json.dumps(d) + '\n')
        os.utime(path, (self.now - age, self.now - age))
        return path

    def look(self, timeline=((T0 - 9 * H, 'a'),)):
        return self.scanner.look(list(timeline), now=self.now)

    def connectors(self, timeline=((T0 - 9 * H, 'a'),)):
        return [o for o in self.look(timeline)['obligations'] if o['kind'] == 'connector']


class AConnectorInUse(Scan):
    def test_a_connector_used_in_the_last_half_hour_holds_with_its_name_time_and_account(self):
        self.write('proj/s1.jsonl', [tool_use(DRIVE, self.now - 600, 'u1'), tool_result('[]', self.now - 599, 'u1')])
        got = self.connectors()
        self.assertEqual(len(got), 1)
        ob = got[0]
        self.assertEqual((ob['kind'], ob['connector'], ob['account_id']), ('connector', 'Google Drive', 'a'))
        self.assertAlmostEqual(ob['at'], self.now - 600, delta=1)
        self.assertAlmostEqual(ob['until'], self.now - 600 + sessions.OBLIGATION_KEEP, delta=1)
        self.assertEqual(sorted(ob), ['account_id', 'at', 'connector', 'kind', 'org', 'until'])

    def test_one_used_forty_minutes_ago_does_not_and_the_newest_use_of_each_counts(self):
        self.write('proj/s1.jsonl', [tool_use(DRIVE, self.now - 2400, 'u1'), tool_use(LINEAR, self.now - 300, 'u2'),
                                     tool_use(LINEAR, self.now - 100, 'u3')])
        got = self.connectors()
        self.assertEqual([(o['connector'], round(self.now - o['at'])) for o in got], [('Linear', 100)])

    def test_its_account_is_the_one_the_chats_own_file_names_else_the_timeline(self):
        self.write('proj/s1.jsonl', [owner_line(ORG_A), org_line(ORG_A, self.now - 7000),
                                     org_line(ORG_C, self.now - 1200), tool_use(DRIVE, self.now - 600, 'u1')])
        self.write('proj/s2.jsonl', [said('no account line here', self.now - 900),
                                     tool_use(LINEAR, self.now - 500, 'u2')])
        got = dict((o['connector'], (o['org'], o['account_id'])) for o in self.connectors())
        self.assertEqual(got, {'Google Drive': (ORG_C, 'a'), 'Linear': (None, 'a')})

    def test_a_flutter_in_the_chats_file_is_not_its_account(self):
        self.write('proj/s1.jsonl', [org_line(ORG_A, self.now - 7000), org_line(ORG_B, self.now - 700),
                                     org_line(ORG_A, self.now - 680), tool_use(DRIVE, self.now - 690, 'u1')])
        self.assertEqual([o['org'] for o in self.connectors()], [ORG_A])

    def test_a_claude_doc_made_counts_once_as_the_doc_and_a_docs_read_as_a_connector(self):
        self.write('proj/s1.jsonl', [tool_use(DOCS, self.now - 600, 'u1'), doc_born(self.now - 599, DOC, 'u1'),
                                     tool_use('mcp__claude_ai_Claude_Docs__read', self.now - 300, 'u2'),
                                     tool_result('{"verdict":"ok","text":"..."}', self.now - 299, 'u2')])
        obs = self.look()['obligations']
        self.assertEqual(sorted((o['kind'], o.get('url') or o.get('connector')) for o in obs),
                         [('connector', 'Claude Docs'), ('doc', DOC)])
        made = [o for o in obs if o['kind'] == 'connector'][0]
        self.assertAlmostEqual(made['at'], self.now - 300, delta=1)          # the read, not the call that made it

    def test_tools_that_are_not_claude_ai_connectors_hold_nothing(self):
        self.write('proj/s1.jsonl', [tool_use('mcp__linear__list', self.now - 60, 'u1'),
                                     tool_use('mcp__claude-in-chrome__navigate', self.now - 50, 'u2'),
                                     tool_use('Bash', self.now - 40, 'u3'),
                                     said('I will call mcp__claude_ai_Google_Drive__search next', self.now - 30)])
        self.assertEqual(self.connectors(), [])

    def test_read_on_from_where_the_last_look_stopped(self):
        path = self.write('proj/s1.jsonl', [org_line(ORG_A, self.now - 7000)], age=900)
        self.assertEqual(self.connectors(), [])
        self.write('proj/s1.jsonl', [org_line(ORG_B, self.now - 400), tool_use(DRIVE, self.now - 300, 'u1')])
        self.assertEqual([(o['connector'], o['org']) for o in self.connectors()], [('Google Drive', ORG_B)])
        self.assertTrue(os.path.exists(path))

    def test_nothing_about_files_or_projects_comes_out(self):
        self.write('secret-project/s-9f1c.jsonl', [owner_line(ORG_A), org_line(ORG_A, self.now - 900),
                                                   tool_use(DRIVE, self.now - 60, 'u1')])
        text = json.dumps(self.look())
        for word in ('secret-project', 's-9f1c', self.tmp, '.jsonl', '/Users/example'):
            self.assertNotIn(word, text)


class TheSnapshotPassesItThrough(Home):
    def snapshot(self, obligations, active='a', accounts=ACCTS):
        activity = {'busy': False, 'idle_for_s': 1800.0, 'obligations': obligations, 'limited_at': None}
        return autoloop._snapshot(FakeClock(), policy.Settings(enabled=True), accounts, active, True, None, False,
                                  activity, False, False, 0.5, (True, 90.0), {}, None)

    def ob(self, org, account_id='a', kind='connector'):
        return {'account_id': account_id, 'org': org, 'kind': kind, 'connector': 'Google Drive', 'at': T0 - 60,
                'until': T0 + 1740}

    def test_the_chats_own_account_decides_whose_it_is(self):
        s = self.snapshot([self.ob(ORG_A), self.ob(ORG_C), self.ob(None), self.ob(None, account_id='b'),
                           self.ob(ORG_X)])
        self.assertEqual([(o['org'], o['account_id']) for o in s.obligations], [(ORG_A, 'a'), (None, 'a')])

    def test_an_organization_two_accounts_share_or_one_not_known_falls_back_to_the_timeline(self):
        accts = (ACCTS[0], dataclasses.replace(ACCTS[1], org_id=ORG_A), dataclasses.replace(ACCTS[2], org_id=None))
        s = self.snapshot([self.ob(ORG_A, account_id='a'), self.ob(ORG_X, account_id='a')], accounts=accts)
        self.assertEqual([o['account_id'] for o in s.obligations], ['a', 'a'])

    def test_obligations_with_no_org_field_are_read_like_org_not_known(self):
        old = {'account_id': 'a', 'kind': 'doc', 'url': 'u', 'at': T0 - 60, 'until': T0 + 1740}
        s = self.snapshot([old, dict(old, account_id='b'), dict(old, account_id=None)])
        self.assertEqual([o['account_id'] for o in s.obligations], ['a', None])


class TheMoveWaits(unittest.TestCase):
    def base(self):
        return {'a': reading('a', five=20.0, week=40.0, week_reset=T0 + 5 * D),
                'b': reading('b', five=10.0, week=50.0, week_reset=T0 + 2 * D),
                'c': reading('c', five=10.0, week=30.0, week_reset=T0 + 6 * D)}

    def snap(self, readings, **kw):
        base = dict(CALM)
        base.update(kw)
        return policy.Snapshot(now=T0, accounts=ACCTS, readings=readings, settings=policy.Settings(enabled=True),
                               active='a', **base)

    def test_an_optional_move_waits_in_its_own_words(self):
        ob = {'kind': 'connector', 'connector': 'Google Drive', 'at': T0 - 120, 'until': T0 + 1680,
              'account_id': 'a', 'org': ORG_A}
        d = policy.decide(self.snap(self.base(), obligations=(ob,)))
        self.assertEqual((d.action, d.kind, d.target), ('stay', 'optional', 'b'))
        self.assertEqual(d.hold, 'a chat used Google Drive as a@acct.example at %s' % policy._clock(T0 - 120, T0))
        d = policy.decide(self.snap(self.base(), obligations=(dict(ob, connector=None),)))
        self.assertEqual(d.hold, 'a chat used a claude.ai connector as a@acct.example at %s'
                         % policy._clock(T0 - 120, T0))

    def test_a_forced_move_goes_at_once(self):
        r = self.base()
        r['a'] = reading('a', five=95.0, week=40.0, week_reset=T0 + 5 * D)
        ob = {'kind': 'connector', 'connector': 'Google Drive', 'at': T0 - 60, 'until': T0 + 1740, 'account_id': 'a'}
        d = policy.decide(self.snap(r, obligations=(ob,)))
        self.assertEqual((d.action, d.kind), ('switch', 'forced'))

    def test_a_watch_is_a_chat_watching(self):
        d = policy.decide(self.snap(self.base(), obligations=({'kind': 'watch', 'url': 'u', 'at': T0 - 60},)))
        self.assertEqual(d.hold, 'a chat is watching an artifact as a@acct.example')


if __name__ == '__main__':
    unittest.main()
