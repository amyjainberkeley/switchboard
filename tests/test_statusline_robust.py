import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The status line against every odd input: a missing or
# broken file, names the page has not renewed for 20 min, a session on a profile of its own, no rate limits, a renamed
# field, a token where a name should be. Always one line, always exit 0, and quick.
# A throwaway home and data folder; the line runs in-process, and as Claude Code runs it (a shell) in a child.
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import statusline as sl

A, B = 'ana@work.example', 'ana@home.example'


def loaded():
    return os.getloadavg()[0] / (os.cpu_count() or 1)


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-line-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        self.data = os.path.join(self.home, '.switchboard')
        os.makedirs(self.data)
        self.env = {'HOME': self.home, 'SWITCHBOARD_HOME': self.data,
                    'CLAUDE_CONFIG_DIR': os.path.join(self.home, '.claude'), 'TZ': 'America/Los_Angeles'}
        env = mock.patch.dict(os.environ, self.env)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('SWITCHBOARD_STATE', None)
        os.environ.pop('NO_COLOR', None)
        time.tzset()
        self.addCleanup(time.tzset)
        self.now = time.mktime((2026, 11, 13, 10, 0, 0, 0, 0, -1))

    def state(self, raw=None, **st):
        with open(os.path.join(self.data, 'statusline.json'), 'w') as f:
            f.write(raw if raw is not None else json.dumps(st))

    def data_in(self, week=49, **more):
        d = dict({'session_id': 'sess-1', 'model': {'display_name': 'Opus'}}, **more)
        if week is not None:
            d['rate_limits'] = {'seven_day': {'used_percentage': week, 'resets_at': self.now + 3 * 86400}}
        return d

    def line(self, data, env=None, now=None):
        out = io.StringIO()
        code = sl.main(stdin=io.BytesIO(json.dumps(data).encode()), stdout=out,
                       env=dict(self.env, NO_COLOR='1', **(env or {})), now=self.now if now is None else now)
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertEqual(text.count('\n'), 1)
        return text.rstrip('\n')


class OddInputs(Sandbox):
    """The line against a missing file, broken JSON, an old file, another account's file, no rate limits."""

    def test_no_file_broken_json_and_a_file_that_is_not_an_object(self):
        self.assertEqual(self.line(self.data_in()), '● Opus · 51% left · refills Mon 10 AM')
        for raw in ('{"active": ', '[1, 2]', '', 'null', '{"active": 5, "next": ["x"], "at": "soon"}'):
            with self.subTest(raw=raw):
                self.state(raw)
                self.assertEqual(self.line(self.data_in()), '● Opus · 51% left · refills Mon 10 AM')

    def test_names_twenty_minutes_old_say_so(self):
        self.state(active=A, next=B, at=self.now - 20 * 60)
        self.assertEqual(self.line(self.data_in()),
                         '● %s (stale 20 min) on Opus · 51%% left · refills Mon 10 AM · next: %s' % (A, B))
        self.state(active=A, next=B, at=self.now - 3 * 3600)
        self.assertIn('(stale 3 h)', self.line(self.data_in()))
        self.state(active=A, next=B, at=self.now - 9 * 60)            # under ten minutes: fresh
        self.assertNotIn('stale', self.line(self.data_in()))
        self.state(active=A, next=B)                                  # an older page: no time, nothing said
        self.assertNotIn('stale', self.line(self.data_in()))

    def test_a_time_ahead_of_the_clock_is_never_trusted(self):
        self.state(active=A, at=self.now + 3600)                      # the clock was set back an hour
        self.assertEqual(self.line(self.data_in()), '● %s on Opus · 51%% left · refills Mon 10 AM' % A)

    def test_a_chat_on_a_profile_of_its_own_shows_its_own_account_never_the_switched_one(self):
        self.state(active=A, next=B, at=self.now)
        own = os.path.join(self.tmp, 'own-profile')
        os.makedirs(own)
        self.assertEqual(self.line(self.data_in(), env={'CLAUDE_CONFIG_DIR': own}),
                         '● its own login on Opus · 51% left · refills Mon 10 AM')
        with open(os.path.join(own, '.claude.json'), 'w') as f:
            json.dump({'oauthAccount': {'emailAddress': 'maya@ivy.example'}, 'projects': {}}, f)
        self.assertEqual(self.line(self.data_in(), env={'CLAUDE_CONFIG_DIR': own}),
                         '● maya@ivy.example on Opus · 51% left · refills Mon 10 AM')
        with open(os.path.join(own, '.config.json'), 'w') as f:      # Claude Code's own rule: this one wins
            json.dump({'oauthAccount': {'emailAddress': 'maya@other.example'}}, f)
        self.assertIn('maya@other.example', self.line(self.data_in(), env={'CLAUDE_CONFIG_DIR': own}))
        with open(os.path.join(own, '.config.json'), 'w') as f:      # big: never read on every refresh
            json.dump({'oauthAccount': {'emailAddress': 'maya@other.example'}, 'pad': 'x' * (sl.OWN_MAX + 1)}, f)
        self.assertIn('its own login', self.line(self.data_in(), env={'CLAUDE_CONFIG_DIR': own}))
        default = os.path.join(self.home, '.claude')                  # the default folder is not "its own"
        self.assertIn(A, self.line(self.data_in(), env={'CLAUDE_CONFIG_DIR': default}))

    def test_no_rate_limits_a_renamed_field_or_a_missing_block_is_still_a_line(self):
        self.state(active=A, next=B, at=self.now)
        shapes = {'no rate limits yet': self.data_in(week=None),
                  'a renamed field': dict(self.data_in(week=None), rateLimits={'seven_day': {'used_percentage': 9}}),
                  'a window with another shape': dict(self.data_in(week=None),
                                                      rate_limits={'seven_day': {'used': 9, 'resets': 'Monday'}}),
                  'no model': {'session_id': 'x'}, 'nothing at all': {}}
        for why, data in shapes.items():
            with self.subTest(why):
                text = self.line(data)
                self.assertTrue(text.startswith('● %s' % A), text)
                self.assertNotIn('left', text)

    def test_a_token_where_a_name_should_be_is_never_printed(self):
        for secret in ('sk-ant-oat01-' + 'Ab9' * 20, 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJlLWhlcmU',
                       'A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0'):
            with self.subTest(secret=secret[:12]):
                self.state(active=secret, next=secret, at=self.now)
                text = self.line(self.data_in())
                self.assertNotIn(secret, text)
                self.assertNotIn(secret[:12], text)
        self.assertFalse(sl.looks_secret(A))
        self.assertFalse(sl.looks_secret('ana.reyes.longname.example.org@northwind-holdings.example'))


class AlwaysExitZeroAndQuick(Sandbox):
    """As Claude Code runs it, every input above ends in one line and exit 0, quickly."""

    def sh(self, data, **env):
        cmd = sl.command(python=sys.executable)
        e = dict(os.environ, COLUMNS='200', NO_COLOR='1', **env)
        t0 = time.monotonic()
        r = subprocess.run(['/bin/sh', '-c', cmd], input=data, capture_output=True, env=e, timeout=60)
        return r, time.monotonic() - t0

    def test_every_odd_input_exits_0_with_one_line_and_no_traceback(self):
        inputs = [b'', b'{}', b'\x00\xff garbage', b'[]', b'{"rate_limits": "x"}',
                  json.dumps(self.data_in()).encode(), json.dumps(self.data_in(week=None)).encode()]
        for raw_state in (None, '{"active": ', json.dumps({'active': A, 'at': 0})):
            if raw_state is not None:
                self.state(raw_state)
            for data in inputs:
                with self.subTest(state=raw_state, data=data[:20]):
                    r, _ = self.sh(data)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertEqual(r.stderr, b'')
                    self.assertEqual(r.stdout.decode('utf-8').count('\n'), 1)

    def test_the_work_takes_under_150_ms_and_a_whole_run_stays_light(self):
        self.state(active=A, next=B, at=self.now - 1200)
        data = self.data_in()
        took = []
        for _ in range(20):
            t0 = time.monotonic()
            self.line(data)
            took.append(time.monotonic() - t0)
        self.assertLess(min(took), 0.15, took)                       # the line's own work
        if loaded() < 2:
            self.assertLess(sorted(took)[len(took) // 2], 0.15, took)
        # A whole run: Python's start, the line, the exit. These tests run under taskpolicy -b, whose children start at
        # background priority on the slow cores (Claude Code runs the line at its own priority), so here the bound only
        # catches a line that grew heavy (the whole package imported, a file read that waits).
        runs = [self.sh(json.dumps(data).encode())[1] for _ in range(3)]
        self.assertLess(min(runs), 1.0 if loaded() < 2 else 5.0, runs)


class TheStatusFileItReads(Sandbox):
    """The page renews statusline.json's time every five minutes (server.write_status_file): the line's "stale" is a
    page that stopped, never a quiet afternoon."""

    def test_a_file_renewed_four_minutes_ago_is_fresh(self):
        self.state(active=A, next=None, at=self.now - 240)
        self.assertEqual(self.line(self.data_in()), '● %s on Opus · 51%% left · refills Mon 10 AM' % A)


if __name__ == '__main__':
    unittest.main()
