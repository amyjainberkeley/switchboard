import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Thousands of made-up interleavings of Claude Code processes saving its Keychain item, with Switchboard's looks in
# between, from a fixed seed. Each stand-in "saver" behaves as Claude Code 2.1.329 does (read in its program text): it
# reads the item (its belief whether the item is there), takes the folder lock <config folder>/.storage-write.lock (some
# stand-ins skip it, as some process did on the night the sign-in was lost), reads fresh (the item, else the plaintext
# file, else nothing), changes its own part and writes the whole object. When macOS refuses the write it writes the
# object to the plaintext file and then deletes the item if it believed the item was there; a write that lands after
# it believed the item absent deletes the plaintext file. Its read is two steps: after the Keychain says "no such item"
# the file may be opened a step later, when another saver has acted. Besides: a chosen sign-out, a sign-in inside
# Claude Code, a renewal, a switch, refused writes of Switchboard's own, and looks with and without the sign-in watch's
# hint.
# After every step: (a) Switchboard never wrote over an account login (outside a switch, and outside taking back its
# own write within putback.TAKE_BACK_S while the settings name nobody) and never while the folder lock was held by
# another; (b) after a chosen sign-out nothing is written until someone signs in, but such a take-back; (c) what it
# writes is the named account's login, never older than the saved copy at that moment; (d) the limits of an episode
# hold; (e) a plaintext login file Switchboard makes is only ever a copy of the sign-in in the item, known as the
# sign-in hold's (a put-back leaves one for the processes still reading the file); once the savers are quiet, the item
# holds the named account's login, one Anthropic still accepts, and Switchboard left no plaintext file (one it keeps
# for a while after putting its login back in the item is gone by then: the looks go on until it is), or the case
# ends in a way `ending` names with a cause it checks (never one where Switchboard failed to keep a valid login); (f)
# no token in a log line, a History line, the record or a returned `seen`.
# Stand-ins only (fakes_accounts, an in-process Keychain and a made-up Anthropic); made-up *.example accounts.
import json
import os
import random
import shutil
import tempfile
import unittest
from unittest import mock

from fakes_autoswitch import FakeClock
from fakes_accounts import Rig, identity_block
from switchboard.accounts import api, autoloop, context, identity, live, putback, switcher

MCP = {'mcpOAuth': {'docs-server': {'accessToken': 'mcp-made-up-1', 'expiresAt': 1}}}
CASES = 2000
SEED = 36036
ACCOUNTS = {'uuid-a': ('ana@work.example', 'org-a'), 'uuid-b': ('ana@home.example', 'org-b')}


def dumps(blob):
    return json.dumps(blob, separators=(',', ':'))


def owner_of(text):
    """The made-up account a login text belongs to, from its refresh token (the stand-in Anthropic names it there)."""
    login = live.parse(text) if text else None
    rt = login.refresh_token if login is not None and login.kind == 'oauth' else None
    if not rt:
        return None
    for uuid in ACCOUNTS:
        if '-%s-s' % uuid in rt:
            return uuid
    return None


class Saver(object):
    """One Claude Code process saving the whole item, one step at a time."""

    def __init__(self, world, n, honors):
        self.w, self.n, self.honors = world, n, honors
        self.step = None            # None: idle; else the next step's name
        self.holding = False
        self.believed = None        # its read before the save: the item was there
        self.obj = None
        self.renew = False
        self.saves_left = 3

    def start(self, renew=False):
        if self.step is None and self.saves_left > 0:
            self.saves_left -= 1
            self.renew = renew
            self.step = 'begin'

    def act(self):
        """One step of the save. True when it did something."""
        w = self.w
        if self.step == 'begin':
            self.believed = w.item() is not None
            self.step = 'lock' if self.honors else 'read'
        elif self.step == 'lock':
            try:
                os.mkdir(w.lock)
            except FileExistsError:
                return False                                    # held by another: try again later
            self.holding = True
            self.step = 'read'
        elif self.step == 'read':
            text = w.item()
            if text is None and w.rng.random() < w.two_step:
                self.step = 'read_file'         # "no such item"; the file is opened in a step of its own
                return True
            if text is None and os.path.exists(w.cred):
                with open(w.cred) as f:
                    text = f.read()
            self.take(text)
        elif self.step == 'read_file':
            text = None                         # another saver may have brought the item back and removed the file
            if os.path.exists(w.cred):
                with open(w.cred) as f:
                    text = f.read()
            self.take(text)
        elif self.step == 'write':
            obj = dict(self.obj)
            obj['discoveryState'] = {'p%d' % self.n: w.rng.randint(1, 10 ** 6)}
            who = owner_of(dumps(obj))
            if self.renew and who and live.parse(dumps(obj)).refresh_token in w.rig.server.valid_tokens(who):
                w.clock.advance(1.0)                            # a renewal of a token Anthropic still accepts
                obj['claudeAiOauth'] = json.loads(w.rig.server.refresh_for(dumps(obj)))['claudeAiOauth']
            self.obj = obj
            if w.rng.random() < w.refuse:
                self.step = 'file'                              # macOS refused it (exit 45): the fallback
                return True
            w.rig.sec.put(w.rig.service, w.rig.account, dumps(obj))
            if not self.believed and os.path.exists(w.cred):
                os.unlink(w.cred)
            self.step = 'unlock'
        elif self.step == 'file':
            with open(w.cred, 'w') as f:
                f.write(dumps(self.obj))
            self.step = 'delete' if self.believed else 'unlock'
        elif self.step == 'delete':
            w.rig.sec.drop(w.rig.service, w.rig.account)
            self.step = 'unlock'
        elif self.step == 'unlock':
            if self.holding:
                os.rmdir(w.lock)
                self.holding = False
            self.step = None
        else:
            return False
        return True

    def take(self, text):
        try:
            self.obj = json.loads(text) if text else {}
        except ValueError:
            self.obj = {}
        self.step = 'write'


class World(object):
    def __init__(self, case_rng, tmp):
        self.rng = case_rng
        os.environ['SWITCHBOARD_HOME'] = os.path.join(tmp, 'data')
        os.makedirs(os.environ['SWITCHBOARD_HOME'], exist_ok=True)
        self.clock = FakeClock(wall=1794837600.0 + case_rng.uniform(0, 10 ** 6), mono=50000.0)
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono, sleep=self.clock.sleep)
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        putback.reset()
        putback._KEPT.clear()
        self.rig.keep('uuid-a', *ACCOUNTS['uuid-a'])
        self.clock.advance(1.0)
        self.rig.keep('uuid-b', *ACCOUNTS['uuid-b'], siblings=MCP)
        self.lock = os.path.join(self.rig.home, '.claude', '.storage-write.lock')
        self.cred = os.path.join(self.rig.home, '.claude', '.credentials.json')
        self.refuse = case_rng.choice((0.0, 0.2, 0.5, 0.8))
        honor = case_rng.choice((1.0, 0.8, 0.5, 0.0))
        self.savers = [Saver(self, i, case_rng.random() < honor) for i in range(case_rng.randint(2, 6))]
        # Claude Code's read is two steps (2.1.329): the Keychain says "no such item", then the file is opened; how
        # often another saver gets a step in between
        self.two_step = case_rng.choice((0.0, 0.5, 1.0))
        self.signed_out = False
        self.signing_in = None
        self.in_switch = False
        self.writes = []            # (wall, kind) of each write Switchboard landed in the live item outside a switch
        self.ours = []              # (text, wall) of the same writes
        self.broken = []
        self.seen = []
        self.actions = {}
        self.snap = self.snapshot()
        real_write = self.rig.kc.write

        def write(service, account, value, interactive=False, create_only=False):
            if service == self.rig.service and not self.in_switch:
                self.check_write(value, create_only)
            done = real_write(service, account, value, interactive=interactive, create_only=create_only)
            if service == self.rig.service and not self.in_switch and done.ok:
                self.writes.append(self.clock.wall())
                self.ours.append((value, self.clock.wall()))
            return done
        self.rig.kc.write = write
        real_delete = self.rig.kc.delete

        def delete(service, account):
            if service == self.rig.service and not self.in_switch and not self.taking_back():
                self.fail('(a) removed a login that is not a write of its own that met a sign-out')
            return real_delete(service, account)
        self.rig.kc.delete = delete

    def close(self):
        self.use.__exit__(None, None, None)
        self.rig.close()

    def item(self):
        return self.rig.live_text()

    def named(self):
        block = self.rig.config().get('oauthAccount')
        return identity.label_id(self.rig.doc(), block) if block else None

    def snapshot(self):
        cred = None
        if os.path.exists(self.cred):
            with open(self.cred) as f:
                cred = f.read()
        return self.item(), cred, json.dumps(self.rig.config().get('oauthAccount'))

    def hint(self):
        now = self.snapshot()
        before, self.snap = self.snap, now
        lf = None
        if before[1] is None and now[1] is not None:
            lf = 'appeared'
        elif before[1] is not None and now[1] is None:
            lf = 'gone'
        elif before[1] != now[1]:
            lf = 'changed'
        return {'keychain': before[0] != now[0], 'login_file': lf, 'settings': before[2] != now[2], 'tick': False}

    def fail(self, what):
        self.broken.append(what)

    def taking_back(self, value=None):
        """Switchboard takes back its own write (putback._undo): the settings are there and name nobody, the item
        holds exactly a text it wrote less than putback.TAKE_BACK_S ago, and what it writes is that text without the
        account login (or it deletes the item)."""
        cur, now = self.item(), self.clock.wall()
        if cur is None or 'oauthAccount' in self.rig.config() or not os.path.exists(self.rig.config_path()):
            return False
        if not any(cur == text and 0 <= now - t < putback.TAKE_BACK_S for text, t in self.ours):
            return False
        if value is None:
            return True
        theirs = json.loads(cur)
        theirs.pop('claudeAiOauth', None)
        return json.loads(value) == theirs

    def check_write(self, value, create_only):
        cur = self.item()
        if self.taking_back(value):
            return
        if cur is not None and live.parse(cur).kind == 'oauth':
            self.fail('(a) wrote over an account login')
        if any(s.holding for s in self.savers):
            self.fail('(a) wrote while a Claude Code process held the folder lock')
        if self.signed_out:
            self.fail('(b) wrote after a chosen sign-out')
        aid = self.named()
        who = owner_of(value)
        if aid is None or who != aid:
            self.fail('(c) wrote a login of %s while the settings name %s' % (who, aid))
            return
        saved = live.parse(self.rig.vault(aid))
        if live.compare(live.parse(value), saved) == 'older':
            self.fail('(c) wrote an older generation than the saved copy')

    # what happens

    def look(self):
        kind = self.rng.random()
        if self.rng.random() < 0.1:
            self.rig.kc.rule('write', self.rig.service, why='collided')        # another writer at that moment
        cred_before = os.path.exists(self.cred)
        if kind < 0.6:
            res = api.watch_look(self.hint())
        elif kind < 0.8:
            self.hint()
            res = api.watch_look(None)
        else:
            self.hint()
            res = api.put_back_pass()
        self.rig.kc.rules = [r for r in self.rig.kc.rules if r['times'] != 0 and r['verb'] != 'write']
        if getattr(res, 'seen', None) is not None:
            self.seen.append(res.seen)
        self.actions[res.action] = self.actions.get(res.action, 0) + 1
        if not cred_before and os.path.exists(self.cred) and not self.spare_there():
            self.fail('(e) Switchboard made a plaintext login file other than a copy of the sign-in in the item')
        return res

    def sign_out(self):
        """/logout: the item and the plaintext file removed, then the settings' account cleared."""
        self.rig.sec.drop(self.rig.service, self.rig.account)
        if os.path.exists(self.cred):
            os.unlink(self.cred)
        if self.rng.random() < 0.5:
            self.look()                                     # a look in the moment between the two steps
        self.rig.set_config(None)
        self.signed_out = True

    def sign_in(self):
        """A sign-in inside Claude Code: the login taken out of the item and the settings' account cleared, then the
        new login stored with its account."""
        uuid = self.rng.choice(sorted(ACCOUNTS))
        cur = self.item()
        blob = json.loads(cur) if cur else {}
        blob.pop('claudeAiOauth', None)
        self.rig.set_live(dumps(blob))
        self.rig.set_config(None)
        if self.rng.random() < 0.5:
            self.look()
        email, org = ACCOUNTS[uuid]
        self.clock.advance(1.0)                 # a sign-in takes a while: two never share an end stamp to the ms
        login = json.loads(self.rig.server.add(uuid, email, org))
        blob.update(login)
        self.rig.set_live(dumps(blob))
        self.rig.set_config(identity_block(uuid, email, org))
        self.signed_out = False

    def lost(self):
        """The end of the unlucky order with no look between: the item deleted, the file deleted, then a save that read
        neither and wrote the item with only its connector state."""
        self.rig.sec.drop(self.rig.service, self.rig.account)
        if os.path.exists(self.cred):
            os.unlink(self.cred)
        self.rig.set_live(dumps(dict(MCP, discoveryState={'lost': self.rng.randint(1, 10 ** 6)})))

    def switch(self):
        aid = self.named()
        to = [x for x in ACCOUNTS if x != aid]
        if aid is None or not to:
            return
        self.in_switch = True
        try:
            switcher.switch(self.ctx, to[0], kind='manual', source='cli', reason='')
        except api.AccountsError:
            pass
        finally:
            self.in_switch = False

    def check(self):
        rec = putback.load_record(self.ctx)
        ep = rec.get('episode')
        if ep and ep['writes'] > putback.EPISODE_WRITES:
            self.fail('(d) %d writes in one episode' % ep['writes'])
        now = self.clock.wall()
        if sum(1 for t in rec.get('episodes') or [] if now - t < putback.PUT_BACK_SPAN_S) > putback.EPISODES_PER_HOUR:
            self.fail('(d) more episodes in an hour than allowed')
        if len([t for t in self.writes if now - t < putback.PUT_BACK_SPAN_S]) > \
                putback.EPISODE_WRITES * putback.EPISODES_PER_HOUR + putback.PUT_BACK_MAX:
            self.fail('(d) more writes in an hour than the limits allow')

    def secrets_said(self):
        texts = list(self.rig.logs) + [e[0] for e in self.rig.events] + \
            [json.dumps(e[2], default=str) for e in self.rig.events] + [json.dumps(s) for s in self.seen]
        try:
            with open(self.ctx.path(putback.STATE)) as f:
                texts.append(f.read())
        except OSError:
            pass
        return [t for t in texts if 'sk-ant-' in t or 'mcp-made-up' in t]

    def finish(self):
        """The savers finish what they started, then stay quiet; Switchboard looks as the watch would."""
        for s in self.savers:
            guard = 0
            while s.step is not None and guard < 20:
                s.act()
                guard += 1
                self.clock.advance(0.05)
        for i in range(90):
            gap = 0.5 if i < 8 else 5.0
            self.clock.advance(gap)
            self.look()
            self.check()
            if i > 10 and self.settled() and not self.file_kept_for_now():
                break

    def settled(self):
        aid = self.named()
        return aid is not None and owner_of(self.item()) == aid

    def spare_there(self):
        """The plain login file is a copy of the sign-in Switchboard left after a put-back: one it keeps for a while
        (file_kept_for_now) holding exactly the text in the item."""
        if not self.file_kept_for_now():
            return False
        with open(self.cred) as f:
            return f.read() == self.item()

    def file_kept_for_now(self):
        """The plain login file is one Switchboard keeps for a while after it put that file's login back in the item
        (the Claude Code processes that asked while the item was gone still read it): a later look takes it away, so
        the case is not quiet yet."""
        note = putback._hold_note(self.ctx)
        try:
            st = os.stat(self.cred)
        except OSError:
            return False
        return note is not None and note['stamp'] == (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)


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

    def run_case(self, case):
        rng = random.Random(SEED * 100003 + case)
        tmp = tempfile.mkdtemp(prefix='savers-')
        w = World(rng, tmp)
        try:
            for _ in range(rng.randint(15, 45)):
                r = rng.random()
                idle = [s for s in w.savers if s.step is None]
                busy = [s for s in w.savers if s.step is not None]
                if r < 0.30 and idle:
                    rng.choice(idle).start(renew=rng.random() < 0.15)
                elif r < 0.60 and busy:
                    rng.choice(busy).act()
                elif r < 0.85:
                    w.look()
                elif r < 0.88 and not w.signed_out and not busy:
                    w.sign_out()
                elif r < 0.92 and not busy:
                    w.sign_in()
                elif r < 0.94 and not w.signed_out and not busy:
                    w.switch()
                elif r < 0.97 and not busy:
                    w.lost()
                w.clock.advance(rng.choice((0.01, 0.05, 0.2, 0.6, 2.0, 6.0)))
                w.check()
                if w.broken:
                    break
            if not w.broken:
                w.finish()
            outcome = self.ending(w)
            said = w.secrets_said()
            if said:
                w.fail('(f) a token in %r' % said[0][:80])
            return w.broken, outcome, w.actions
        finally:
            w.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def ending(self, w):
        """How the case ended once quiet: 'signed_in', 'excused' (with why) or a broken (e)."""
        aid = w.named()
        rec = putback.load_record(w.ctx)
        if aid is None:
            return 'signed_out'
        saved = live.parse(w.rig.vault(aid))
        item = live.parse(w.item()) if w.item() else None
        item_rt = item.refresh_token if item is not None and item.kind == 'oauth' else None
        latest = w.rig.server.valid_refresh(aid)
        if latest != saved.refresh_token:
            # Claude Code renewed and its renewal was not kept: excused only when the item does not hold it (Claude
            # Code lost it itself); a valid login in the item that Switchboard did not keep is Switchboard's fault
            if item_rt is not None and item_rt == latest and owner_of(w.item()) == aid:
                w.fail('(e) quiet, the item holds the valid login of %s and its saved copy is older: not kept' % aid)
                return 'broken'
            return 'saved_not_good'
        if rec.get('limit_at') is not None:
            return 'limit'
        signed_in = owner_of(w.item()) == aid
        if signed_in and item_rt not in w.rig.server.valid_tokens(aid):
            return self.dead_login_in_the_item(w, aid, item, saved)
        if w.settled() and not os.path.exists(w.cred):
            return 'signed_in'
        if signed_in and os.path.exists(w.cred):
            return 'signed_in_file_left'                    # Claude Code's own file, beside a good item
        # the settings name the account a switch just left (putback.SWITCHED_AWAY_S): neither a put-back nor a promote
        # is made by that name, so it is left as it is; never once that account was signed in again after the switch
        now = w.ctx.wall()
        if putback.decide(putback.see(w.ctx, now)[0], rec['missing'], rec['done'], now, rec['failed'],
                          **putback._evidence(rec, now)).key == 'switched':
            again = self.signed_in_after_the_switch(w, aid, saved)
            if not again:
                return 'switched_away'
            w.fail('(e) quiet, the settings name %s, %s after the switch away from it, yet nothing was put back'
                   % (aid, again))
            return 'broken'
        filed = None
        if os.path.exists(w.cred):
            with open(w.cred) as f:
                text = f.read()
            filed = owner_of(text) or live.parse(text).kind
        w.fail('(e) quiet, the settings name %s, its saved login is good, yet the item holds %s (file: %s); last '
               'lines: %s' % (aid, owner_of(w.item()) or (item.kind if item else 'nothing'), filed, w.rig.logs[-4:]))
        return 'broken'

    @staticmethod
    def dead_login_in_the_item(w, aid, item, saved):
        """The item holds a login of the named account that Anthropic no longer accepts while its saved copy is the
        valid one. Switchboard never writes over an account login, so this is excused only when Claude Code wrote it:
        a saver that skips the folder lock wrote back an object it read before another saver's renewal (an older
        generation than the saved copy)."""
        if any(text == item.text for text, _ in w.ours):
            w.fail('(e) quiet, the item holds a login of %s that Anthropic no longer accepts, written by Switchboard'
                   % aid)
            return 'broken'
        if live.compare(item, saved) == 'older' and not all(s.honors for s in w.savers):
            return 'older_login_by_claude_code'
        w.fail('(e) quiet, the item holds a login of %s that Anthropic no longer accepts (%s than the saved copy)'
               % (aid, live.compare(item, saved)))
        return 'broken'

    @staticmethod
    def signed_in_after_the_switch(w, aid, saved):
        """How the account was seen signed in again after the last switch away from it, or None: its saved login was
        kept after that switch, or Claude Code's plain login file holds its login, the saved copy or newer."""
        doc = w.rig.doc()
        last = (doc.get('switches') or [None])[-1] or {}
        if (doc['accounts'][aid].get('saved_at') or 0) > (last.get('at') or 0):
            return 'its saved login was kept'
        if os.path.exists(w.cred):
            with open(w.cred) as f:
                filed = live.parse(f.read())
            if owner_of(filed.text) == aid and filed.full_pair and live.compare(filed, saved) in ('same', 'newer'):
                return 'its login is in the plain login file'
        return None

    def test_the_rules_hold_over_many_made_up_interleavings(self):
        endings, actions = {}, {}
        for case in range(CASES):
            broken, outcome, acts = self.run_case(case)
            self.assertEqual(broken, [], 'case %d' % case)
            endings[outcome] = endings.get(outcome, 0) + 1
            for k, n in acts.items():
                actions[k] = actions.get(k, 0) + n
        print('\nendings over %d cases: %s; looks: %s' % (CASES, sorted(endings.items()), sorted(actions.items())))
        self.assertGreater(endings.get('signed_in', 0), CASES // 2)
        for action in ('promote', 'put_back', 'kept', 'wait'):             # the check reached each of them
            self.assertGreater(actions.get(action, 0), CASES // 25, action)


if __name__ == '__main__':
    unittest.main()
