import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Codex is asked about ONCE in one flow, whichever road reaches it. A first run (`switchboard setup`), a bare
# `switchboard add` (with the page running, and without it) and `switchboard add codex` all end in the providers
# code's own add: its notice (what pressing Codex's row sends, to whom, under whose terms) is said once, and kept on a
# yes; the question "Add Codex too?" is asked once by the first run and by bare add, and never by `add codex`, which
# is the person's yes already. A no adds nothing and is not asked again in the same flow. After a yes on any road,
# nothing says the notice or asks again: not another bare add or first run, not `add codex`, not the first press on
# Codex's row.
#
# Every road runs the real code from `switchboard.__main__` to `switchboard.providers.cli`. The Mac under it is the
# stand-in of tests/test_setup_first_run_terminal.py (the page, the engine) and of tests/fakes_provider_kit.py (Claude
# and Codex, signed in; neither is ever run). Made-up accounts only.
import io
import os
import sys
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import __main__ as cli  # noqa: E402
from switchboard.providers import cli as providers_cli  # noqa: E402
from switchboard.providers import words as provider_words  # noqa: E402
from test_keep import B, C  # noqa: E402
from test_setup_first_run_terminal import (CODEX_NOTICE, CODEX_OFFER, CODEX_SHOW, REAL_ADD_SIGNED_IN,  # noqa: E402
                                           REAL_PROVIDERS_MAIN, STEP_NOTICE, WAIT, FakeTty, Setup, cli_free_port,
                                           over)

PRESS_NOTICE = provider_words.PRESS_NOTICE.format(name='Codex', vendor='OpenAI', plan='ChatGPT plan')

# Each road, with the answer given to "Add Codex too?" (None: add codex asks no such question).
ROADS = (('setup', 'y'), ('setup', 'n'),
         ('add, the page running', 'y'), ('add, the page running', 'n'),
         ('add codex', None),
         ('add, the page not running', 'y'), ('add, the page not running', 'n'))     # last: they stop the page


class CodexOnceOnEveryRoad(Setup):
    def setUp(self):
        super().setUp()
        self.no_link_app()                                  # the link step has its own tests
        self.patch(providers_cli, 'main', REAL_PROVIDERS_MAIN)     # the fixture's default_kit: its stand-in Mac
        self.next_account = [B, C]

    def road(self, name, answer):
        """Walk one road from its start, on a stand-in Mac where Codex is here, signed in and not added: the
        stand-in Mac and what was printed."""
        self.kit_rig, self.codex, self.offered = None, None, []
        self.with_codex()
        r = self.providers_kit(answers=('y', 'n') if answer == 'y' else ('n',) if answer == 'n' else ())
        if name == 'setup':
            code, out = self.setup('s')                         # no other Claude account
        elif name == 'add, the page running':
            self.page.sign_in(WAIT, over(self.next_account[0]), got=self.next_account.pop(0))
            code, out = self.setup('s', args=('add',))          # one more account, then no other
        elif name == 'add codex':
            code, out = self.run_cli('add', 'codex', tty=FakeTty([]))
        else:
            self.page.stop()
            os.environ['SWITCHBOARD_PORT'] = str(cli_free_port())

            def stop(seconds):
                if seconds == cli.POLL:
                    raise KeyboardInterrupt                     # the person stops at the first /login step
            self.patch(cli, '_sleep', stop)
            code, out = self.setup(args=('add',))
        self.assertEqual(code, 0, out)
        return r, out

    def test_the_notice_once_and_the_question_at_most_once_on_every_road(self):
        for name, answer in ROADS:
            with self.subTest(road=name, answer=answer):
                r, out = self.road(name, answer)
                asks = 0 if answer is None else 1
                added = answer != 'n'
                notice = STEP_NOTICE if name == 'setup' else CODEX_NOTICE     # inside setup's step: indented
                self.assertEqual(out.count(notice), 1, out)                        # said once
                self.assertEqual((r.asked.count(CODEX_OFFER), out.count(CODEX_OFFER)), (asks, asks), out)
                self.assertLessEqual(r.asked.count(CODEX_SHOW), 1)
                self.assertEqual(self.offered, [None] * asks)                       # through the one call site
                self.assertEqual(('codex' in r.choices.added(), r.choices.notice_said('codex')), (added, added))
                if not added:
                    continue
                # Nothing says it or asks it again: another bare add or first run, add codex, a press on its row.
                asked = list(r.asked)
                with redirect_stdout(io.StringIO()) as again:
                    self.assertEqual(REAL_ADD_SIGNED_IN(r.kit), 0)
                    self.assertEqual(REAL_PROVIDERS_MAIN(['add', 'codex'], kit=r.kit), 0)
                    self.assertEqual(providers_cli.move('codex', kit=r.kit), 0)
                self.assertEqual(r.asked, asked)
                self.assertNotIn(CODEX_NOTICE, again.getvalue())
                self.assertNotIn(PRESS_NOTICE, again.getvalue())
                self.assertEqual(len(r.calls.execs), 1, again.getvalue())        # the press went on to Codex


if __name__ == '__main__':
    unittest.main()
