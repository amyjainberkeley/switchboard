import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""The "made under another account, it is not deleted" note: only for the Artifact tools and Claude Docs, only when the
call named a claude.ai artifact link, and once per link per move of account.

Any other claude.ai connector (Google Drive, Gmail, Calendar) can answer "not found" about a file, an email or a page
that really is missing; that says nothing about another account, so it gets no word. Hook inputs follow Claude Code's
own shapes (tests/fakes_hooks.py); every id and name is made up.
  SWITCHBOARD_HOME="$(mktemp -d)/data" python3 -m unittest discover -s tests -p 'test_hooks_session_not_found_scope.py'"""
import unittest

from fakes_links import A, B, C, ID1, ID2, LINK
from fakes_hooks import DRIVE, answered
from test_hooks_session import NOW, HookSandbox

UNKNOWN = LINK % ('u' * 20)         # a link the owner map does not know
GMAIL = 'mcp__claude_ai_Gmail__search_threads'


class OnlyItemTools(HookSandbox):
    def setUp(self):
        super().setUp()
        self.owners(active=B, timeline=[[A, NOW - 7200], [B, NOW - 600]])

    def test_another_connector_saying_not_found_gets_no_word(self):
        for tool in (DRIVE, GMAIL):
            for text in ('File not found: Q3 plan.pdf', 'HTTP 404 page not found', 'Error: not_found'):
                with self.subTest(tool=tool, text=text):
                    self.assertIsNone(self.hook(dict(self.event('PostToolUse'), **answered(tool, text, sid='s-1'))))
                    self.assertIsNone(self.hook(self.event('PostToolUseFailure', tool_name=tool,
                                                           tool_input={'url': UNKNOWN}, error=text)))

    def test_an_item_tool_with_no_artifact_link_gets_no_word(self):
        for tool, inp in (('Artifact', {'action': 'list'}), ('ArtifactData', {'url': 'https://example.com/x'}),
                          ('mcp__claude_ai_Claude_Docs__query', {'q': 'plan'})):
            with self.subTest(tool=tool):
                self.assertIsNone(self.hook(self.event('PostToolUseFailure', tool_name=tool, tool_input=inp,
                                                       error='artifact not found')))

    def test_an_item_tool_with_a_link_is_told_once_per_link_per_move(self):
        def fail(url, sid='s-1', now=NOW):
            return self.hook(self.event('PostToolUseFailure', sid=sid, tool_name='Artifact',
                                        tool_input={'action': 'read', 'url': url}, error='artifact not found'), now=now)
        said = fail(UNKNOWN)
        self.assertIn('Switchboard: Claude Code moves between the person\'s Claude accounts (now on %s). An artifact '
                      'made under another of them reads as "not found" from here. That does not mean it was deleted, '
                      'and no new login is needed, so do not run /login or /logout.' % B, said)   # its words as ever
        self.assertIsNone(fail(UNKNOWN, now=NOW + 60))                  # the same link again: said already
        self.assertIn('belongs to %s' % A, fail(LINK % ID1, now=NOW + 90))  # another link
        self.assertIsNone(fail(LINK % ID1, now=NOW + 95))
        self.assertIsNotNone(fail(UNKNOWN, sid='s-2', now=NOW + 100))   # another chat hears it too
        self.owners(active=C, timeline=[[A, NOW - 7200], [B, NOW - 600], [C, NOW + 200]])
        self.assertIn('now on %s' % C, fail(UNKNOWN, now=NOW + 300))   # a move since: said again, once
        self.assertIsNone(fail(UNKNOWN, now=NOW + 360))

    def test_a_claude_doc_of_the_account_in_use_gets_no_word(self):
        self.assertIsNone(self.hook(self.event('PostToolUse', tool_name='mcp__claude_ai_Claude_Docs__read',
                                               tool_input={'container': {'kind': 'project', 'id': ID2}},
                                               tool_response=[{'type': 'text', 'text': 'Refused: not found'}])))

    def test_a_claude_doc_by_its_container_id_owned_elsewhere_is_told(self):
        said = self.hook(self.event('PostToolUse', tool_name='mcp__claude_ai_Claude_Docs__read',
                                    tool_input={'container': {'kind': 'project', 'id': ID1}},
                                    tool_response=[{'type': 'text', 'text': 'Refused: not found'}]))
        self.assertIn('this Claude Doc belongs to %s' % A, said)


if __name__ == '__main__':
    unittest.main()
