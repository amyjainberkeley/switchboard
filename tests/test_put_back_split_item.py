import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Claude Code 2.1.331 can keep its Keychain item in a split form (behind a server flag): the item carries
# 'keychainOverflow' and the account login can sit in another item, so the item holds no login although Claude Code is
# signed in. The put-back never takes that for a lost sign-in. Stand-ins only; made-up *.example accounts.
import json
import unittest

from test_put_back_engine import MCP, Base
from switchboard.accounts import putback

SPLIT = dict(MCP, keychainOverflow={'items': ['made-up-1', 'made-up-2']})


class TheSplitForm(Base):
    def test_an_item_in_the_split_form_is_never_written_and_it_is_said_once(self):
        text = json.dumps(SPLIT, separators=(',', ':'))
        self.rig.set_live(text)
        lines = []
        for _ in range(30):
            res = self.look(putback.PUT_BACK_AFTER_S + 1)
            self.assertEqual(res.action, 'leave', res.reason)
            if res.line:
                lines.append(res.line)
        self.assertEqual(self.live_writes(), 0)
        self.assertEqual(self.rig.live_text(), text)
        self.assertEqual(len(lines), 1, lines)
        self.assertEqual(len([x for x in self.rig.logs if 'split Keychain item' in x]), 1, self.rig.logs)
        self.assertEqual(self.repairs(), [])
        self.assertEqual(self.rig.vault('uuid-b'), self.saved_b)

    def test_a_real_loss_after_it_is_still_put_back(self):
        self.rig.set_live(json.dumps(SPLIT, separators=(',', ':')))
        self.look()
        self.lose_login()
        self.look(0)
        res = self.look(putback.PUT_BACK_AFTER_S + 1)
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertEqual(len(self.repairs()), 1)


if __name__ == '__main__':
    unittest.main()
