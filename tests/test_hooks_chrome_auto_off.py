import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A need switch for Claude in Chrome happens only while automatic switching is on: Switchboard never switches by
# itself while auto-switch is off. The session hook reads whether it is on from the page's owner map;
# while it is off it asks the engine for nothing and tells the session plainly how the person can switch.
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..'))

from fakes_links import A, B
from test_hooks_session import HookSandbox

STORE = 'https://chromewebstore.google.com/detail/claude/fcoeoabgfenejglbffodgkkbkcdhcgfn'
CHROME = {'profiles': {'P1': {'extension': False, 'account': A}, 'P2': {'extension': True, 'account': B}},
          'host': True, 'store': STORE}


class ChromeWhileAutoIsOff(HookSandbox):
    def chrome_event(self, sid='s-1'):
        return self.event('PreToolUse', sid=sid, tool_name='mcp__claude-in-chrome__navigate', tool_input={'url': 'x'})

    def test_auto_switch_off_asks_for_no_switch_and_says_how_to_switch(self):
        for auto in (False, None):                  # off, or a map from before it said
            with self.subTest(auto=auto):
                self.owners(chrome=CHROME, auto=auto)
                said = self.hook(self.chrome_event(sid='s-%s' % auto),
                                 ask=lambda *a: self.fail('asked for a switch while auto-switch is off'))
                self.assertIn('Automatic switching is off, so Switchboard does not move Claude Code there by itself',
                              said)
                self.assertIn('`switchboard switch %s`' % B, said)
                self.assertIn('Do not run /login or /logout', said)
                self.assertIn('--profile-directory="P1"', said)
                self.assertFalse(os.path.exists(os.path.join(self.data, 'hooks', 'chrome-need.json')))

    def test_with_auto_switch_on_it_asks_for_a_switch(self):
        self.owners(chrome=CHROME, auto=True)
        asked = []
        self.hook(self.chrome_event(), ask=lambda accounts, followers, why_of: asked.append(followers) or (B, object()))
        self.assertEqual(asked, [[B]])


if __name__ == '__main__':
    unittest.main()
