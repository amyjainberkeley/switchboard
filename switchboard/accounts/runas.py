"""Act as another account without touching the live login: a private config folder and its own Keychain item.

run_as(target, argv):
- the target is the live login's own account (proved by the credential, not the label): `claude` runs plainly, on
  the default login (any CLAUDE_CONFIG_DIR removed), so one rotating refresh token never lives in two places;
- the live login can't be read: refused (seeding the target could make a second holder of the live lineage);
- otherwise the account is claimed (auto never switches to it, upkeep never refreshes it), a private folder is made
  under <data>/run/, the target's login (its account-bound parts only: the Mac's MCP logins stay single) goes into
  the Keychain item Claude Code uses for that folder (facts.keychain_service with CLAUDE_CONFIG_DIR = the folder), an
  identity block goes into the folder's .claude.json, and `claude` runs there with the variables that would bypass the
  account login removed. Unless bare, the folder also links to the person's own Claude Code folder for what every
  account shares (chats and memory, instructions, settings and hooks, skills: runfolder.py has the list), and its
  .claude.json gets copies of the person's MCP servers and of the entry for the folder the run starts in.
- afterwards a newer generation Claude Code made in the folder is saved back (only over the generation that was
  seeded; anything else goes to the recovery stash), then the item and the folder are removed (each link as a link,
  never followed; a folder whose link was replaced by a real file or folder is kept and said) and the claim let go.
A claim whose processes died (a crash, a closed terminal) is settled the same way by reap_stale_claims(), before
anything else may spend that account's token.
"""
import os
import shutil
import signal
import subprocess
import tempfile
import threading

from . import ccfiles, facts, identity, live as live_mod, runfolder, tokens, vault
from .api import ABSENT, PRESENT, UNREADABLE, AccountsError, Read


def _base_env(env):
    return dict(os.environ if env is None else env)


def _own_env(base, folder=None):
    """The environment of a run: the caller's, without what would put Claude Code on a key, a token, a cloud provider
    or a profile instead of the account's login (facts.NOT_THE_LOGIN), and on the run's own Claude Code folder
    (none given: the default one, whatever folder the caller's shell names)."""
    env = dict((k, v) for k, v in base.items() if k not in facts.NOT_THE_LOGIN)
    env.pop('CLAUDE_SECURESTORAGE_CONFIG_DIR', None)
    env.pop('CLAUDE_CONFIG_DIR', None)
    if folder is not None:
        env['CLAUDE_CONFIG_DIR'] = folder
    return env


def _find_claude(ctx, base):
    if ctx.claude:
        return ctx.claude
    return shutil.which('claude', path=base.get('PATH')) or None


def run_as(ctx, target, argv, cwd=None, env=None, timeout=None, bare=False, before=None, say=None, apart=False,
           strict=False):
    """Run `claude <argv>` as account id `target`. Returns its exit code (124 when it ran past timeout).

    bare: the private folder holds the identity only (nothing of the person's Claude Code folder). apart: it shares
    all but the chat history (runfolder.HISTORY), so the chat goes with the folder. strict: never on the person's own
    folder; when target is the login Claude Code is on, AccountsError('in_use') before anything starts. before(plain)
    is called just before Claude Code starts: plain is True when it runs on the login Claude Code is on (no private
    folder). say(text) gets a sentence for the person when the run's folder had to be kept."""
    from .capture import in_runas_env
    if in_runas_env(ctx):
        raise AccountsError('other', 'This shell is already inside a `switchboard run` session; run it from a '
                                     'normal terminal.')
    base = _base_env(env)
    claude = _find_claude(ctx, base)
    if not claude:
        raise AccountsError('other', "Claude Code's `claude` command wasn't found; check that it is on your PATH.")
    try:
        doc = vault.load(ctx)
    except vault.VaultError as e:
        raise AccountsError('other', str(e))
    if target not in doc['accounts']:
        raise AccountsError('not_found', 'No saved account matches that.')
    email = doc['accounts'][target].get('email') or target
    if doc['accounts'][target].get('conflict'):
        from .switcher import conflict_error
        raise conflict_error(email)            # its saved login is another account's: never run as it, plainly or not
    lr = live_mod.read_live(ctx, fresh=True)
    if lr.read.kind == UNREADABLE or lr.read.degraded:
        from .switcher import unreadable_error
        raise unreadable_error(lr.read)
    if lr.login is not None and lr.login.full_pair and identity.offline(lr.login, doc).account_id == target:
        if strict:
            raise AccountsError('in_use', in_use_words(email, apart))
        if before is not None:
            before(True)
        return _run(ctx, [claude] + list(argv), cwd, _own_env(base), timeout, None)
    reap_stale_claims(ctx, say=say)
    # refused here, before anything is made, when it can't be shared
    shared = [] if bare else runfolder.plan(ctx, history=not apart)
    folder, claim = _claim_and_seed(ctx, target, email, shared, cwd or os.getcwd(), bare)
    try:
        if before is not None:
            before(False)
        return _run(ctx, [claude] + list(argv), cwd, _own_env(base, folder), timeout,
                    lambda pid: _note_child(ctx, target, pid))
    finally:
        finish(ctx, target, say=say)


def in_use_words(email, apart):
    """Why a strict run as the account Claude Code is on starts nothing. That account runs only on the person's own
    folder: a second copy of its login in a private folder could end the first one's sign-in when either renews it."""
    if apart:
        return ('%s is the account Claude Code is on, so a run as it uses your own Claude Code folder and its chat '
                'would be kept. Nothing was started.' % email)
    return ('%s is the account Claude Code is on, so a run as it would start on your own Claude Code folder. Nothing '
            'was started.' % email)


def _claim_and_seed(ctx, target, email, shared=(), cwd=None, bare=True):
    with vault.switch_lock(ctx, timeout=30.0):
        try:
            consume = vault.consume_lock(ctx, target, timeout=10.0)   # nothing spends it between the read and the claim
        except vault.LockBusy:
            raise AccountsError('claude_busy', "%s's saved login is being renewed right now; try again in a few "
                                               "seconds." % email)
        try:
            folder, claim, tlogin, identity_block = _claim(ctx, target, email, consume,
                                                           runfolder.links_of(shared))
        finally:
            consume.release()
    try:
        done = ctx.keychain.write(claim['service'], claim['account'], live_mod.dump(live_mod.account_bound(
            tlogin.blob)), interactive=True)
        if not done.ok:
            from .switcher import unreadable_error
            raise unreadable_error(Read(UNREADABLE, why=done.why), 'the login for the run', verb='write')
        seed = runfolder.seed(ctx, identity_block, cwd, bare)
        ccfiles.atomic_write(os.path.join(folder, '.claude.json'),
                             ccfiles.dumps_for_claude(seed).encode('utf-8'), mode=0o600, follow=False)
        try:
            runfolder.build(folder, shared)
        except OSError as e:
            raise AccountsError('other', "Couldn't set up the run's folder (%s). %s" % (e.__class__.__name__,
                                                                                       runfolder.BARE))
    except BaseException:
        finish(ctx, target)
        raise
    return folder, claim


def _claim(ctx, target, email, consume, links=()):
    """Under the switch and consume locks: the target's saved login (renewed first when expired), and the claim."""
    try:
        if (vault.load(ctx)['accounts'].get(target) or {}).get('conflict'):
            from .switcher import conflict_error
            raise conflict_error(email)        # (read again under the locks: a renewal may have just said so)
    except vault.VaultError as e:
        raise AccountsError('other', str(e))
    tr = vault.read_current(ctx, target, fresh=True, interactive=True)
    if tr.kind != PRESENT:
        from .switcher import unreadable_error
        if tr.kind == ABSENT:
            raise AccountsError('dead_login', "%s's saved login is missing. Sign in to it in Claude Code, and "
                                              "Switchboard keeps it again." % email)
        raise unreadable_error(tr, "%s's saved login" % email)
    tlogin = live_mod.parse(tr.value)
    if not tlogin.full_pair:
        raise AccountsError('dead_login', "%s's saved login is incomplete; sign in to it again." % email)
    if tlogin.expired(ctx.wall()):
        res = tokens.refresh(ctx, target, budget_s=45.0, consume=consume, reason='run')
        if res.why.startswith((tokens.WAITING, tokens.SAVE_FAILED)):
            from .switcher import waiting_error
            raise waiting_error(res, email)    # the renewed login is here and not saved: the words name why
        if res.outcome == 'permanent':
            raise AccountsError('dead_login', '%s needs sign-in: Anthropic refused its saved login.' % email)
        if res.why in (tokens.CONFLICT, 'conflict'):
            from .switcher import conflict_error
            raise conflict_error(email)        # the renewal's answer named another account: its new login is set aside
        if res.spent_maybe or (res.outcome == 'skipped' and res.why == 'in_flight'):
            job = tokens._INFLIGHT.get(target)
            if job is not None:
                job.done.wait(35.0)                         # a late answer is saved by its worker
            raise AccountsError('network', "Couldn't finish renewing %s's saved login; try again in a minute."
                                % email)
        if res.outcome in ('refreshed', 'adopted'):
            tr = vault.read_current(ctx, target, fresh=True, interactive=True)
            if tr.kind != PRESENT:
                raise AccountsError('keychain_slow', "%s's saved login couldn't be read again; try again." % email)
            tlogin = live_mod.parse(tr.value)
    else:
        res = tokens.adopt_waiting(ctx, target)    # (not run out, yet spent when a renewed one waits: switcher.py)
        if res.outcome == 'adopted':
            tr = vault.read_current(ctx, target, fresh=True, interactive=True)
            if tr.kind != PRESENT:
                raise AccountsError('keychain_slow', "%s's saved login couldn't be read again; try again." % email)
            tlogin = live_mod.parse(tr.value)
        elif res.spent_maybe:
            from .switcher import waiting_error
            raise waiting_error(res, email)
    try:
        with vault.account_lock(ctx, timeout=10.0):
            doc = vault.load(ctx)
            a = doc['accounts'].get(target)
            if a is None:
                raise AccountsError('not_found', 'That account was removed meanwhile.')
            if a.get('conflict'):
                from .switcher import conflict_error
                raise conflict_error(email)    # marked while its login was read (capture's check takes this lock only)
            if target in doc['claims']:
                raise AccountsError('claimed', '%s is already in use by another `switchboard run`.' % email)
            run_dir = ctx.path('run')
            os.makedirs(run_dir, mode=0o700, exist_ok=True)
            folder = tempfile.mkdtemp(prefix='run-', dir=run_dir)
            penv = {'CLAUDE_CONFIG_DIR': folder}
            claim = {'pid': os.getpid(), 'start': ctx.proc_start(os.getpid()), 'dir': folder,
                     'service': facts.keychain_service(penv), 'account': facts.keychain_account(ctx.env),
                     'seeded_fp': tlogin.fingerprint, 'seeded_rx': tlogin.refresh_expires_ms, 'at': ctx.wall(),
                     'links': list(links)}         # recorded before any link is made: taking down checks each one
            doc['claims'][target] = claim
            vault.save(ctx, doc)
            identity_block = a.get('identity') or {'accountUuid': a.get('uuid'), 'emailAddress': a.get('email')}
    except vault.LockBusy:
        raise AccountsError('other', 'Switchboard is busy; try again in a moment.')
    return folder, claim, tlogin, identity_block


def _note_child(ctx, target, pid):
    try:
        with vault.changing(ctx, timeout=10.0) as doc:
            c = doc['claims'].get(target)
            if c is not None:
                c['child'], c['child_start'] = pid, ctx.proc_start(pid)
    except (vault.LockBusy, vault.VaultError, OSError):
        pass


def _run(ctx, cmd, cwd, env, timeout, on_child):
    """Run cmd in this terminal; wait for it (a Ctrl-C goes to it, and we keep waiting so we can tidy up)."""
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env)
    except OSError as e:
        raise AccountsError('other', "Couldn't start Claude Code (%s); check that `claude` is on your PATH." % e)
    if on_child is not None:
        on_child(proc.pid)
    main = threading.current_thread() is threading.main_thread()
    old = signal.signal(signal.SIGINT, signal.SIG_IGN) if main else None
    # a closed Terminal window (SIGHUP) or a kill (SIGTERM) would end this process before the run's login copy and
    # folder are removed: the signal goes on to Claude Code instead, and we keep waiting so we can tidy up
    olds = {}

    def forward(signum, frame):
        try:
            proc.send_signal(signum)
        except OSError:
            pass
    if main:
        for s in (signal.SIGTERM, signal.SIGHUP):
            olds[s] = signal.signal(s, forward)
    try:
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            return 124
    finally:
        if main:
            signal.signal(signal.SIGINT, old)
            for s, h in olds.items():
                signal.signal(s, signal.SIG_DFL if h is None else h)


def _lineage(c, claim):
    return c is not None and c.full_pair and (c.fingerprint == claim.get('seeded_fp') or
                                              identity.renewal_of(c.refresh_expires_ms, claim.get('seeded_rx')))


def finish(ctx, target, say=None):
    """Save back what the run renewed, then remove its item, its folder and the claim. Returns a note or None.

    The folder goes through runfolder.take_down: never through a link. One whose links are not all links any more
    is kept (only its own login file goes), the claim is let go all the same (the login is settled), and that is
    said: in the log, in History, and to say(text) when given."""
    kept = None
    try:
        with vault.account_lock(ctx, timeout=30.0):
            doc = vault.load(ctx)
            claim = doc['claims'].get(target)
            if not isinstance(claim, dict):
                return None
            note = _adopt(ctx, doc, target, claim)
            if note is False:
                claim['cleanup'] = 'its renewed login could not be read yet'
                vault.save(ctx, doc)
                return None
            gone = ctx.keychain.delete(claim['service'], claim['account']) if claim.get('service') else None
            if gone is not None and not gone.ok:
                claim['cleanup'] = 'its Keychain item could not be removed yet (%s)' % gone.why
                vault.save(ctx, doc)
                return note
            folder = claim.get('dir') or ''
            if folder:
                what, detail = runfolder.take_down(folder, claim.get('links'), ctx.path('run'))
                if what != 'gone':
                    email = (doc['accounts'].get(target) or {}).get('email') or target
                    kept = _kept_words(email, folder, what, detail)
            doc['claims'].pop(target, None)
            vault.save(ctx, doc)
    except (vault.LockBusy, vault.VaultError, OSError) as e:
        ctx.log('A run of %s will be tidied later: %s' % (target, e.__class__.__name__))
        return None
    if kept:
        ctx.say(kept, 'warn', h='problem')
        if say is not None:
            say(kept)
    return note


def _kept_words(email, folder, what, detail):
    """One plain sentence for a run's folder that was not removed."""
    if what == 'kept' and isinstance(detail, list):
        names = ', '.join(detail[:3]) + (' and %d more' % (len(detail) - 3) if len(detail) > 3 else '')
        return ('The folder of the run as %s was kept at %s: %s in it %s no longer the link to your Claude Code '
                'folder that the run made, so nothing in it was removed. Take what you need from it, then remove it.'
                % (email, folder, names, 'is' if len(detail) == 1 else 'are'))
    if what == 'kept':
        return ('The folder of the run as %s, %s, is a link now, so it was left as it is.' % (email, folder))
    if what == 'outside':
        return ("The folder of the run as %s, %s, is not in Switchboard's run folder, so it was left as it is."
                % (email, folder))
    return ('The folder of the run as %s, %s, could not all be removed (%s). Nothing it linked to was touched.'
            % (email, folder, detail))


def _adopt(ctx, doc, target, claim):
    """Bring a newer generation from the run's item or file into the vault. None: nothing to do; a note: saved;
    False: the run's item could not be read (so it must not be removed yet)."""
    cands = []
    if claim.get('service'):
        r = ctx.keychain.read(claim['service'], claim['account'], fresh=True, interactive=True)
        if r.kind == UNREADABLE:
            return False
        if r.kind == PRESENT:
            cands.append(live_mod.parse(r.value))
    folder = claim.get('dir') or ''
    if folder:
        fr, raw = ccfiles.read_bytes(os.path.join(folder, facts.CREDENTIALS_FILE))
        if fr.kind == PRESENT:
            try:
                cands.append(live_mod.parse(raw.decode('utf-8')))
            except UnicodeDecodeError:
                pass
    best = None
    for c in cands:
        if _lineage(c, claim) and (best is None or live_mod.compare(c, best) == 'newer'):
            best = c
    if best is None or (best.fingerprint == claim.get('seeded_fp') and best.text and
                        doc['accounts'].get(target, {}).get('expires_ms') == best.expires_ms):
        return None
    a = doc['accounts'].get(target)
    cur = vault.read_current(ctx, target, fresh=True, interactive=True)
    stored = live_mod.parse(cur.value) if cur.kind == PRESENT else None
    if a is not None and stored is not None and (stored.fingerprint == claim.get('seeded_fp') or
                                                 live_mod.compare(best, stored) == 'newer'):
        merged = live_mod.dump(live_mod.compose(best.blob, stored.blob))
        if vault.save_login(ctx, target, merged, old=cur).ok:
            vault.note_login(a, live_mod.parse(merged), 'fingerprint', ctx.wall())
            vault.save(ctx, doc)
            ctx.log('Saved the login the run of %s renewed (%s)' % (a.get('email') or target, best.fingerprint))
            return 'Saved the login the run of %s renewed' % (a.get('email') or target)
    vault.stash(ctx, 'successor:' + target if a is not None else 'safety:' + (best.fingerprint or best.text_fp),
                best.text, target, 'renewed by a run, not placed', supersedes=claim.get('seeded_fp'))
    return None


def reap_stale_claims(ctx, say=None):
    """Settle every claim whose processes are gone (finish: a folder that must be kept is said). [notes]."""
    try:
        doc = vault.load(ctx)
    except vault.VaultError:
        return []
    notes = []
    for aid, c in list(doc.get('claims', {}).items()):
        if isinstance(c, dict) and vault.claim_alive(ctx, c):
            continue
        note = finish(ctx, aid, say=say)
        if note:
            notes.append(note)
    return notes
