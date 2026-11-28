"""Stand-ins for the sign-in hold's tests (putback.hold_seen, putback.hold_tidy, api.watch_hold): one made-up Mac on
the account engine's stand-ins (fakes_accounts) with a fake clock, Claude Code's moves on its plain login file
(its fallback write, another process's removal, a sign-out, a sign-in inside Claude Code), and the sign-in watch's
calls as it makes them. Made-up *.example accounts; every plain login file lives in the test's throwaway home.

Nothing here touches the Keychain, launchd, Claude Code or a real home folder.
"""
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from fakes_autoswitch import FakeClock
from fakes_accounts import Rig, identity_block
from switchboard.accounts import api, autoloop, context, facts, putback
from switchboard.accounts import signin_watch as sw

MCP = {'mcpOAuth': {'docs-server': {'accessToken': 'mcp-made-up-1', 'expiresAt': 1}}}
A = ('uuid-a', 'ana@work.example', 'org-a')
B = ('uuid-b', 'ana@home.example', 'org-b')


class Killed(BaseException):
    """The process stopped at this point (launchd's SIGKILL, a crash): nothing after it runs."""


class Base(unittest.TestCase):
    MIRROR = False                  # the standing copy of the sign-in (fakes_accounts.Rig's `mirror`)

    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='hold-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.tmp, 'data')
        os.makedirs(os.environ['SWITCHBOARD_HOME'])
        self.clock = FakeClock(wall=time.time(), mono=time.monotonic())
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono, sleep=self.clock.sleep, mirror=self.MIRROR)
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        putback.reset()
        putback._KEPT.clear()
        self.rig.keep(*A)
        self.clock.advance(1.0)
        self.b_text = self.rig.keep(*B, siblings=MCP)       # Claude Code is on B
        self.cred = facts.credentials_file(self.rig.env, self.rig.home)
        self.calls_before = len(self.rig.kc.calls)
        self.posts_before = len(self.rig.server.requests)
        patcher = mock.patch.object(autoloop, 'notify_put_back', lambda res: None)     # no notification in tests
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.use.__exit__(None, None, None)
        putback.reset()
        putback._KEPT.clear()
        self.rig.close()
        if self._old_home is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    # Claude Code's side
    def cc_writes(self, text=None, age=0.0):
        """Claude Code's fallback after a refused save: its whole object into the plain login file (written `age`
        seconds ago by the fake clock)."""
        text = self.rig.live_text() if text is None else text
        with open(self.cred, 'w') as f:
            f.write(text)
        os.chmod(self.cred, 0o600)
        t = self.clock.wall() - age
        os.utime(self.cred, (t, t))
        return text

    def cc_removes(self):
        """Another Claude Code process, whose save landed after it read "no such item", removes the file."""
        os.unlink(self.cred)

    def cc_deletes_item(self):
        """The refused process's own delete of the Keychain item, after its fallback write."""
        self.rig.sec.drop(self.rig.service, self.rig.account)

    def settings_name(self, who):
        self.rig.set_config(identity_block(*who) if who else None)

    # the sign-in watch's calls
    def hold(self, event):
        return api.watch_hold(event)

    def look(self, after=0.0, **hint):
        self.clock.advance(after)
        h = dict(sw.no_hint())
        h.update(hint)
        if 'login_file_written' not in hint:
            h['login_file_written'] = h['login_file'] in ('appeared', 'changed')
        return api.watch_look(h)

    # what is there
    def file_text(self):
        try:
            with open(self.cred) as f:
                return f.read()
        except FileNotFoundError:
            return None

    def note(self):
        try:
            with open(self.ctx.path(putback.HOLD_FILE)) as f:
                return json.load(f)
        except FileNotFoundError:
            return None

    def leftovers(self):
        """Names of ours beside Claude Code's plain login file (temporary files)."""
        return [n for n in os.listdir(os.path.dirname(self.cred)) if n.endswith('.hold')]

    def ours(self):
        """The plain login file is one the hold put there (its stamp is the note's)."""
        st = os.stat(self.cred) if os.path.exists(self.cred) else None
        note = self.note()
        return st is not None and note is not None and \
            note.get('stamp') == [st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size]

    def holder_text(self):
        return (putback._HOLDER.get(self.ctx.data_dir) or {}).get('text')

    def kept_then_gone(self, text=None, age=0.0):
        """Claude Code writes the file, the watch tells the hold, another process removes it, the watch tells the
        hold. Returns (what Claude Code wrote, the hold's answer to 'gone')."""
        text = self.cc_writes(text, age=age)
        self.assertEqual(self.hold('appeared')['did'], 'kept')
        self.clock.advance(0.18)
        self.cc_removes()
        return text, self.hold('gone')

    def assert_no_keychain_or_network(self):
        self.assertEqual(self.rig.kc.calls[self.calls_before:], [])
        self.assertEqual(self.rig.server.requests[self.posts_before:], [])

    def assert_no_secret_in(self, *things):
        for t in things:
            text = t if isinstance(t, str) else json.dumps(t, default=str)
            self.assertNotIn('sk-ant-', text)
            self.assertNotIn('mcp-made-up', text)


def stamp_of(path):
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    return (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)


class MiniWatch(object):
    """The sign-in watch's two calls, as it makes them: notice() compares Claude Code's plain login file, its settings
    and its Keychain item with the last notice and tells the hold at once (settings first, then the file: 'appeared',
    'changed' or 'gone'), as the watch does in its own loop; a file the hold says is its own is dropped from the hint.
    look() hands the hint gathered since the last look to the engine's look (api.watch_look), then notices what the
    look itself changed. answers: every answer of the hold, as (event, dict)."""

    def __init__(self, rig, clock, hold=None, look=None):
        self.rig, self.clock = rig, clock
        self.hold = hold or api.watch_hold
        self.look_fn = look or api.watch_look
        self.cred = facts.credentials_file(rig.env, rig.home)
        self.file = stamp_of(self.cred)
        self.settings = self._settings()
        self.item = rig.live_text()
        self.pending = None
        self.answers = []

    def _settings(self):
        try:
            with open(self.rig.config_path(), 'rb') as f:
                return f.read()
        except FileNotFoundError:
            return None

    def notice(self, files_only=False):
        """files_only: the settings' change (if any) is left for a later notice, as when it lands after the watch
        compared the stamps of this cycle."""
        hint = dict(sw.no_hint())
        now = self._settings()
        if now != self.settings and not files_only:
            self.settings = now
            hint['settings'] = True
            self.answers.append(('settings', self.hold('settings')))
        f = stamp_of(self.cred)
        old, self.file = self.file, f           # what the hold puts there is seen at the next notice
        ev = 'appeared' if old is None and f is not None else 'gone' if old is not None and f is None else \
            'changed' if old != f else None
        if ev is not None:
            out = self.hold(ev)
            self.answers.append((ev, out))
            if ev != 'gone' and isinstance(out, dict) and out.get('ours'):
                ev = None                       # the hold's own file: no change for the look
            if ev == 'gone' and isinstance(out, dict) and out.get('did') == 'held':
                # the link wakes the watch's kqueue at once: its next compare sees the file, and the hold says it is
                # its own
                self.file = stamp_of(self.cred)
                self.answers.append(('appeared', self.hold('appeared')))
        hint['login_file'] = ev
        hint['login_file_written'] = ev in ('appeared', 'changed')
        item = self.rig.live_text()
        if item != self.item:
            self.item = item
            hint['keychain'] = True
        if hint['settings'] or hint['login_file'] or hint['keychain']:
            self.pending = sw.merge(self.pending, hint)
        return hint

    def look(self, after=0.0):
        self.clock.advance(after)
        self.notice()
        hint, self.pending = self.pending or dict(sw.no_hint()), None
        res = self.look_fn(hint)
        self.notice()
        return res
