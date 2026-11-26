import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The installer's own words for a first install:
# - the PATH line it prints puts ~/.local/bin after everything else, for zsh, bash and fish: Claude Code's own
#   installer can leave an older `claude` in ~/.local/bin, and putting the folder first would change which claude the
#   person's terminal runs (install-check case 8 runs the zsh line for real);
# - the slow step, the account engine, says how long it can take;
# - before the background jobs are added for the first time, one line says to choose Allow if macOS asks.
# install-check never adds the background jobs (--no-agent everywhere), so the last two are read from install.sh.
# And one rule for "in 3 hours" on the page and in the terminal.
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)

from switchboard import term
import test_app  # its headless-Chrome helper


def install_sh():
    with open(os.path.join(ROOT, 'install.sh'), encoding='utf-8') as f:
        return f.read()


class ThePathLine(unittest.TestCase):
    def test_every_shell_puts_local_bin_last(self):
        text = install_sh()
        hows = dict(re.findall(r'^\s*(bash|fish|\*)\) PATH_HOW="(.*)" ;;$', text, re.M))
        self.assertEqual(set(hows), {'bash', 'fish', '*'})
        for shell in ('bash', '*'):
            self.assertIn('export PATH=\\"\\$PATH:\\$HOME/.local/bin\\"', hows[shell], shell)
            self.assertNotIn('.local/bin:\\$PATH', hows[shell], shell)
        self.assertEqual(hows['fish'], 'fish_add_path --append ~/.local/bin')


class TheFirstInstallSaysWhatToExpect(unittest.TestCase):
    def test_the_slow_steps_say_what_they_are(self):
        # The engine is part of the program, so the slow steps are the downloads: Switchboard's own Python
        # (its size and where it is kept) and the menu bar's packages.
        text = install_sh()
        self.assertIn('begin "Downloading Switchboard\'s own Python $PBS_VERSION, kept in $(short "$DATA") (27 MB, once)"', text)
        self.assertIn('begin "Adding the menu bar item"', text)
        self.assertNotIn('Setting up the account engine', text)

    def test_before_the_jobs_are_first_added_it_says_to_choose_allow(self):
        text = install_sh()
        start = text.index('if [ "$AGENT" = 1 ]; then\n  mkdir -p "$HOME/Library/LaunchAgents"')
        block = text[start:text.index('launchctl bootstrap', start)]
        line = re.search(r'\[ -e "\$PLIST" \] \|\| say "If macOS asks, choose Allow\."', block)
        self.assertTrue(line, 'the line, said only when the page job is new, before its plist is written')
        self.assertLess(line.start(), block.index('cat > "$PLIST"'))


class LaunchServicesOnlyForTheAccountsOwnInstall(unittest.TestCase):
    """A try-out install in a throwaway home, or with a test's own Applications folder
    (SWITCHBOARD_APPLICATIONS), told this Mac's Launch Services about its Switchboard.app (lsregister -f), so macOS
    could list a second app with the menu bar app's bundle id beside the real one. Launch Services belongs to the user
    account, not to a home folder: only the account's own install tells it, and never with --no-agent."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-ls-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        os.makedirs(self.home)

    def ls(self, says, agent='1', apps=None):
        """LS as install.sh works it out, run in sh with a stand-in dscl that prints `says` (None: dscl fails)."""
        text = install_sh()
        start = text.index('LS="$AGENT"\n')
        block = text[start:text.index('\n', text.index('[ -n "$OWN_HOME" ]', start)) + 1]
        bin_dir = os.path.join(self.tmp, 'bin')
        os.makedirs(bin_dir, exist_ok=True)
        with open(os.path.join(bin_dir, 'dscl'), 'w') as f:
            f.write('#!/bin/sh\n' + ("printf '%%s\\n' %s\n" % shlex.quote(says) if says is not None else 'exit 1\n'))
        os.chmod(os.path.join(bin_dir, 'dscl'), 0o755)
        env = {'PATH': bin_dir + ':/usr/bin:/bin', 'HOME': self.home, 'AGENT': agent}
        if apps:
            env['SWITCHBOARD_APPLICATIONS'] = apps
        r = subprocess.run(['/bin/sh', '-c', block + 'printf %s "$LS"'], env=env, capture_output=True, text=True, timeout=30)
        return r.stdout

    def test_the_accounts_own_install_tells_it(self):
        self.assertEqual(self.ls('NFSHomeDirectory: ' + self.home), '1')
        self.assertEqual(self.ls('NFSHomeDirectory:\n ' + self.home), '1')          # dscl's form for a long value

    def test_a_try_out_never_does(self):
        self.assertEqual(self.ls('NFSHomeDirectory: ' + self.home, agent='0'), '0')                     # --no-agent
        self.assertEqual(self.ls('NFSHomeDirectory: ' + self.home, apps=os.path.join(self.tmp, 'A')), '0')
        self.assertEqual(self.ls('NFSHomeDirectory: /Users/someone'), '0')          # HOME is not the account's
        self.assertEqual(self.ls(None), '0')                                        # the account's home unknown

    def test_every_call_to_launch_services_asks_first(self):
        text = install_sh()
        calls = [line for line in text.splitlines() if '"$LSREGISTER"' in line]
        self.assertTrue(calls)
        for line in calls:
            self.assertIn('[ "$LS" = 0 ] ||', line)
        self.assertIn('if [ "$LS" = 1 ]; then REGISTER=""; else REGISTER="--no-register"; fi', text)   # the link app


def page_until():
    """The page's own words for the time left: its plural() and until(), as written in web/index.html."""
    with open(os.path.join(ROOT, 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
        text = f.read()
    plural = text[text.index('const plural = '):text.index('\n', text.index('const plural = '))]
    start = text.index('function until(ms) {')
    return plural + '\n' + text[start:text.index('\n}\n', start) + 2]


class TheResetSaysTheSameTimeLeftEverywhere(test_app.ChromePage):
    """Anthropic sends a 7:00 reset as 6:59:59.65. The terminal takes it to the nearest minute before it counts the
    time left; the page did that for the clock only, so at the half hour the page said "in 2 hours" where the
    terminal said "in 3 hours" (and "in 1 day" for its "in 2 days"). The page's own until(), run in Chrome on a set
    clock, against term.until on the same numbers."""

    def test_the_page_counts_from_the_same_minute_as_the_terminal(self):
        n = 1794999960                                          # a whole minute
        cases = [[n, n + 150 * 60 - 0.35],                      # 2.5 hours, sent a moment early: both say 3
                 [n, n + 36 * 3600 - 0.35],                     # 1.5 days, the same: both say 2
                 [n, n + 150 * 60 + 0.4],                       # a moment late
                 [n + 25, n + 150 * 60],                        # 2 h 29 min 35 s: both say 2
                 [n, n + 29 * 60 + 29.6]]                       # minutes
        got = self.run_page("""
      const src = %s, cases = %s;
      done(cases.map(([now, t]) => new Function('nowMs', src + '\\nreturn until;')(() => now * 1000)(t * 1000)));
""" % (json.dumps(page_until()), json.dumps(cases)))
        self.assertEqual(got, [term.until(t, now) for now, t in cases])
        self.assertEqual(got[:2], ['in 3 hours', 'in 2 days'])


if __name__ == '__main__':
    unittest.main()
