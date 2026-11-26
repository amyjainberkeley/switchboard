import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""A claude.ai connector that stops answering after a move.

Every connector call made within an hour after a move can fail, in Claude Code's own words
(`MCP server "claude.ai Google Drive" session expired`, `... is not connected`, `... connection closed before the
tool call ...`), with no word to the chat. The session hooks take every mcp__claude_ai_* tool: a connector's
answer is noted in the chat's own record (when it last answered here), and a failure with a move behind it gets, once
per connector per move: which account the chat is on, that connectors are added per account on claude.ai, whether
that account has had the connector answer on this Mac (chats.json: yes with when, no, or not known), where it last
answered, and the way through (`switchboard run`, or /mcp for the person). Never /login, never "removed". A failure
with no move behind it gets nothing.

Hook inputs follow Claude Code 2.1.325's own shapes (tests/fakes_hooks.py); every id and name is made up."""
import json
import os
import re
import time
import unittest
from unittest import mock

from fakes_links import A, B, C
from fakes_hooks import (DOCS, DRIVE, LINEAR, ORG_A, ORG_B, ORG_C, WORDINGS, CHAT, ChatFile, answered, failure,
                             org_line, owner_line, said, write_index)
from test_hooks_session import NOW, HookSandbox, Settings

from switchboard import session_hooks

NEVER = ('removed', 'sign in again', 'log in again', 'reinstall', 'run /login.', 'run /login to', 'then /login')
OLD = '^(Artifact|ArtifactComments|ArtifactData|mcp__claude_ai_Claude_Docs__.*)$'
NEW = '^(Artifact|ArtifactComments|ArtifactData|mcp__claude_ai_.*)$'


def when(t, now=NOW):
    return session_hooks._when(t, now)


class ConnectorCase(HookSandbox):
    """A chat that started on A, used Google Drive there, and was moved to B (Switchboard at NOW - 600; its own next
    request went as B at NOW - 500)."""

    def setUp(self):
        super().setUp()
        self.chat = ChatFile(os.path.join(self.home, '.claude', 'projects'))
        self.connectors = {ORG_A: {'Google Drive': NOW - 5000, 'Linear': NOW - 4000},
                           ORG_B: {'Google Drive': NOW - 90000}}
        write_index(self.data, connectors=self.connectors, at=NOW - 30)

    def ev(self, event, sid=CHAT):
        return dict(event, session_id=sid, transcript_path=self.chat.path)

    def say(self, event, sid=CHAT, now=NOW, env=None):
        got = self.hook(self.ev(event, sid), now=now, env=env)
        for word in NEVER:
            self.assertNotIn(word, (got or '').lower())
        return got

    def moved(self, used=True):
        self.chat.add(said('hello', NOW - 7300), owner_line(ORG_A), org_line(ORG_A, NOW - 7200))
        self.owners(active=A, timeline=[[A, NOW - 9000]])
        self.hook(self.ev(self.event('SessionStart', source='startup')), now=NOW - 7100)
        if used:
            self.assertIsNone(self.say(answered(DRIVE), now=NOW - 5000))       # it answered here, as A
        self.owners(active=B, timeline=[[A, NOW - 9000], [B, NOW - 600]])
        self.chat.add(said('go on', NOW - 510), org_line(ORG_B, NOW - 500))


class WhatAFailedConnectorIsTold(ConnectorCase):
    def test_every_wording_on_both_events(self):
        self.moved(used=False)
        n = 0
        for words in WORDINGS:
            kind = 'session expired' if 'expired' in words else 'not connected' if 'not connected' in words else \
                'connection closed before the tool call'
            for ev in (failure(DRIVE, words), answered(DRIVE, words % 'Google Drive', error=True),
                       answered(DRIVE, words % 'Google Drive')):
                n += 1
                with self.subTest(words=words, event=ev['hook_event_name'], error='isError' in str(ev)):
                    got = self.say(ev, sid='chat-%d' % n)
                    self.assertIn('Switchboard: Google Drive did not answer here (%s).' % kind, got)
                    self.assertIn('This chat has been on %s since %s, and was on %s before. claude.ai connectors are '
                                  'added per account on claude.ai.' % (B, when(NOW - 500), A), got)
                    self.assertIn('No new login is needed: do not run /login or /logout.', got)
        self.assertEqual(n, 15)

    def test_the_account_it_is_on_has_had_it_answer_here(self):
        self.moved()
        got = self.say(failure(DRIVE, WORDINGS[0]))
        self.assertIn('Google Drive last answered in this chat at %s, as %s. This chat has been on %s since %s, and '
                      'was on %s before. claude.ai connectors are added per account on claude.ai.'
                      % (when(NOW - 5000), A, B, when(NOW - 500), A), got)
        self.assertIn('%s has had Google Drive answer on this Mac (last at %s), so the person can reconnect it with '
                      '/mcp.' % (B, when(NOW - 90000)), got)
        self.assertNotIn('last answered under', got)       # the record's newest answer elsewhere is this chat's own
        self.assertIn('To use it as %s now, without moving anything: `switchboard run %s -- -p "<request>"`.'
                      % (A, A), got)

    def test_the_account_it_is_on_has_not(self):
        del self.connectors[ORG_B]
        write_index(self.data, connectors=self.connectors, at=NOW - 30)
        self.moved()
        got = self.say(failure(DRIVE, WORDINGS[1]))
        self.assertIn('%s has not had Google Drive answer on this Mac in the last 7 days (as of %s); if %s has it on '
                      'claude.ai, the person can reconnect it with /mcp.' % (B, when(NOW - 30), B), got)
        self.assertIn('Google Drive last answered in this chat at %s, as %s.' % (when(NOW - 5000), A), got)

    def test_with_no_record_it_is_not_known(self):
        write_index(self.data, connectors=None)
        with open(os.path.join(self.data, 'chats.json')) as f:
            doc = json.load(f)
        del doc['connectors']
        with open(os.path.join(self.data, 'chats.json'), 'w') as f:
            json.dump(doc, f)
        self.moved()
        got = self.say(failure(DRIVE, WORDINGS[3]))
        self.assertIn('Whether %s has Google Drive is not known here; if it does, the person can reconnect it with '
                      '/mcp.' % B, got)
        self.assertNotIn('last answered under', got)
        self.assertIn('`switchboard run %s -- -p "<request>"`' % A, got)    # it answered in this chat as A

    def test_without_the_pages_look_switchboards_own_timeline_says_the_move(self):
        os.unlink(os.path.join(self.data, 'chats.json'))
        self.moved()
        got = self.say(failure(DRIVE, WORDINGS[0]))
        self.assertIn('Google Drive last answered in this chat at %s. Switchboard moved Claude Code from %s to %s at '
                      '%s.' % (when(NOW - 5000), A, B, when(NOW - 600)), got)
        self.assertIn('Whether %s has Google Drive is not known here' % B, got)
        self.assertNotIn('switchboard run', got)          # no record names the account it answered under

    def test_a_chat_behind_the_clock_says_where_its_last_request_went(self):
        self.chat.add(owner_line(ORG_A), org_line(ORG_A, NOW - 7200))
        self.owners(active=A, timeline=[[A, NOW - 9000]])
        self.hook(self.ev(self.event('SessionStart', source='startup')), now=NOW - 7100)
        self.owners(active=B, timeline=[[A, NOW - 9000], [B, NOW - 60]])
        got = self.say(failure(DRIVE, WORDINGS[0]))
        self.assertIn('Switchboard moved Claude Code from %s to %s at %s (this chat\'s last request was still under '
                      '%s)' % (A, B, when(NOW - 60), A), got)

    def test_the_newest_move_counts_when_switchboard_moved_after_the_chats_own_last_move(self):
        self.chat.add(owner_line(ORG_A), org_line(ORG_A, NOW - 30000), org_line(ORG_C, NOW - 20000))
        self.owners(active=C, timeline=[[A, NOW - 40000], [C, NOW - 20100]])
        self.hook(self.ev(self.event('SessionStart', source='startup')), now=NOW - 25000)
        self.assertIsNone(self.say(answered(DRIVE), now=NOW - 10000))          # it answered here, as C
        self.owners(active=B, timeline=[[A, NOW - 40000], [C, NOW - 20100], [B, NOW - 60]])
        got = self.say(failure(DRIVE, WORDINGS[0]))
        self.assertIn('Google Drive last answered in this chat at %s, as %s.' % (when(NOW - 10000), C), got)
        self.assertIn('Google Drive last answered under %s at %s.' % (A, when(NOW - 5000)), got)
        self.assertIn("Switchboard moved Claude Code from %s to %s at %s (this chat's last request was still under %s)."
                      % (C, B, when(NOW - 60), C), got)

    def test_once_per_connector_per_move(self):
        self.moved()
        self.assertIsNotNone(self.say(failure(DRIVE, WORDINGS[0])))
        self.assertIsNone(self.say(failure(DRIVE, WORDINGS[1]), now=NOW + 30))
        got = self.say(failure(LINEAR, WORDINGS[1], name='Linear'), now=NOW + 40)
        self.assertIn('Switchboard: Linear did not answer here (not connected).', got)
        self.assertIn('Linear last answered under %s at %s.' % (A, when(NOW - 4000, NOW + 40)), got)
        self.owners(active=C, timeline=[[A, NOW - 9000], [B, NOW - 600], [C, NOW + 1000]])
        self.chat.add(org_line(ORG_C, NOW + 1100))
        got = self.say(failure(DRIVE, WORDINGS[0]), now=NOW + 1300)
        self.assertIn('This chat has been on %s since %s, and was on %s before.' % (C, when(NOW + 1100, NOW + 1300), B),
                      got)

    def test_the_total_stays_within_the_cap(self):
        long_a, long_b = 'a' * 110 + '@work.example', 'b' * 110 + '@home.example'
        write_index(self.data, orgs={ORG_A: long_a, ORG_B: long_b}, connectors={ORG_B: {'Z' * 80: NOW - 100}})
        self.chat.add(owner_line(ORG_A), org_line(ORG_A, NOW - 7200), org_line(ORG_B, NOW - 500))
        self.owners(active=long_b, timeline=[[long_a, NOW - 9000], [long_b, NOW - 600]])
        got = self.say(failure('mcp__claude_ai_' + 'Z' * 80 + '__x', WORDINGS[4], name='Z' * 80))
        self.assertLessEqual(len(got), session_hooks.CAP)
        self.assertTrue(got.endswith('do not run /login or /logout.'), got[-80:])


class OnlyWhatIsKnown(ConnectorCase):
    """A "no" only over a record that read every chat of its window, with the window named; the
    way through names only an account the record shows the connector answered under; and account words only when
    (a) it answered in this chat before the chat's last move and not since, (b) the whole record shows it under
    another account and never under this one, or (c) the move was within the hour. Facts, in order, never a cause."""

    def old_move(self, used=False, days=3):
        """The chat started on A, (when `used`) Linear answered in it, and it moved to B `days` days ago."""
        t = NOW - days * 86400
        self.chat.add(said('hello', t - 300), owner_line(ORG_A), org_line(ORG_A, t - 200))
        self.owners(active=A, timeline=[[A, t - 9000]])
        self.hook(self.ev(self.event('SessionStart', source='startup')), now=t - 100)
        if used:
            self.assertIsNone(self.say(answered(LINEAR), now=t - 50))
        self.owners(active=B, timeline=[[A, t - 9000], [B, t + 400]])
        self.chat.add(said('go on', t + 500), org_line(ORG_B, t + 500))
        return t + 500

    def test_a_no_needs_a_record_that_read_every_chat_and_names_its_window(self):
        write_index(self.data, connectors={ORG_A: {'Linear': NOW - 4000}}, at=NOW - 30, done=False)
        self.moved(used=False)
        got = self.say(failure(LINEAR, WORDINGS[1], name='Linear'))
        self.assertIn('Whether %s has Linear is not known here' % B, got)
        self.assertNotIn('has not had', got)
        write_index(self.data, connectors={ORG_A: {'Linear': NOW - 4000}}, at=NOW - 30)
        got = self.say(failure(LINEAR, WORDINGS[1], name='Linear'), sid='chat-2')
        self.assertIn('%s has not had Linear answer on this Mac in the last 7 days (as of %s); if %s has it on '
                      'claude.ai, the person can reconnect it with /mcp.' % (B, when(NOW - 30), B), got)
        self.assertNotIn('answer on this Mac;', got)

    def test_the_way_through_names_only_an_account_the_record_shows_it_answered_under(self):
        # The review's t3: nothing shows Linear ever answered under the account the chat moved from.
        write_index(self.data, connectors={}, at=NOW - 30, done=False)
        self.moved(used=False)
        got = self.say(failure(LINEAR, WORDINGS[1], name='Linear'))
        self.assertIn('Switchboard: Linear did not answer here (not connected).', got)
        self.assertNotIn('switchboard run', got)

    def test_an_old_move_with_nothing_else_known_gets_no_word(self):
        # The review's t3b: a move three days ago, the connector never answered in this chat, and the record says
        # nothing that sets the accounts apart: Claude Code's own error stands alone.
        write_index(self.data, connectors={ORG_B: {'Google Drive': NOW - 90000}}, at=NOW - 30)
        self.old_move()
        self.assertIsNone(self.say(failure(LINEAR, WORDINGS[1], name='Linear')))
        self.assertIsNone(self.say(failure(DRIVE, WORDINGS[0])))
        self.assertNotIn('ctold', self.record())

    def test_an_old_move_is_told_when_it_answered_in_this_chat_before_it(self):
        write_index(self.data, connectors={}, at=NOW - 30, done=False)
        t = self.old_move(used=True)
        got = self.say(failure(LINEAR, WORDINGS[1], name='Linear'))
        self.assertIn('Linear last answered in this chat at %s, as %s.' % (when(NOW - 3 * 86400 - 50), A), got)
        self.assertIn('This chat has been on %s since %s, and was on %s before.' % (B, when(t), A), got)
        self.assertIn('To use it as %s now' % A, got)

    def test_an_old_move_is_told_when_the_whole_record_shows_it_only_under_another_account(self):
        write_index(self.data, connectors={ORG_A: {'Linear': NOW - 4000}}, at=NOW - 30)
        t = self.old_move()
        got = self.say(failure(LINEAR, WORDINGS[1], name='Linear'))
        self.assertIn('Linear last answered under %s at %s.' % (A, when(NOW - 4000)), got)
        self.assertIn('%s has not had Linear answer on this Mac in the last 7 days (as of %s)' % (B, when(NOW - 30)),
                      got)
        self.assertIn('This chat has been on %s since %s, and was on %s before.' % (B, when(t), A), got)
        self.assertIn('To use it as %s now' % A, got)

    def test_facts_in_order_and_never_a_cause(self):
        self.moved()
        got = self.say(failure(DRIVE, WORDINGS[0]))
        order = ['did not answer here', 'last answered in this chat', 'This chat has been on', 'are added per account',
                 'has had Google Drive answer', 'To use it as', 'No new login']
        at = [got.find(x) for x in order]
        self.assertNotIn(-1, at, got)
        self.assertEqual(at, sorted(at), got)
        for cause in ('after Google Drive last answered', 'moved from', 'because', 'caused'):
            self.assertNotIn(cause, got)

    def record(self):
        with open(os.path.join(self.data, 'hooks', CHAT + '.json')) as f:
            return json.load(f)


class NothingWhenNoMoveIsBehindIt(ConnectorCase):
    def test_a_chat_that_never_moved(self):
        self.chat.add(owner_line(ORG_A), org_line(ORG_A, NOW - 7200))
        self.owners(active=A, timeline=[[A, NOW - 9000]])
        self.hook(self.ev(self.event('SessionStart', source='startup')), now=NOW - 7100)
        for words in WORDINGS:
            self.assertIsNone(self.say(failure(DRIVE, words)))

    def test_it_answered_here_after_the_move(self):
        self.moved()
        self.assertIsNone(self.say(answered(DRIVE), now=NOW - 100))            # it worked as B
        self.assertIsNone(self.say(failure(DRIVE, WORDINGS[0])))               # an expiry on the same account

    def test_a_helper_agent_or_a_chat_on_its_own_login_hears_nothing_new(self):
        self.moved()
        self.assertIsNone(self.say(failure(DRIVE, WORDINGS[0], agent_id='agent-1', agent_type='Explore')))
        own = {'CLAUDE_CONFIG_DIR': os.path.join(self.tmp, 'own-login')}
        self.assertIsNone(self.say(failure(DRIVE, WORDINGS[0]), sid='own-1', env=own))
        self.assertIsNotNone(self.say(failure(DRIVE, WORDINGS[0])))         # the chat itself still does

    def test_a_helpers_answer_counts_as_answered_here(self):
        self.moved()
        self.assertIsNone(self.say(answered(DRIVE, agent_id='agent-1'), now=NOW - 100))
        self.assertIsNone(self.say(failure(DRIVE, WORDINGS[0])))

    def test_words_that_only_quote_the_failure_or_name_another_connector_are_no_failure(self):
        self.moved()
        body = 'x' * 500 + ' MCP server "claude.ai Google Drive" session expired'
        self.assertIsNone(self.say(answered(DRIVE, body)))
        self.assertIsNone(self.say(failure(DRIVE, 'MCP server "claude.ai Slack" session expired')))
        self.assertIsNone(self.say(failure(DRIVE, 'Input validation error: query is required')))
        self.assertIsNone(self.say(failure('mcp__linear__list', WORDINGS[0], name='Linear')))

    def test_a_claude_doc_refused_after_a_move_names_its_owner(self):
        self.moved()
        self.owners(active=B, timeline=[[A, NOW - 9000], [B, NOW - 600]],
                    artifacts_={'d' * 20: {'o': A, 'p': 'P1', 's': 'seen'}})
        got = self.say(dict(self.event('PostToolUseFailure'), tool_name=DOCS,
                            tool_input={'container': {'kind': 'project', 'id': 'd' * 20}},
                            error='Refused: this artifact is in another organization (other_org).'))
        self.assertIn('this Claude Doc belongs to %s' % A, got)


class TheUsualAnswerStaysCheap(ConnectorCase):
    def test_an_answer_reads_no_map_and_no_chat_file(self):
        self.moved(used=False)
        with mock.patch.object(session_hooks, 'owners', side_effect=AssertionError('the map is not read')), \
                mock.patch.object(session_hooks.chatfile, 'read', side_effect=AssertionError('no chat file read')):
            began = time.process_time()
            for i in range(20):
                self.assertIsNone(self.say(answered(DRIVE), now=NOW + i))
            took = time.process_time() - began
        self.assertLess(took, 0.25)
        with open(os.path.join(self.data, 'hooks', CHAT + '.json')) as f:
            self.assertEqual(json.load(f)['ok']['Google Drive'], NOW + 19)


class TheMatcherTakesEveryConnector(Settings):
    def test_every_claude_ai_connector_and_the_item_tools_and_nothing_else(self):
        self.on()
        obj = json.loads(self.read())
        for e in ('PostToolUse', 'PostToolUseFailure'):
            self.assertEqual(obj['hooks'][e][0]['matcher'], NEW)
        for tool in (DRIVE, LINEAR, DOCS, 'mcp__claude_ai_Claude_Docs__read', 'Artifact', 'ArtifactComments',
                     'ArtifactData'):
            self.assertTrue(re.match(NEW, tool), tool)
        for tool in ('mcp__claude-in-chrome__navigate', 'Bash', 'mcp__linear__list', 'ArtifactX', 'xArtifact'):
            self.assertIsNone(re.match(NEW, tool), tool)
        self.assertTrue(session_hooks.status(self.path)['current'])

    def test_an_install_by_the_older_version_is_brought_up_to_date_and_still_comes_out_exactly(self):
        mine = ('{\n  "model": "opus",\n  "hooks": {\n    "PostToolUse": [\n      {\n        "matcher": "Write|Edit",\n'
                '        "hooks": [{"type": "command", "command": "prettier"}]\n      }\n    ]\n  }\n}\n')
        self.write(mine)
        with mock.patch.dict(session_hooks.MATCHERS, {'PostToolUse': OLD, 'PostToolUseFailure': OLD}):
            self.on()
        older = self.read()
        s = session_hooks.status(self.path)
        self.assertEqual((s['in'], s['works'], s['current']), (True, True, False))
        from switchboard import __main__ as cli
        self.assertEqual(cli.hooks_line(s), "Session hooks in Claude Code: on, from an older version that doesn't "
                                            "hear claude.ai connectors. Run: switchboard hooks on")
        plan = session_hooks.install_plan(self.path, command_line=session_hooks.command())
        self.assertTrue(plan['ok'] and plan['change'])
        session_hooks.apply(plan)
        self.assertEqual(self.read(), older.replace(json.dumps(OLD), json.dumps(NEW)))   # only the two matchers
        self.assertTrue(session_hooks.status(self.path)['current'])
        self.assertEqual(cli.hooks_line(session_hooks.status(self.path)), 'Session hooks in Claude Code: on')
        self.assertFalse(session_hooks.install_plan(self.path, command_line=session_hooks.command())['change'])
        self.off()
        self.assertEqual(self.read(), mine)                                       # exactly as it was

    def test_upgrade_by_itself_only_when_they_are_in_and_not_turned_off(self):
        self.assertFalse(session_hooks.upgrade())                        # not in: nothing is put in
        self.assertIsNone(self.read())
        with mock.patch.dict(session_hooks.MATCHERS, {'PostToolUse': OLD, 'PostToolUseFailure': OLD}):
            self.on()
        os.makedirs(self.data, exist_ok=True)
        with open(os.path.join(self.data, 'settings.json'), 'w') as f:
            json.dump({'session_hooks': False}, f)
        before = self.read()
        self.assertFalse(session_hooks.upgrade())                        # turned off in Switchboard: left alone
        self.assertEqual(self.read(), before)
        os.unlink(os.path.join(self.data, 'settings.json'))
        self.assertTrue(session_hooks.upgrade())
        self.assertTrue(session_hooks.status(self.path)['current'])
        self.assertFalse(session_hooks.upgrade())
        self.off()
        self.assertIsNone(self.read())

    def test_a_group_you_share_with_them_keeps_its_matcher(self):
        cmd = session_hooks.command()
        shared = {'hooks': {e: [dict(({'matcher': OLD} if e in session_hooks.MATCHERS else {}),
                                     hooks=[{'type': 'command', 'command': cmd, 'timeout': 10},
                                            {'type': 'command', 'command': 'mine'}])]
                            for e in session_hooks.EVENTS}}
        self.write(json.dumps(shared, indent=2))
        self.assertTrue(session_hooks.status(self.path)['current'])        # nothing of theirs alone to bring up
        plan = session_hooks.install_plan(self.path, command_line=cmd)
        if plan.get('change'):
            session_hooks.apply(plan)
        obj = json.loads(self.read())
        self.assertEqual(obj['hooks']['PostToolUse'][0]['matcher'], OLD)          # yours too: not changed


if __name__ == '__main__':
    unittest.main()
