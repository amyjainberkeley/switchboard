import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Thousands of made-up orders, from a fixed seed, of what happens to Claude Code's Keychain item and its plain login
# file while Switchboard keeps a copy of the sign-in there for the processes that were told "no such item": Claude
# Code's refused saves in its own order (the whole object into the file, then the item deleted), removals of the file
# by a process whose save landed after it had read "no such item" (it creates the item first), saves that find the
# item, renewals by one process, another program putting another account's login in the item, chosen sign-outs in two
# steps, switches by Switchboard, Claude Code holding its save lock, and the sign-in watch's notices and looks (and the
# loop's). Stand-in Claude Code processes trust each answer of the item for 30 s, a "no such item" answer too, and
# while they hold that answer they read their login from the plain login file (read in Claude Code's program text,
# 2.1.329 to 2.1.332). After every step:
#   (1) after a look made while the save lock was free, a file of ours beside an item that holds a full login holds
#       that very login (its tokens and their end): never another one for longer than one look after the item changed;
#   (2) what Switchboard puts in place as a file of ours is a login of the account the settings name, and a file of
#       ours is only ever brought to a login of its own account;
#   (3) once the settings name nobody, the watch's next notice or look leaves no file of ours;
#   (4) the hold's rules never replace or remove a file Claude Code wrote (one a promote or a put-back handed to the
#       hold, its login now in the item, is the hold's to take away; one Claude Code writes after that is not);
#   (5) after a look made while the save lock was free, no file of ours is beside an item holding a login HOLD_TIDY_S
#       or more after the item was last absent or without a login (or Claude Code last wrote the file);
#   (6) after a chosen sign-out's settings step and the look after it, no login of that account is in the item or in a
#       file of ours (a file Claude Code wrote itself in that moment is its own);
#   (7) after a look made while the save lock was free, with nothing still owed an answer: a process told "no such item"
#       less than 30 s ago, under the same account, finds a login of that account in the plain login file whenever the
#       item holds that account's full login (a process Switchboard could know about: it asked no more than
#       HOLD_TIDY_S - 30 s after Switchboard last knew the item absent; a file Claude Code wrote without a login is its
#       own and is left out);
#   (h) with the setting off, nothing is put in place.
# (1) and (5) leave out a look the watch makes for a change of the settings alone: with no loss on record it is
# answered without a read of the item.
# And at the end, quiet: no file of ours beside an item that holds a login, no temporary name left, the note names the
# file there or is gone, and no token in a log line, a History line, the sign-in record, the note or an answer.
# Stand-ins only (fakes_accounts, an in-process Keychain and a made-up Anthropic); made-up *.example accounts.
import json
import os
import random
import shutil
import tempfile
import time
import unittest
from unittest import mock

from hold_fakes import A, B, MCP, MiniWatch, stamp_of
from fakes_autoswitch import FakeClock
from fakes_accounts import Rig, identity_block
from switchboard.accounts import api, autoloop, context, facts, live, putback, switcher

CASES = 1500
SEED = 5150
ACCOUNTS = {'uuid-a': A, 'uuid-b': B}
TRUST_S = 30.0          # a Claude Code process trusts each answer of its Keychain item this long (2.1.329 to 2.1.332)


def dumps(blob):
    return json.dumps(blob, separators=(',', ':'))


def owner_of(text):
    """The made-up account a login text belongs to, from its refresh token (the stand-in Anthropic names it there)."""
    login = live.parse(text) if text else None
    rt = login.refresh_token if login is not None and login.kind == 'oauth' else None
    for uuid in ACCOUNTS:
        if rt and '-%s-s' % uuid in rt:
            return uuid
    return None


def full(text):
    return bool(text) and live.parse(text).full_pair


class Reader(object):
    """A Claude Code process that asks its Keychain item now and then, and trusts each answer for TRUST_S."""

    def __init__(self):
        self.until = None           # when its current answer runs out
        self.absent = False         # that answer is "no such item"
        self.asked_at = None
        self.named = None           # the account the settings named when it asked


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
        self.lock = facts.storage_lock(self.rig.env, self.rig.home)
        self.broken = []
        self.answers = []
        self.readers = [Reader() for _ in range(rng.randint(2, 5))]
        self.known = []             # wall times Switchboard could know the item absent
        self.missing_at = self.clock.wall()     # when the item last held no login, or Claude Code last wrote the file
        self.cc_stamps = set()      # stamps of plain login files Claude Code wrote
        self.writer = None          # who wrote the plain login file there: 'cc' or 'sb'
        self.cc_wrote_at = None
        self.in_switch = False
        self.held = False           # Claude Code holds its save lock
        self.signed_out = None      # the account a sign-out ended for, until the look after it
        self.named_was, self.named_since = 'uuid-b', self.clock.wall()     # who the settings name, and since when
        self.off = rng.random() < 0.1
        self.counts = dict.fromkeys(('spare', 'late', 'followed', 'removed_another', 'checked_readers'), 0)
        if self.off:
            with open(self.ctx.path('settings.json'), 'w') as f:
                json.dump({'auto_policy': {'hold_signin_file': False}}, f)
        self.watch = MiniWatch(self.rig, self.clock, hold=self.hold)
        self._patch()

    # ---------- hooks on the stand-ins ----------
    def _patch(self):
        world = self
        self._real = (putback._link, putback._hold_swap, putback._hold_remove, self.rig.kc.write, putback.hold_adopt)
        real_link, real_swap, real_remove, real_write, real_adopt = self._real

        def adopt(ctx, path, stamp):
            # a write of ours put this file's very login in the item and handed the file to the hold: from now on it
            # is the hold's to take away, and a file Claude Code writes after this is Claude Code's again
            done = real_adopt(ctx, path, stamp)
            if done:
                world.cc_stamps.discard(tuple(stamp))
                world.writer = 'sb'
            return done

        def link(src, dst):
            with open(src) as f:
                text = f.read()
            real_link(src, dst)                 # raises when a file is there
            if world.off:
                world.fail('(h) a file put in place with the setting off')
            if owner_of(text) != world.named():
                world.fail('(2) a file of ours put in place with a login of %s while the settings name %s'
                           % (owner_of(text), world.named()))
            world.writer = 'sb'

        def swap(ctx, path, old, note, text):
            pre, before = stamp_of(path), world.file()
            new = real_swap(ctx, path, old, note, text)
            if new is not None:
                world.counts['followed'] += 1
                if pre in world.cc_stamps:
                    world.fail('(4) a file Claude Code wrote was replaced by the hold')
                if owner_of(text) != owner_of(before):
                    world.fail('(2) a file of ours brought to a login of another account')
                world.writer = 'sb'
            return new

        def remove(ctx, path, stamp):
            pre = stamp_of(path)
            done = real_remove(ctx, path, stamp)
            if done and pre in world.cc_stamps:
                world.fail('(4) a file Claude Code wrote was removed by the hold')
            return done

        def write(service, account, value, interactive=False, create_only=False):
            done = real_write(service, account, value, interactive=interactive, create_only=create_only)
            if service == world.rig.service and done.ok and not world.in_switch:
                world.known.append(world.clock.wall())          # a put-back or a promote: the item was missing
            return done
        putback._link, putback._hold_swap, putback._hold_remove, putback.hold_adopt = link, swap, remove, adopt
        self.rig.kc.write = write

    def close(self):
        putback._link, putback._hold_swap, putback._hold_remove, self.rig.kc.write, putback.hold_adopt = self._real
        if self.held:
            os.rmdir(self.lock)
        self.use.__exit__(None, None, None)
        self.rig.close()

    def hold(self, event):
        if event in ('appeared', 'changed') and self.writer == 'cc':
            self.known.append(self.cc_wrote_at)     # a fallback write the hold sees: the item goes right after it
        out = api.watch_hold(event)
        self.answers.append(out)
        if out.get('late'):
            self.counts['late'] += 1
        return out

    def fail(self, what):
        self.broken.append(what)

    # ---------- what is there ----------
    def item(self):
        return self.rig.live_text()

    def file(self):
        try:
            with open(self.cred) as f:
                return f.read()
        except FileNotFoundError:
            return None

    def named(self):
        uuid = (self.rig.config().get('oauthAccount') or {}).get('accountUuid')
        if uuid != self.named_was:                  # the extension restarts every chat's process at such a change
            self.named_was, self.named_since = uuid, self.clock.wall()
        return uuid

    def ours(self):
        note = putback._hold_note(self.ctx)
        st = stamp_of(self.cred)
        return st is not None and note is not None and st in (note['stamp'], note.get('prev'))

    def note_missing(self):
        if not full(self.item()):
            self.missing_at = self.clock.wall()

    # ---------- Claude Code ----------
    def cc_write(self, text):
        with open(self.cred, 'w') as f:
            f.write(text)
        t = self.clock.wall()
        os.utime(self.cred, (t, t))
        self.cc_stamps.add(stamp_of(self.cred))
        self.writer, self.cc_wrote_at = 'cc', t
        self.missing_at = t

    def fresh(self):
        """A save's fresh read: the item, else the plain login file, else nothing."""
        cur = self.item()
        if cur is None:
            cur = self.file()
        try:
            return json.loads(cur) if cur else {}
        except ValueError:
            return {}

    def refused(self):
        """A refused save: its whole object into the plain login file, then (it believed the item there) the item
        deleted a moment later; now and then another process removes the file right after (the dangerous order)."""
        obj = self.fresh()
        obj['discoveryState'] = {'f': self.rng.randint(1, 10 ** 6)}
        self.cc_write(dumps(obj))
        self.notice()
        self.clock.advance(self.rng.choice((0.05, 0.2, 0.35)))
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.notice()
        if self.rng.random() < 0.4:
            self.clock.advance(0.1)
            self.cc_remove()

    def cc_remove(self):
        if os.path.exists(self.cred):
            os.unlink(self.cred)
        self.notice()

    def save_landed_after_no_such_item(self):
        """A process that had read "no such item" saves: its fresh read, its part, the item written; then it removes
        the plain login file (Claude Code does, after a save that found the item absent)."""
        obj = self.fresh()
        obj['discoveryState'] = {'s': self.rng.randint(1, 10 ** 6)}
        self.rig.set_live(dumps(obj))
        if os.path.exists(self.cred):
            os.unlink(self.cred)
        self.notice()

    def save(self):
        """A save that finds the item: its fresh read, its part, the item written; the file is left alone."""
        obj = self.fresh()
        obj['discoveryState'] = {'p': self.rng.randint(1, 10 ** 6)}
        self.rig.set_live(dumps(obj))
        self.notice()

    def renew(self):
        """One process renews the login in the item (a refresh token Anthropic still accepts); its fresh read found
        the item, so it leaves the file alone."""
        cur, who = self.item(), owner_of(self.item())
        if who and who == self.named() and live.parse(cur).refresh_token in self.rig.server.valid_tokens(who):
            self.clock.advance(0.5)
            self.rig.set_live(self.rig.server.refresh_for(cur))
            self.notice()

    def another(self):
        """Another program puts the other account's login in the item, now and then with its settings too."""
        who = self.named() or 'uuid-b'
        other = [u for u in ACCOUNTS if u != who][0]
        key = [k for k in self.rig.doc()['accounts'] if k.startswith(other)][0]
        self.rig.set_live(self.rig.vault(key))
        if self.rng.random() < 0.5:
            self.rig.set_config(identity_block(*ACCOUNTS[other]))
        self.signed_out = None                  # a login put there from outside: not one left by a sign-out
        self.notice()

    def sign_out(self):
        """/logout in Claude Code's order: the refresh token revoked, the item deleted and the file removed, then a
        moment later the settings' account cleared, with now and then a notice or a look of the watch in between."""
        who = self.named()
        if who:
            self.rig.server.revoke(who)
        self.rig.sec.drop(self.rig.service, self.rig.account)
        if os.path.exists(self.cred):
            os.unlink(self.cred)
        r = self.rng.random()
        if r < 0.4:
            self.notice()
        elif r < 0.6:
            self.look()
        self.clock.advance(self.rng.choice((0.005, 0.1, 0.3)))
        self.rig.set_config(None)
        self.signed_out = who
        self.notice()

    def sign_in(self):
        """A sign-in inside Claude Code after a sign-out: a new login of one account in the item, then its settings."""
        uuid = self.rng.choice(sorted(ACCOUNTS))
        self.clock.advance(1.0)                 # two sign-ins never share an end stamp to the millisecond
        blob = self.fresh() if self.item() is not None else {}
        blob.pop('claudeAiOauth', None)
        blob.update(json.loads(self.rig.server.add(*ACCOUNTS[uuid])))
        self.rig.set_live(dumps(blob))
        self.rig.set_config(identity_block(*ACCOUNTS[uuid]))
        self.signed_out = None                  # a new login: the sign-out before it is over
        self.notice()

    def lock_now(self, held):
        if held and not self.held:
            os.mkdir(self.lock)
        elif not held and self.held:
            os.rmdir(self.lock)
        self.held = held

    def asks(self):
        """A process whose answer ran out asks the item again."""
        r = self.rng.choice(self.readers)
        now = self.clock.wall()
        if r.until is not None and now < r.until:
            return
        r.until, r.absent = now + TRUST_S, self.item() is None
        r.asked_at, r.named = now, self.named()

    # ---------- Switchboard ----------
    def switch(self):
        aid = self.named()
        if not aid or not full(self.item()) or self.held:
            return
        self.in_switch = True
        try:
            switcher.switch(self.ctx, [x for x in ACCOUNTS if x != aid][0], kind='manual', source='cli', reason='')
        except api.AccountsError:
            pass
        finally:
            self.in_switch = False
        if self.file() is not None and not self.ours():
            self.writer = 'sb'                      # a switch's rewrite of Claude Code's file, kept in step
        self.notice()

    def notice(self, files_only=False):
        self.note_missing()
        self.watch.notice(files_only)
        if not files_only and self.named() is None and self.ours() and os.path.exists(self.rig.config_path()):
            self.fail('(3) a file of ours outlived a sign-out by more than one notice')

    def look(self):
        free = not self.held
        if free and self.item() is None:
            self.known.append(self.clock.wall())     # the look reads "no such item"
        self.note_missing()
        self.watch.notice()
        pend = self.watch.pending or {}
        # a look for a change of the settings alone is answered without a read of the item while no loss is on record
        alone = bool(pend.get('settings')) and not any(pend.get(k) for k in ('keychain', 'login_file', 'tick',
                                                                           'login_file_written'))
        if self.rng.random() < 0.85:
            res = self.watch.look()
        else:
            res = api.put_back_pass()               # the loop's look; the watch keeps its hint for its own
            self.watch.notice()
            alone = False
        self.note_missing()
        if getattr(res, 'seen', None) is not None:
            self.answers.append(res.seen)
        out = self.hold('looked')
        if out.get('did') == 'spare':
            self.counts['spare'] += 1
        elif out.get('why') == 'another':
            self.counts['removed_another'] += 1
        self.after_look(free, alone)

    def after_look(self, free, alone):
        item, text, now = self.item(), self.file(), self.clock.wall()
        if self.named() is None and self.ours() and os.path.exists(self.rig.config_path()):
            self.fail('(3) a file of ours beside settings that name nobody, after a look')
        if free and not alone and self.ours() and full(item) and \
                not putback._same_login(live.parse(text), live.parse(item)):
            self.fail('(1) a file of ours holds another login than the item, after a look')
        # with the save lock held, a look that follows a sighting of the loss goes straight to that lock and ends there
        if free and not alone and self.ours() and full(item) and now - self.missing_at >= putback.HOLD_TIDY_S + 0.01:
            self.fail('(5) a file of ours %.1f s after the item last held no login, after a look'
                      % (now - self.missing_at))
        if free and self.signed_out is not None:
            # a plain login file Claude Code wrote itself is its own (a process that saved in the moment of the
            # sign-out can copy a login there); Switchboard answers for the item and for files of its own
            if owner_of(item) == self.signed_out or (self.ours() and owner_of(text) == self.signed_out):
                self.fail('(6) a login of the account that signed out is left after its sign-out')
            self.signed_out = None
        if free and not self.off and not putback._HOLD_LATE.get(self.ctx.data_dir):
            self.check_readers(item, text, now)

    def check_readers(self, item, text, now):
        x = self.named()
        if x is None or owner_of(item) != x or not full(item):
            return
        known = max(self.known) if self.known else None
        found = full(text) and owner_of(text) == x
        if text is not None and not full(text) and self.writer == 'cc':
            return                                  # Claude Code's own file without a login
        for r in self.readers:
            if not r.absent or not now < r.until or r.named != x or r.asked_at < self.named_since:
                continue
            if known is None or known < r.asked_at - (putback.HOLD_TIDY_S - TRUST_S):
                continue                            # an ask Switchboard could not know about
            self.counts['checked_readers'] += 1
            if not found:
                self.fail('(7) a process told "no such item" %.1f s ago finds no login in the plain login file while '
                          'the item holds the login' % (now - r.asked_at))
                return

    # ---------- the end ----------
    def quiet(self):
        """Claude Code stops, and the watch looks on: every second for a few, then every 5 s until no file of ours is
        left (one the last write kept goes HOLD_TIDY_S after it; (5) is checked at each look)."""
        self.lock_now(False)
        for _ in range(8):
            self.clock.advance(1.0)
            self.look()
        for _ in range(int(putback.HOLD_KEEP_S) // 5):
            if not self.ours() or self.broken:
                break
            self.clock.advance(5.0)
            self.look()
        left = [n for n in os.listdir(os.path.dirname(self.cred)) if n.endswith('.hold')]
        if left:
            self.fail('a temporary name of ours is left: %s' % left)
        if putback._hold_note(self.ctx) is not None and not self.ours():
            self.fail('signin-hold.json names no file that is there')
        if full(self.item()) and self.ours():
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


class ManyOrders(unittest.TestCase):
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
        tmp = tempfile.mkdtemp(prefix='spare-many-')
        w = World(rng, tmp)
        try:
            for _ in range(rng.randint(20, 45)):
                r = rng.random()
                if r < 0.12:
                    w.refused()
                elif r < 0.20:
                    w.save_landed_after_no_such_item()
                elif r < 0.26:
                    w.cc_remove()
                elif r < 0.31:
                    w.save()
                elif r < 0.38:
                    w.renew()
                elif r < 0.40:
                    w.another()
                elif r < 0.43 and w.named():
                    w.sign_out()
                elif r < 0.46 and not w.named():
                    w.sign_in()
                elif r < 0.48:
                    w.switch()
                elif r < 0.52:
                    w.lock_now(not w.held)
                elif r < 0.64:
                    w.asks()
                elif r < 0.70:
                    w.notice(files_only=rng.random() < 0.3)
                else:
                    w.look()
                w.clock.advance(rng.choice((0.01, 0.05, 0.2, 0.5, 1.0, 2.0, 4.0, 8.0)))
                if w.broken:
                    break
            if not w.broken:
                w.quiet()
            said = w.secrets_said()
            if said:
                w.fail('(e) a token in %r' % said[0][:80])
            for k, n in w.counts.items():
                counts[k] += n
            counts['off_cases'] += w.off
            return w.broken
        finally:
            w.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_rules_hold_over_many_made_up_orders(self):
        counts = dict.fromkeys(('spare', 'late', 'followed', 'removed_another', 'checked_readers', 'off_cases'), 0)
        for case in range(CASES):
            self.assertEqual(self.run_case(case, counts), [], 'case %d' % case)
        print('\nover %d cases: %s' % (CASES, sorted(counts.items())))
        self.assertGreater(counts['spare'], CASES // 10)          # the check reached spares made by looks,
        self.assertGreater(counts['late'], CASES // 20)           # late removals,
        self.assertGreater(counts['followed'], CASES // 50)       # files brought up to a renewal,
        self.assertGreater(counts['removed_another'], 0)          # files beside another account's login,
        self.assertGreater(counts['checked_readers'], CASES)      # processes told "no such item",
        self.assertGreater(counts['off_cases'], CASES // 20)      # and cases with the setting off


if __name__ == '__main__':
    unittest.main()
