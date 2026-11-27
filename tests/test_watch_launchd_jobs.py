import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# What launchd is told, pinned. Switchboard stays up because launchd starts the page again whenever it exits (its
# own watchdog ends a page that hangs, and counts on that), and starts the menu bar again whenever it exits (its menu
# has no Quit; `switchboard menubar off` takes its job out). Those promises are a few keys in the two job descriptions
# install.sh writes. This reads them from install.sh itself (install-check never adds the jobs) and fails when a key
# that must stay is lost or changed. A job or a key added later is not a failure.
import os
import plistlib
import re
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)

from switchboard import menubar, server, watch


def install_sh():
    with open(os.path.join(ROOT, 'install.sh'), encoding='utf-8') as f:
        return f.read()


def job(text, var):
    """The job description install.sh writes to "$<var>", as a dict. Its shell parts ($LABEL, $(xml "$DATA/...")) are
    replaced by plain words: only the keys and their fixed values are read."""
    m = re.search(r'cat > "\$%s" <<EOF\n(.*?)\nEOF\n' % var, text, re.S)
    if m is None:
        raise AssertionError('install.sh writes no job to $%s' % var)
    body = re.sub(r'\$\(xml "[^"]*"\)', 'x', m.group(1))
    body = re.sub(r'\$[A-Z_]+', 'x', body)
    return plistlib.loads(body.encode('utf-8'))


class ThePageJob(unittest.TestCase):
    def setUp(self):
        self.page = job(install_sh(), 'PLIST')

    def test_it_starts_at_login(self):
        self.assertIs(self.page.get('RunAtLoad'), True)

    def test_it_is_started_again_after_any_exit(self):
        # KeepAlive true: a crash, the page's own watchdog (watch.EXIT_STUCK), no Python for the loop
        # (watch.EXIT_PYTHON) and a clean exit all bring a fresh page. A rule like SuccessfulExit false would leave the
        # page down after a clean exit.
        self.assertIs(self.page.get('KeepAlive'), True)

    def test_it_waits_twenty_seconds_between_two_starts(self):
        # The menu bar's watch waits far longer than this before it starts the page itself, so a page launchd is about
        # to start is never started twice.
        self.assertEqual(self.page.get('ThrottleInterval'), 20)
        self.assertGreater(watch.PAGE_DOWN_FOR, 3 * self.page['ThrottleInterval'])

    def test_it_gets_a_minute_to_stop(self):
        # On SIGTERM the page stops its loop (up to 45 s, engine.AutoHost.STOP_WAIT) before it exits.
        self.assertEqual(self.page.get('ExitTimeOut'), 60)
        self.assertLess(server.engine.AutoHost.STOP_WAIT, self.page['ExitTimeOut'])

    def test_its_label_is_the_one_the_menu_bar_starts(self):
        self.assertIn('LABEL="%s"' % menubar.PAGE_AGENT, install_sh())


class TheMenuBarJob(unittest.TestCase):
    def setUp(self):
        self.bar = job(install_sh(), 'MENUBAR_PLIST')

    def test_it_comes_back_after_any_exit(self):
        # KeepAlive true: a crash, a clean exit, or anything else that ends it, and launchd starts it again within its
        # ThrottleInterval. Only `switchboard menubar off` keeps it off, by taking the job out (menubar_keep.turn_off).
        self.assertIs(self.bar.get('KeepAlive'), True)
        self.assertEqual(self.bar.get('ThrottleInterval'), 20)

    def test_it_starts_at_login(self):
        self.assertIs(self.bar.get('RunAtLoad'), True)

    def test_its_label_is_the_one_the_page_starts(self):
        self.assertIn('MENUBAR_LABEL="$LABEL.menubar"', install_sh())
        self.assertEqual(menubar.PAGE_AGENT + '.menubar', menubar.AGENT)


class TheReaderCanFail(unittest.TestCase):
    """The pins above would be worth nothing if the reader passed on anything: here it is shown a copy of install.sh
    with a key lost or changed, and the same checks fail."""

    def test_a_lost_keepalive_is_seen(self):
        text = install_sh().replace('  <key>KeepAlive</key><true/>\n', '', 1)
        self.assertIsNone(job(text, 'PLIST').get('KeepAlive'))

    def test_a_changed_menu_bar_rule_is_seen(self):
        text = install_sh()
        at = text.index('cat > "$MENUBAR_PLIST" <<EOF')
        text = text[:at] + text[at:].replace('<key>KeepAlive</key><true/>',
                                             '<key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>', 1)
        self.assertEqual(job(text, 'MENUBAR_PLIST').get('KeepAlive'), {'SuccessfulExit': False})

    def test_a_job_that_is_not_written_is_seen(self):
        with self.assertRaises(AssertionError):
            job(install_sh().replace('cat > "$PLIST" <<EOF', 'cat > "$ELSEWHERE" <<EOF'), 'PLIST')

    def test_an_added_key_is_no_failure(self):
        text = install_sh().replace('  <key>RunAtLoad</key><true/>\n', '  <key>RunAtLoad</key><true/>\n'
                                    '  <key>LowPriorityIO</key><true/>\n', 1)
        page = job(text, 'PLIST')
        self.assertIs(page.get('KeepAlive'), True)
        self.assertIs(page.get('LowPriorityIO'), True)


if __name__ == '__main__':
    unittest.main()
