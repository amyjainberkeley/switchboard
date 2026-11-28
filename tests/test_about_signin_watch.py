import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# `switchboard about` names the sign-in watch among what runs, as the installer's own line just did ("The sign-in
# watch is running, and starts at every login."): its job's property list is there and starts at load, and its
# heartbeat is fresh (server.watch_beat). A watch whose job is there but does not beat is said in one line, with what
# starts it again. An install without the watch's job says what it said before. The watch's property list is one of
# the files `Runs as` checks are yours.
#
# Made-up install in a throwaway home, as tests/test_about.py makes it. Nothing runs, launchd is not asked.
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import about, core, menubar, server  # noqa: E402
import test_about as ta  # noqa: E402

PORT = ta.PORT


class TheSignInWatch(ta.Install):
    def watch_job(self):
        agents = os.path.join(self.home, 'Library', 'LaunchAgents')
        cmd = os.path.join(self.data, 'bin', 'switchboard-watch')
        ta.write(cmd, '#!/bin/sh\n', 0o755)
        ta.write_plist(os.path.join(agents, server.WATCH_JOB + '.plist'),
                       {'Label': server.WATCH_JOB, 'ProgramArguments': [cmd], 'RunAtLoad': True})

    def beat(self, **more):
        core.write_json(core.data_path(server.WATCH_BEAT), dict({'beat_at': time.time(), 'pid': os.getpid()}, **more))

    def running(self, status='running'):
        return about.running_words(about.running(ta.page(status)))

    def test_a_watch_that_beats_is_named_with_the_others(self):
        self.watch_job()
        self.beat()
        self.assertEqual(self.running(), ['the menu bar item, the sign-in watch, and the page on 127.0.0.1:%s (this '
                                          'Mac only); all start at login' % PORT])

    def test_a_watch_whose_job_is_there_but_does_not_beat_is_said(self):
        self.watch_job()
        self.beat(stopped_at=time.time())
        self.assertEqual(self.running(), ['the menu bar item, and the page on 127.0.0.1:%s (this Mac only); all '
                                          'start at login' % PORT,
                                          'the sign-in watch is not running: the page starts it again'])

    def test_only_some_jobs_start_at_login(self):
        self.watch_job()
        self.beat()
        os.remove(os.path.join(self.home, 'Library', 'LaunchAgents', menubar.AGENT + '.plist'))
        self.assertEqual(self.running()[0], 'the menu bar item, the sign-in watch, and the page on 127.0.0.1:%s '
                                            '(this Mac only); the page and the sign-in watch start at login' % PORT)

    def test_without_its_job_nothing_changes(self):
        self.beat()
        self.assertEqual(self.running(), ['the menu bar item, and the page on 127.0.0.1:%s (this Mac only); both '
                                          'start at login' % PORT])

    def test_its_property_list_is_one_of_the_files_checked(self):
        self.assertIn(server.WATCH_JOB, about._agents())


if __name__ == '__main__':
    unittest.main()
