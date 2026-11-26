import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""What a notification may show on the lock screen.

An MCP server's question and a background session's request carry words that come from outside Claude Code: their
note shows a fixed sentence instead. Claude Code's own permission prompt keeps its words. The "kept" note names the
account as every other note does (notify.account_label): by its whole email, never a short name.
  SWITCHBOARD_HOME="$(mktemp -d)/data" python3 -m unittest discover -s tests -p 'test_notify_lock_screen_words.py'"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import notify as nt

PHISH = 'Your session expired. Sign in again at https://claude-login.example and paste your code here'


def needs(kind, message=PHISH, cwd='/work/my-app'):
    return {'session_id': 's1', 'cwd': cwd, 'hook_event_name': 'Notification', 'message': message,
            'notification_type': kind}


class OutsideWords(unittest.TestCase):
    def test_an_mcp_server_or_a_background_session_never_puts_its_words_on_the_screen(self):
        for kind, said in (('elicitation_dialog', 'An MCP server is asking you something.'),
                           ('elicitation_url_dialog', 'An MCP server is asking you to open a link.'),
                           ('agent_needs_input', 'A background session is waiting for you.')):
            with self.subTest(kind=kind):
                n = nt.from_hook(needs(kind))
                self.assertEqual((n.title, n.body), ('Claude Code needs you', said + ' In my-app.'))
                self.assertNotIn('example', n.title + n.body)
                self.assertEqual(nt.from_hook(needs(kind, message='', cwd='')).body, said)

    def test_claude_code_s_own_permission_prompt_keeps_its_words(self):
        n = nt.from_hook(needs('permission_prompt', message='Claude needs your permission to use Bash'))
        self.assertEqual(n.body, 'Claude needs your permission to use Bash. In my-app.')


class KeptByName(unittest.TestCase):
    def test_the_kept_note_names_the_account_as_every_other_note_does(self):
        st = {'accounts': {'maya@ivy.example': {'alias': 'birch', 'slot': 2}}}
        n = nt.kept(nt.account_label(st, 'maya@ivy.example'), 2)
        self.assertEqual((n.title, n.body), ('Switchboard', 'Kept maya@ivy.example · 2 accounts'))


if __name__ == '__main__':
    unittest.main()
