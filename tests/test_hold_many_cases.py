import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Thousands of made-up interleavings, from a fixed seed, of what happens to Claude Code's plain login file: its
# fallback writes (a full login, or an object without one), removals by other Claude Code processes, the item's
# delete, saves that read the item else the file, chosen sign-outs and sign-ins inside Claude Code (each in two steps
# with anything between), switches by Switchboard, the sign-in watch telling the hold at random moments, looks of the
# watch and of the loop, restarts of the watch, a watch stopped between its pending note and its link or between its
# link and its settled note, a file that appears just before the link, and the setting turned off and on. After every
# step:
#   (a) a file of ours never outlives a sign-out by more than one event: once the settings name nobody, the watch's
#       next notice leaves no file of ours, and none is put in place while they name nobody;
#   (b) never more than HOLD_MAX files put back after one fill of the holder;
#   (c) what is put in place is byte for byte a text Claude Code wrote into the file within the last HOLD_FOR_S,
#       never one a write of Switchboard's own into the item rewrote the file with (when the watch saw a write and,
#       at its next notice, only the file's removal, it is the write it saw, not a later one it did not see), or a
#       spare copy of the sign-in a look makes: the item's own text, or the saved login of the account the settings
#       name that a put-back is about to write (these count toward neither (b) nor (c)'s seconds);
#   (d) never over an existing file;
#   (e) no login text in a log line, a History line, the sign-in record, signin-hold.json or an answer;
#   (f) with the Keychain item holding an account login and one look made, no file of ours is there HOLD_TIDY_S or
#       longer after its note's time (when Claude Code wrote its bytes or the item was last known absent; a younger
#       one stays: the item may be deleted a moment after the look read it);
#   (g) a file of ours is never promoted by the first look that finds it (a look with no earlier sighting of the
#       loss of the named account on record: the engine's rule of two looks);
#   (h) with the setting off, no file is ever put in place;
#   (i) no look leaves Claude Code without a login anywhere when it had one (in its item or in its plain login file)
#       and its settings name an account before and after the look.
# And at the end, quiet (looks for longer than a file of ours may stay beside an item): no temporary name of ours is
# left, signin-hold.json names the file there or is gone, and no file of ours is left beside an item that holds a
# login.
# Stand-ins only (fakes_accounts, an in-process Keychain and a made-up Anthropic); made-up *.example accounts.
import json
import os
import random
import shutil
import tempfile
import time
import unittest
from unittest import mock

from hold_fakes import A, B, MCP, Killed, MiniWatch, stamp_of
from fakes_autoswitch import FakeClock
from fakes_accounts import Rig, identity_block
from switchboard.accounts import api, autoloop, ccfiles, context, facts, live, putback, switcher
from switchboard.accounts import signin_watch as sw

CASES = 1500
SEED = 4646
ACCOUNTS = {'uuid-a': A, 'uuid-b': B}


def dumps(blob):
    return json.dumps(blob, separators=(',', ':'))


class World(object):
    def __init__(self, rng, tmp):
        self.rng = rng
        os.environ['SWITCHBOARD_HOME'] = os.path.join(tmp, 'data')
        os.makedirs(os.environ['SWITCHBOARD_HOME'], exist_ok=True)
        self.clock = FakeClock(wall=time.time() + rng.uniform(0, 1000), mono=50000.0)
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono, sleep=self.clock.sleep)
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        putback.reset()
        putback._KEPT.clear()
        self.rig.keep(*A)
        self.clock.advance(1.0)
        self.rig.keep(*B, siblings=MCP)
        self.cred = facts.credentials_file(self.rig.env, self.rig.home)
        self.broken = []
        self.removed = 0                    # files of ours taken away
        self.bumped = 0                     # plain login files a write of ours into the item rewrote
        self.killed = 0                     # watches stopped part-way through a put-back
        self.leftover = 0                   # notices that found a file of ours while the settings named nobody
        self.answers = []
        self.signing_out = self.signing_in = None
        self.writes = []                    # (text, wall) of each write into the file by a writer that is not the hold
        self.made_since_fill = 0
        self.kill = None                    # 'before' or 'after': the next link stops the watch there
        self.race = False                   # the next link meets a file Claude Code wrote a moment before
        self.off = rng.random() < 0.15      # the setting off for the whole case (h)
        self.ever_on = not self.off
        if self.off:
            self.set_hold(False)
        self.watch = MiniWatch(self.rig, self.clock, hold=self.hold)
        self._patch()

    # ---------- the stand-ins' hooks ----------
    def _patch(self):
        real_link = putback._link
        world = self

        def link(src, dst):
            with open(src) as f:
                text = f.read()
            if world.race:
                world.race = False
                theirs = dumps(dict(MCP, discoveryState={'race': world.rng.randint(1, 10 ** 6)}))
                with open(dst, 'w') as f:
                    f.write(theirs)
                world.wrote(theirs)
            pre = stamp_of(dst)
            if world.kill == 'before':
                world.kill = None
                world.killed += 1
                raise Killed()
            if not putback.hold_on(world.ctx):
                world.fail('(h) a file put in place with the setting off')
            if not world.named():
                world.fail('(a) a file put in place while the settings name nobody')
            kept = any(text == t and 0 <= world.clock.wall() - at < putback.HOLD_FOR_S for t, at in world.writes)
            if not kept and not world.spare_text(text):
                world.fail('(c) put in place bytes not written into the file in the last %g s' % putback.HOLD_FOR_S)
            real_link(src, dst)                 # raises when a file is there
            if pre is not None:
                world.fail('(d) a file put in place over one that was there')
            world.made_since_fill += kept
            if world.made_since_fill > putback.HOLD_MAX:
                world.fail('(b) %d files put back after one fill' % world.made_since_fill)
            if world.kill == 'after':
                world.kill = None
                world.killed += 1
                raise Killed()
        self._real_link = real_link
        putback._link = link
        real_bump = ccfiles.bump_credentials_file

        def bump(env, home, text, log=None, announce=None):
            stamp = real_bump(env, home, text, log=log, announce=announce)
            world.bumped += bool(stamp is not None)     # a rewrite by a write of ours: never a text the hold may use
            return stamp
        self._real_bump = real_bump
        ccfiles.bump_credentials_file = bump
        real_remove = putback._hold_remove

        def remove(ctx, path, stamp):
            done = real_remove(ctx, path, stamp)
            world.removed += bool(done)
            return done
        self._real_remove = real_remove
        putback._hold_remove = remove

    def close(self):
        putback._link = self._real_link
        ccfiles.bump_credentials_file = self._real_bump
        putback._hold_remove = self._real_remove
        self.use.__exit__(None, None, None)
        self.rig.close()

    def hold(self, event):
        out = api.watch_hold(event)
        self.answers.append(out)
        if out.get('did') == 'kept':
            self.made_since_fill = 0
        return out

    def fail(self, what):
        self.broken.append(what)

    # ---------- what is there ----------
    def item(self):
        return self.rig.live_text()

    def named(self):
        return (self.rig.config().get('oauthAccount') or {}).get('accountUuid')

    def ours(self):
        note = putback._hold_note(self.ctx)
        st = stamp_of(self.cred)
        if st is None or note is None:
            return False
        if note['stamp'] is not None:
            return note['stamp'] == st
        t = stamp_of(note['tmp']) if note['tmp'] else None
        return t is not None and t[:2] == st[:2]

    def item_has_login(self):
        text = self.item()
        return bool(text) and live.parse(text).kind == 'oauth'

    def spare_text(self, text):
        """A spare copy of the sign-in a look puts in place: the item's own text, or the saved login of the account
        the settings name that a put-back is about to write (with the item's machine-wide parts)."""
        if text == self.item():
            return True
        aid = self.named()
        saved = self.rig.vault(aid) if aid else None
        try:
            return bool(saved) and json.loads(text).get('claudeAiOauth') == json.loads(saved).get('claudeAiOauth')
        except ValueError:
            return False

    def set_hold(self, on):
        with open(self.ctx.path('settings.json'), 'w') as f:
            json.dump({'auto_policy': {'hold_signin_file': on}}, f)

    def wrote(self, text):
        self.writes.append((text, self.clock.wall()))

    # ---------- Claude Code ----------
    def cc_write(self):
        """A refused save: its object into the file (a full login, or now and then one without a login)."""
        cur = self.item()
        if cur is None and os.path.exists(self.cred):
            with open(self.cred) as f:
                cur = f.read()
        try:
            blob = json.loads(cur) if cur else {}
        except ValueError:
            blob = {}
        if self.rng.random() < 0.15:
            blob.pop('claudeAiOauth', None)
        blob['discoveryState'] = {'p': self.rng.randint(1, 10 ** 6)}
        text = dumps(blob)
        with open(self.cred, 'w') as f:
            f.write(text)
        t = self.clock.wall()
        os.utime(self.cred, (t, t))
        self.wrote(text)

    def cc_remove(self):
        if os.path.exists(self.cred):
            os.unlink(self.cred)

    def cc_delete_item(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)

    def cc_save(self):
        """A save that reads the item, else the file, else nothing, and writes the item whole; one that found no item
        removes the file after it."""
        cur = self.item()
        absent = cur is None
        if absent and os.path.exists(self.cred):
            with open(self.cred) as f:
                cur = f.read()
        try:
            blob = json.loads(cur) if cur else {}
        except ValueError:
            blob = {}
        blob['discoveryState'] = {'s': self.rng.randint(1, 10 ** 6)}
        self.rig.set_live(dumps(blob))
        if absent and os.path.exists(self.cred):
            os.unlink(self.cred)

    def sign_out_start(self):
        """/logout: the refresh token revoked, the item deleted, the file removed (the settings follow later)."""
        who = self.named()
        if who:
            self.rig.server.revoke(who)
        self.cc_delete_item()
        self.cc_remove()
        self.signing_out = True

    def sign_out_end(self):
        self.rig.set_config(None)
        self.signing_out = None

    def sign_in_start(self):
        cur = self.item()
        blob = json.loads(cur) if cur else {}
        blob.pop('claudeAiOauth', None)
        self.rig.set_live(dumps(blob))
        self.cc_remove()
        self.rig.set_config(None)
        self.signing_in = blob

    def sign_in_end(self):
        uuid = self.rng.choice(sorted(ACCOUNTS))
        self.clock.advance(1.0)
        blob = dict(self.signing_in)
        blob.update(json.loads(self.rig.server.add(*ACCOUNTS[uuid])))
        self.rig.set_live(dumps(blob))
        self.rig.set_config(identity_block(*ACCOUNTS[uuid]))
        self.signing_in = None

    def switch(self):
        aid = self.named()
        to = [x for x in ACCOUNTS if x != aid]
        if not aid or not self.item_has_login():
            return
        try:
            switcher.switch(self.ctx, to[0], kind='manual', source='cli', reason='')
        except api.AccountsError:
            pass

    # ---------- the watch ----------
    def notice(self, files_only=False):
        self.leftover += bool(not self.named() and self.ours() and not files_only)
        try:
            self.watch.notice(files_only)
        except Killed:
            self.restart()
            return
        if files_only:
            return
        if not self.named() and self.ours() and self.rig.config_path() and os.path.exists(self.rig.config_path()):
            self.fail('(a) a file of ours outlived a sign-out by more than one event')

    def look(self):
        rec = putback.load_record(self.ctx)
        first = rec['missing'] is None or rec['missing'][0] != self.named()
        ours_only = not self.item() and self.ours() and self._file_has_login()
        had = bool(self.named()) and (self.item_has_login() or self._file_has_login())
        try:
            self.watch.notice()
        except Killed:
            self.restart()
            return
        hint, self.watch.pending = self.watch.pending or dict(sw.no_hint()), None
        # the watch's look, or the loop's; what the look itself changed is noticed at the next notice of the stream
        try:
            res = api.watch_look(hint) if self.rng.random() < 0.8 else api.put_back_pass()
        except Killed:                          # stopped part-way through a spare a put-back made
            self.restart()
            return
        if getattr(res, 'seen', None) is not None:
            self.answers.append(res.seen)
        if res.action == putback.PROMOTE and ours_only and first:
            self.fail('(g) a file of ours was promoted by the first look that found it')
        note = putback._hold_note(self.ctx)
        since = None if note is None else note['wrote'] if note['wrote'] is not None else note['at']
        if self.item_has_login() and self.ours() and not (
                since is not None and 0 <= self.clock.wall() - since < putback.HOLD_TIDY_S):
            self.fail('(f) a file of ours %g s old or older beside an item that holds a login, after a look'
                      % putback.HOLD_TIDY_S)
        if had and self.named() and not (self.item_has_login() or self._file_has_login()):
            self.fail('(i) a look left no login anywhere')

    def _file_has_login(self):
        try:
            with open(self.cred) as f:
                return live.parse(f.read()).kind == 'oauth'
        except (OSError, ValueError):
            return False

    def restart(self):
        """The watch stopped (or was stopped) and launchd started a new one: nothing in memory, its start, then a fresh
        view of the files."""
        putback.reset()
        putback._KEPT.clear()
        self.made_since_fill = 0
        self.watch = MiniWatch(self.rig, self.clock, hold=self.hold)
        self.hold('start')

    # ---------- the end ----------
    def quiet(self):
        """Claude Code ends what it started, then nothing more: the watch notices and looks for a while."""
        if self.signing_out:
            self.sign_out_end()
        if self.signing_in is not None:
            self.sign_in_end()
        for _ in range(12):
            self.clock.advance(1.0)
            self.notice()
            self.look()
        for _ in range(int(putback.HOLD_TIDY_S) // 5 + 1):    # past the time a file of ours may stay beside an item
            self.clock.advance(5.0)
            self.notice()
            self.look()
        left = [n for n in os.listdir(os.path.dirname(self.cred)) if n.endswith('.hold')]
        if left:
            self.fail('a temporary name of ours is left: %s' % left)
        note = putback._hold_note(self.ctx)
        if note is not None and not self.ours():
            self.fail('signin-hold.json names no file that is there')
        if self.item_has_login() and self.ours():
            self.fail('a file of ours beside an item that holds a login, after the quiet end')

    def secrets_said(self):
        texts = list(self.rig.logs) + [e[0] for e in self.rig.events] + \
            [json.dumps(e[2], default=str) for e in self.rig.events] + \
            [json.dumps(a, default=str) for a in self.answers]
        for name in (putback.HOLD_FILE, 'signin-record.jsonl'):
            try:
                with open(self.ctx.path(name)) as f:
                    texts.append(f.read())
            except OSError:
                pass
        return [t for t in texts if 'sk-ant-' in t or 'mcp-made-up' in t]


class ManyInterleavings(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        patcher = mock.patch.object(autoloop, 'notify_put_back', lambda res: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        putback.reset()
        putback._KEPT.clear()
        if self._old_home is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self._old_home

    def run_case(self, case, counts):
        rng = random.Random(SEED * 100003 + case)
        tmp = tempfile.mkdtemp(prefix='hold-many-')
        w = World(rng, tmp)
        try:
            for _ in range(rng.randint(20, 45)):
                r = rng.random()
                moved = True                    # Claude Code changed something the watch sees
                if r < 0.18:
                    w.cc_write()
                    counts['cc_write'] += 1
                elif r < 0.30:
                    w.cc_remove()
                elif r < 0.34:
                    w.cc_delete_item()
                elif r < 0.37:
                    for _ in range(rng.randint(3, 8)):      # Claude Code processes removing the file again and again
                        w.cc_remove()
                        w.notice(files_only=rng.random() < 0.5)
                        w.clock.advance(0.05)
                    counts['bursts'] += 1
                elif r < 0.44:
                    w.cc_save()
                elif r < 0.47 and not w.signing_out and w.signing_in is None and w.named():
                    w.sign_out_start()
                elif r < 0.50 and w.signing_out:
                    w.sign_out_end()
                elif r < 0.52 and w.signing_in is None and not w.signing_out:
                    w.sign_in_start()
                elif r < 0.54 and w.signing_in is not None:
                    w.sign_in_end()
                else:
                    moved = False
                    if r < 0.64:
                        w.notice()
                    elif r < 0.80:
                        w.look()
                    elif r < 0.82 and not w.signing_out and w.signing_in is None:
                        w.switch()
                    elif r < 0.85:
                        w.restart()
                        counts['restart'] += 1
                    elif r < 0.88:
                        w.kill = rng.choice(('before', 'after'))
                    elif r < 0.90:
                        w.race = True
                    elif r < 0.91 and not w.off:
                        on = rng.random() < 0.5
                        w.set_hold(on)
                        w.ever_on = w.ever_on or on
                if moved and rng.random() < 0.75:
                    # the watch tells the hold at once (else at a later notice); now and then the settings' change lands
                    # after its compare of the stamps, and reaches the hold at the next notice
                    w.notice(files_only=rng.random() < 0.3)
                w.clock.advance(rng.choice((0.002, 0.01, 0.05, 0.1, 0.2, 0.4, 0.8, 1.5, 3.5)))
                if w.broken:
                    break
            if not w.broken:
                w.quiet()
            said = w.secrets_said()
            if said:
                w.fail('(e) a token in %r' % said[0][:80])
            counts['made'] += sum(1 for a in w.answers if isinstance(a, dict) and a.get('did') == 'held')
            counts['removed'] += w.removed
            counts['bumped'] += w.bumped
            counts['killed'] += w.killed
            counts['leftover'] += w.leftover
            counts['off_cases'] += w.off
            return w.broken
        finally:
            w.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_rules_hold_over_many_made_up_interleavings(self):
        counts = dict.fromkeys(('cc_write', 'restart', 'made', 'removed', 'off_cases', 'killed', 'leftover', 'bursts',
                                'bumped'),
                               0)
        for case in range(CASES):
            self.assertEqual(self.run_case(case, counts), [], 'case %d' % case)
        print('\nover %d cases: %s' % (CASES, sorted(counts.items())))
        self.assertGreater(counts['made'], CASES // 6)            # the check reached the hold's put-backs,
        self.assertGreater(counts['removed'], CASES // 30)        # its take-aways,
        self.assertGreater(counts['off_cases'], CASES // 20)      # cases with the setting off,
        self.assertGreater(counts['killed'], CASES // 50)         # watches stopped part-way,
        self.assertGreater(counts['leftover'], 0)                 # and a sign-out in the hold's three seconds


if __name__ == '__main__':
    unittest.main()
