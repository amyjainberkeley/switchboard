import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Claude Code 2.1.331 can keep its Keychain item in a split form (behind a server flag): the item carries a pointer,
# 'keychainOverflow', to overflow items that hold the parts that grow (connector logins). An ordinary account's login
# stays in the item itself; only a pointer that carries 'login' means the login sits elsewhere ('sealed', never put back
# over). So an item with the pointer, no login, and no 'login' in the pointer is a lost sign-in like any other, and the
# put-back keeps the item's own pointer. Stand-ins only; made-up *.example accounts.
import json
import unittest

from test_put_back_engine import MCP, Base
from switchboard.accounts import putback

SPLIT = dict(MCP, keychainOverflow={'items': ['made-up-1', 'made-up-2']})


class TheSplitForm(Base):
    def test_an_item_with_the_pointer_and_no_login_is_put_back_with_its_own_pointer(self):
        text = json.dumps(SPLIT, separators=(',', ':'))
        self.rig.set_live(text)
        first = self.look(0)
        self.assertEqual((first.action, first.watch), ('wait', True))
        res = self.look(putback.PUT_BACK_AFTER_S + 1)
        self.assertEqual(res.action, 'put_back', res.reason)
        blob = json.loads(self.rig.live_text())
        self.assertEqual(blob['claudeAiOauth'], json.loads(self.saved_b)['claudeAiOauth'])
        self.assertEqual(blob['keychainOverflow'], SPLIT['keychainOverflow'])     # the item's own, as it was
        self.assertEqual(blob['mcpOAuth'], MCP['mcpOAuth'])
        self.assertEqual(len(self.repairs()), 1)
        self.assertEqual(self.rig.vault('uuid-b'), self.saved_b)
        self.assertFalse([x for x in self.rig.logs if 'split Keychain item' in x], self.rig.logs)

    def test_a_real_loss_after_it_is_still_put_back(self):
        self.rig.set_live(json.dumps(SPLIT, separators=(',', ':')))
        self.look()
        self.lose_login()
        self.look(0)
        res = self.look(putback.PUT_BACK_AFTER_S + 1)
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertEqual(len(self.repairs()), 1)

    def test_a_chosen_sign_out_beside_the_pointer_is_never_undone(self):
        self.rig.set_live(json.dumps(SPLIT, separators=(',', ':')))
        self.rig.set_config(None)
        for _ in range(30):
            self.assertEqual(self.look().action, 'leave')
        self.assertEqual(self.live_writes(), 0)


if __name__ == '__main__':
    unittest.main()
