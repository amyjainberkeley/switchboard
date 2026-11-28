"""The one surface the rest of Switchboard uses for Claude accounts.

Everything about Claude Code's logins, switching, usage readings and the automatic loop sits behind these
names. The modules beside this file implement them, and each name imports its module only when it is called:
roster.py keeps the saved accounts and reads the live login, switcher.py moves it, tokens.py refreshes, capture.py
keeps logins, runas.py runs Claude Code as an account, uninstall.py removes everything, and usage.py, policy.py,
sessions.py and autoloop.py make up the automatic loop. The rest of Switchboard codes against this file only, so a
name, a field or a signature here changes together with every caller.

Rules every implementation keeps:
- Reads are typed (Read): present, absent or unreadable. "Could not read" never means "empty" or "logged out".
- Nothing here blocks forever: every Keychain, network and subprocess call has a deadline.
- A refresh token is spent only inside tokens.py's transaction. The active login's lineage is never spent here.
- Every switch, kept login, refused refresh and rollback is recorded through switchboard.core.event.
- Account ids are Anthropic account ids. Emails and aliases are labels for people, never the key.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

PRESENT, ABSENT, UNREADABLE = 'present', 'absent', 'unreadable'


# ---------- types ----------

@dataclass(frozen=True)
class Read:
    """The result of reading a secret or a file.

    kind: present, absent (an authoritative "not there": item not found in a usable Keychain, file not found)
    or unreadable (anything else). why: a short reason when not present ('locked', 'denied', 'no_gui',
    'timeout', 'unparseable', 'busy', ...). provenance when present: 'keychain', 'file' or 'cache'.
    degraded: the bytes came from a fallback copy that may be older; show them, never spend or keep them.
    age_s: for a cached value, how old it is."""
    kind: str
    value: Optional[str] = None
    why: Optional[str] = None
    provenance: Optional[str] = None
    degraded: bool = False
    age_s: Optional[float] = None


@dataclass(frozen=True)
class Account:
    """One saved account. status: 'ok', 'needs_login' (the server refused its login, or it lapsed),
    'expiring' (its login lapses within 7 days), 'unreadable' (its saved login can't be read right now),
    'claimed' (a run-as is using it) or 'conflict' (Anthropic said its saved login belongs to another account: it is
    never switched to or renewed until a login proven to be its own replaces it).
    plan is the login's own word for it ('max', 'team'). tier and org_type are what Claude Code's settings said of
    the account when it was last kept (its rate tier, its kind of organization), or None: for naming the plan only."""
    id: str
    email: str
    org_id: Optional[str]
    org_name: Optional[str]
    plan: Optional[str]
    alias: Optional[str]
    order: int
    status: str
    refresh_expires_at: Optional[float]
    added_at: float
    fingerprint: Optional[str]
    tier: Optional[str] = None
    org_type: Optional[str] = None


@dataclass(frozen=True)
class Live:
    """Claude Code's live login, and how sure we are whose it is.

    state: PRESENT (an account login, named by account_id when it can be told), ABSENT (Claude Code is not signed
    in), UNREADABLE (the Keychain did not answer; why says how), 'wiped' (Claude Code emptied its login after
    Anthropic refused it; account_id is the account its settings name) or 'other' (no account login: an API key, MCP
    logins only). proof: 'fingerprint', 'refresh_expiry', 'token_account', 'profile', 'label' (only the config's
    email) or 'none'. disagree: the config's identity block and the credential belong to different accounts.
    from_file: the login was read from Claude Code's plaintext login file because the Keychain said "no such item"
    (Claude Code works from that file; the put-back's look puts it back in the Keychain).
    expires_ms, rx_ms: an account login's access expiry (expiresAt) and sign-in end (refreshTokenExpiresAt), epoch
    milliseconds, as read with it; None when the login has none or is not an account login."""
    state: str
    account_id: Optional[str]
    email: Optional[str]
    proof: str
    disagree: bool = False
    degraded: bool = False
    why: Optional[str] = None
    bearer: Optional[str] = None        # state 'other' because Claude Code is on a kept one-year token: its id
    from_file: bool = False
    expires_ms: Optional[int] = None
    rx_ms: Optional[int] = None


@dataclass(frozen=True)
class SwitchResult:
    """What a switch did. kind: 'manual', 'forced', 'optional' or 'need'. source: 'page', 'menu', 'cli',
    'auto', 'hook' or 'renew' (the keep pass putting the newest sign-in of the account in use back live).
    reason: one plain sentence for the history."""
    ok: bool
    from_id: Optional[str]
    to_id: str
    kind: str
    source: str
    reason: str
    rolled_back: bool = False
    note: Optional[str] = None


@dataclass(frozen=True)
class Window:
    """One limit window. name: '5-hour', 'weekly' or 'model:<display name>' (e.g. 'model:Fable').
    pct: 0..100, None when not known (a window whose reset has passed since it was read is None: it has refilled and
    nothing has been read since). resets_at: epoch seconds, None when not known.
    read_at and source: when and where this window's number was read, when they differ from the Reading's (the
    active account's 5-hour and weekly come from the status line, its model windows from the endpoint). None means
    the Reading's own."""
    name: str
    pct: Optional[float]
    resets_at: Optional[float]
    read_at: Optional[float] = None
    source: Optional[str] = None


@dataclass(frozen=True)
class Reading:
    """One account's numbers. source: 'status_line', 'endpoint' or 'none' (the source of its newest number).
    read_at: when its OLDEST number was read, so its age never overstates how fresh it is. frozen: held back by a
    rate limit, so the real numbers can only be higher. error: a plain reason when the numbers aren't current.
    checked_at: when the account was last answered for (Anthropic's endpoint, or Claude Code's status line), at least
    read_at: a window whose newer answer was not believed keeps its own older time, yet the account was checked.
    elsewhere: read on another Mac and taken here (`switchboard readings take`): shown, never decided on."""
    account_id: str
    windows: Tuple[Window, ...]
    read_at: Optional[float]
    source: str
    frozen: bool = False
    error: Optional[str] = None
    checked_at: Optional[float] = None
    elsewhere: bool = False


@dataclass(frozen=True)
class Decision:
    """What the policy says now. action: 'stay' or 'switch'. kind: 'forced', 'optional' or 'need'.
    hold: when an optional switch is wanted but waits, the plain reason (busy, an open doc, too soon...)."""
    action: str
    target: Optional[str] = None
    kind: Optional[str] = None
    reason: str = ''
    hold: Optional[str] = None


@dataclass(frozen=True)
class PutBack:
    """What one look for a lost sign-in did (putback.py). action: 'put_back' (the saved login was written and read
    back), 'wait' (seen missing; looking again), 'failed' (the write did not land; tried again at the next look),
    'limit' (stopped for now) or 'leave'. account: the account's email. reason: plain words for why. line: what was
    said this time, None when nothing new. watch: the next look should come soon. at: when the first put-back of this
    episode was made (one notification per episode).
    Also 'promote' (the login Claude Code kept only in its plaintext file was put back in the Keychain) and, from
    watch_look, 'kept' (the saved copy was brought up to Claude Code's newer login). next_s: seconds until the caller
    should look again, or None. seen: plain facts for a record (words, booleans, 16-hex fingerprints; no token, email
    or account id). account_id: the saved account the settings name (account is its email), or None. key: the rule's
    key for what it found ('refused', 'ended', ... for the saved login's marks, 'limit', 'switched', 'file',
    'file_owner', 'file_limit', 'record', 'sealed'), None when there is none (a put-back, a wait, ...)."""
    action: str
    account: Optional[str] = None
    reason: str = ''
    line: Optional[str] = None
    watch: bool = False
    at: Optional[float] = None
    next_s: Optional[float] = None
    seen: Optional[Dict[str, object]] = None
    account_id: Optional[str] = None
    key: Optional[str] = None


class AccountsError(Exception):
    """A failure a person can act on. str() is one plain sentence that says what to do next."""
    KINDS = ('keychain_locked', 'keychain_slow', 'keychain_denied', 'claude_busy', 'not_found', 'ambiguous',
             'dead_login', 'network', 'rate_limited', 'unreadable_live', 'disagree', 'claimed', 'disk', 'other',
             'in_use')

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind if kind in self.KINDS else 'other'


# ---------- accounts and logins ----------

def accounts() -> List[Account]:
    """Every saved account, in the person's order. Reads metadata only: never the Keychain, never the network."""
    from . import context, roster
    return roster.accounts(context.current())


def live() -> Live:
    """Claude Code's live login and whose it is. Bounded: at most one Keychain read (cached by change signal)."""
    from . import context, roster
    return roster.live_view(context.current())


def find(target: str) -> Account:
    """An account by id, email, alias or its 1-based number. Raises AccountsError not_found or ambiguous."""
    from . import context, roster
    return roster.find(context.current(), target)


def switch(target: str, *, kind: str = 'manual', source: str = 'cli', reason: str = '') -> SwitchResult:
    """Move Claude Code's live login to target: the full transaction in switcher.py (lock, keep the outgoing
    login with proof, write, read back, verify, roll back on mismatch, journal). Raises AccountsError when
    nothing was changed; returns rolled_back=True when it changed and undid it."""
    from . import context, roster, switcher
    ctx = context.current()
    roster._refuse_inside_run(ctx)
    result = switcher.switch(ctx, _account_or_token(ctx, target), kind=kind, source=source, reason=reason,
                             interactive=source in ('page', 'menu', 'cli'))
    roster.forget_live(ctx)
    return result


def add_current() -> Account:
    """Keep the live login as an account (or refresh the saved copy of the account it already is)."""
    from . import context, roster
    return roster.add_current(context.current())


def add_login(config_dir: str) -> Account:
    """Keep the login Claude Code saved for a throwaway config folder: the page's
    Add runs Claude Code's own sign-in with CLAUDE_CONFIG_DIR=config_dir, so the live login is never touched. Reads
    that folder's own item (its hashed Keychain item, else <config_dir>/.credentials.json) and its identity block
    (<config_dir>/.claude.json), keeps the login with proof like add_current (Anthropic's profile answer), removes that
    folder's item once the kept copy reads back, and never writes the live login, except when the account is the one
    Claude Code is on and its live login is gone, emptied or refused by the server: then the new login is made live
    through the switch transaction. Raises AccountsError (unreadable_live when the folder holds no login)."""
    from . import context, roster
    return roster.add_login(context.current(), config_dir)


def remove_sign_in(config_dir: str) -> bool:
    """Remove the Keychain item Claude Code's own sign-in left for a throwaway config folder (for the page's Add,
    once its sign-in is over or was cut off). Named by Claude Code's own rule for that folder and
    removed through the Keychain wrapper (deadline, never a killed dialog). Refuses, with AccountsError, what add_login
    refuses: Claude Code's own folder or item, a `switchboard run` folder. True when the item is gone or was never
    there. Never the live login."""
    from . import context, roster
    return roster.remove_sign_in(context.current(), config_dir)


def remove(target: str, *, everything: bool = False) -> None:
    """Forget a saved account: its vault items and metadata, its auto-switch rule and the one-year tokens kept for
    it. Never touches the live login, so it is refused while Claude Code is on such a token (switch away first).
    everything: the uninstaller's --all, after which nothing switches: the account goes even then."""
    from . import autoloop, context, roster
    ctx = context.current()
    roster._refuse_inside_run(ctx)
    aid = roster.find_id(ctx, target)
    roster.remove(ctx, aid, even_in_use=everything)     # a token kept for it goes with it
    autoloop.forget_rules([aid])
    from . import folders
    folders.forget_account([aid])              # the folders set to run as it go with it


def set_alias(target: str, alias: Optional[str]) -> None:
    """Give an account a short name (None removes it). Aliases are unique and never look like an email or a number."""
    from . import context, roster
    roster.set_alias(context.current(), target, alias)


def check_alias(alias: Optional[str], keep_for: Optional[str] = None) -> Optional[str]:
    """Would set_alias take this short name? Changes nothing. keep_for: the id of an account that may already have
    it. Returns the name as it would be kept (None for none); raises AccountsError, in set_alias's words, when not."""
    from . import context, roster
    return roster.check_alias(context.current(), alias, keep_for)


def leftovers() -> List[Dict[str, object]]:
    """The recovery copies Switchboard holds (logins kept aside, none of them an account's login in use), oldest
    first, with no secret: [{'id' (6 hex digits to type), 'key', 'account_id', 'email' (of the account it was kept
    for, when known), 'why', 'at', 'where' ('keychain' or 'file'), 'needed', 'need', 'needed_by'}] (recovery.py)."""
    from . import context, recovery
    return recovery.listing(context.current())


def clear_leftovers(ids: List[str], everything: bool = False, only_copy: bool = False) -> Dict[str, list]:
    """Remove the recovery copies named by id (or every one), except any still needed to put a login back: each is
    judged again under the switch and account locks. only_copy: a copy named by id that is the only copy of a login
    that was in use goes too. {'cleared', 'kept', 'failed', 'unknown'} (recovery.clear)."""
    from . import context, recovery
    return recovery.clear(context.current(), ids, everything=everything, only_copy=only_copy)


def reorder(ids: List[str]) -> None:
    """Set the order accounts are listed and tried in."""
    from . import context, roster
    roster.reorder(context.current(), ids)


def run_as(target: str, argv: List[str], *, cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None,
           timeout: Optional[float] = None, bare: bool = False, before=None, say=None, apart: bool = False,
           strict: bool = False) -> int:
    """Run `claude <argv>` as target without touching the live login (runas.py). Returns its exit code.
    The run shares the person's Claude Code folder (chats, memory, instructions, settings,
    hooks, skills) unless bare; apart: all of it but the chat history; strict: AccountsError('in_use') rather than
    run on the person's own folder; before(plain) is called just before Claude Code starts, plain True when it runs on
    the login Claude Code is on; say(text) gets a sentence for the person when the run's folder had to be kept."""
    from . import context, roster, runas
    ctx = context.current()
    return runas.run_as(ctx, roster.find_id(ctx, target), argv, cwd=cwd, env=env, timeout=timeout, bare=bare,
                        before=before, say=say, apart=apart, strict=strict)


def keep_pass() -> Optional[str]:
    """One pass of login keeping (capture.py). Returns a plain note when a login was kept or resynced."""
    from . import capture, context, roster
    ctx = context.current()
    note = capture.keep_pass(ctx)
    if note:
        roster.forget_live(ctx)
    return note


def put_back_pass() -> PutBack:
    """One look for a sign-in Claude Code lost while its settings still name a saved account, and the put-back when
    the rule says so (putback.py): one Keychain read while a login is there. A put-back or a promote forgets the cached
    live read. Never writes over an account login, never undoes a sign-out the person chose."""
    from . import context, putback, roster
    ctx = context.current()
    res = putback.look(ctx)
    if res.action in ('put_back', putback.PROMOTE):
        roster.forget_live(ctx)
    return res


def watch_look(hint: Optional[Dict[str, object]] = None) -> PutBack:
    """One look for the sign-in watch and the page, at once when something changed. hint: what changed
    ({'keychain': bool, 'login_file': 'appeared'|'gone'|'changed'|None, 'settings': bool, 'tick': bool}) or None.
    In order, each only when needed: note evidence of the fault, look, then put the login in Claude Code's plaintext
    file back in the Keychain, or put the saved login back, or keep Claude Code's newer login. A put-back made here is
    told as the loop tells it: one notification per episode; one History line per episode that saw proof of the fault
    (its first put-back says it, the later ones are counted in the log), else one per put-back (at most four an hour).
    Returns PutBack with next_s and seen.
    Never writes over an account login, never undoes a sign-out the person chose."""
    from . import context, putback, roster
    ctx = context.current()
    res = putback.watch_look(ctx, hint)
    if res.action in ('put_back', putback.PROMOTE, 'kept'):
        roster.forget_live(ctx)
    if res.action == 'put_back':
        try:
            from . import autoloop
        except ImportError:
            autoloop = None
        tell = getattr(autoloop, 'notify_put_back', None)
        if tell is not None:
            tell(res)
    return res


def watch_hold(event: str) -> Dict[str, object]:
    """The sign-in watch's call at the moment Claude Code's plain login file 'appeared', 'changed' or 'gone', or its
    settings changed ('settings'), after each of its looks ('looked'), and once at its start ('start'): keeps what
    Claude Code's fallback wrote in that file for a few seconds and puts it back the moment it goes, within the bounds
    written above putback.HOLD_FOR_S (putback.hold_seen); says when a removal it could not answer from memory is owed
    a look at once, which answers it from the Keychain item ('late'); takes a file the hold put there away again when
    the settings name nobody or another account, and at the start (putback.hold_tidy), and says when a look is due
    that takes one away ('look_in'). After a look, what that look did with the file when the tidy did nothing (a
    spare copy of the sign-in made, a file brought up to the item, or taken away: putback.hold_spared). No Keychain
    call, no network. Returns plain facts for the record: {'did': 'kept', 'held', 'removed', 'spare', 'followed' or
    None, 'made', 'failed', ...}; no secret."""
    from . import context, putback
    ctx = context.current()
    if event in ('appeared', 'changed', 'gone'):
        return putback.hold_seen(ctx, event)
    if event in ('settings', 'start', 'looked'):
        out = putback.hold_tidy(ctx, start=event == 'start')
        if event == 'looked' and not out.get('did'):
            out.update(putback.hold_spared(ctx))
        return putback._hold_facts(ctx, out)
    return {'did': None}


def hold_on() -> bool:
    """Whether the sign-in watch puts Claude Code's plain login file back when it goes right after a failed save: the
    setting auto_policy -> hold_signin_file in Switchboard's settings.json (on unless it is false). For doctor."""
    from . import context, putback
    return putback.hold_on(context.current())


def mirror_on() -> bool:
    """Whether Claude Code's plain login file is kept as a standing copy of its Keychain item: the setting
    auto_policy -> keep_signin_file in Switchboard's settings.json (on unless it is false), with hold_signin_file on.
    For doctor and the page."""
    from . import context, putback
    return putback.mirror_on(context.current())


def last_put_back() -> Optional[Dict[str, object]]:
    """When Claude Code's login was last put back or promoted, for doctor: {'at': wall seconds, 'account': email,
    'how': 'put_back' or 'promote' (absent for one noted before promotes existed)}, or None."""
    from . import context, putback
    return putback.last_put_back(context.current())


def upkeep_pass(budget_s: float = 60.0) -> List[str]:
    """Refresh idle saved logins that need it, within budget_s (tokens.py). Returns plain notes."""
    from . import context, tokens
    return tokens.upkeep_pass(context.current(), budget_s)


def resolve_journal(older_than: Optional[float] = None) -> Optional[str]:
    """Finish or roll back a switch that was cut off, from what the live login really holds. Run at start.
    older_than (the loop, at each pass): only a journal last written more than that many seconds ago, judged while
    holding the switch lock, so a switch under way (it holds that lock, and settles a journal itself first) is never
    raced; the lock is waited for a second at most. Then nothing is said here: the caller says the note once."""
    import os
    from . import context, roster, switcher, vault
    ctx = context.current()
    if older_than is None:
        note = switcher.resolve_journal(ctx)
    else:
        note = None
        try:
            with vault.switch_lock(ctx, timeout=1.0):
                try:
                    age = ctx.wall() - os.stat(switcher.journal_path(ctx)).st_mtime
                except OSError:
                    age = None                  # no journal (settled meanwhile)
                if age is not None and age > older_than:
                    note = switcher._resolve_locked(ctx)
        except vault.LockBusy:
            pass
    if note:
        roster.forget_live(ctx)
    return note


def health() -> Dict[str, object]:
    """For doctor and the page: Keychain state, Claude Code facts check, journal, store version, claims."""
    from . import context, roster
    return roster.health(context.current())


def access_token(account_id: str, *, fresh: bool = False) -> Read:
    """An access token to read usage with, for the usage poller only (usage.py). The active account's comes from
    Claude Code's live login and is never refreshed here: unreadable, why 'expired', once it has lapsed (Claude Code
    refreshes it when it is next used). Another account's comes from the vault, refreshed through tokens.py's
    transaction when it has expired or when fresh=True (the endpoint just answered 401). present: value = the token.
    Unreadable, why 'conflict', for an account whose saved login Anthropic says is another account's (its numbers would
    be that account's). The caller never stores or logs it. Bounded like every Keychain read."""
    from . import context, roster
    return roster.access_token(context.current(), account_id, fresh=fresh)


# ---------- usage, policy, activity, the automatic loop ----------
# Built in usage.py, policy.py, sessions.py and autoloop.py; imported here only when called (they import this file).

def readings() -> Dict[str, Reading]:
    """The latest reading per account id, from the readings store (~/.switchboard/usage.json). Never reads the network
    or the Keychain. Each window carries its own read_at and source when they differ from the reading's; checked_at is
    when the account was last answered for. A reading taken from another Mac (readings.py) stands in for an older one
    of this Mac's while its time lasts, marked elsewhere; automatic switching reads usage.readings, never these."""
    from . import usage
    from .. import readings as taken
    return taken.shown(usage.readings())


def request_read(all_accounts: bool = True) -> float:
    """Ask the running loop to read now: the account in use, and with all_accounts every account, each unless it was
    read in the last minute or the pace says wait. Returns the request's time; the loop's heartbeat (auto_status'
    'asked') carries it once the loop has answered. Raises OSError when it can't be written."""
    from . import autoloop
    return autoloop.request_read(all_accounts)


def feed_status_line(account_id: str, rate_limits: Dict[str, object], at: float) -> None:
    """Claude Code's status line hands in live numbers for the account it runs on (its `rate_limits`, as the status
    line's JSON has them). Refused when older than 10 minutes, dated past the clock, already refilled, or carrying
    another account's open window."""
    from . import usage
    usage.feed_status_line(account_id, rate_limits, at)


def activity() -> Dict[str, object]:
    """{'busy': bool, 'idle_for_s': float or None, 'sessions': [...], 'obligations': [{'account_id', 'org', 'kind'
    ('artifact', 'doc', 'watch' or 'connector'), 'url' (a connector: 'connector', its name), 'until'}]}. 'org' is the
    organization the chat's own file says it was on, when it says. Cheap: file times and one process listing,
    bounded."""
    from . import autoloop, sessions
    return sessions.activity(autoloop.timeline_of(autoloop.load_state()))


def decide(now: Optional[float] = None) -> Decision:
    """The policy's answer for the current snapshot (policy.decide is pure; this gathers its inputs from disk and at
    most one bounded read of the live login; never the network)."""
    from . import autoloop
    return autoloop.decide_now(now)


def best() -> Decision:
    """The account a forced switch would land on now, for a move a person asks for (`switchboard switch --best`):
    policy.best on a snapshot gathered now (no network). kind 'manual'; 'stay' with the reason when there is none."""
    from . import autoloop
    return autoloop.best_now()


def auto_settings() -> Dict[str, object]:
    """{'enabled', 'session_at', 'weekly_at', 'model_at', 'model', 'strategy'}, and 'rules' while an account has one:
    {account id: 'last' (used last) or 'out' (kept out of auto-switch)}, an account with no rule not in it. Likewise
    'own_lines' ({account id: {'session_at', 'weekly_at'}}, each only when the account has its own), 'ranks' ({account
    id: 1 to 99}) and 'models' (every model counted, only when there are several); 'pick_hold' and 'sign_lines'
    only when they are not their defaults."""
    from . import autoloop
    return autoloop.settings_dict()


def set_auto(enabled=None, session_at=None, weekly_at=None, model_at=None, model=None, strategy=None,
             rule=None, line=None, rank=None, pick_hold=None, sign_lines=None) -> Dict[str, object]:
    """Change automatic switching; returns the new settings. rule: (account id, 'in' | 'last' | 'out') for one saved
    account ('in' takes its rule away). line: (account id, {'session_at': N, 'weekly_at': N} or None), that account's
    own lines (None, or a key with None, puts it back on the shared line). rank: (account id, 1 to 99 or None). model:
    a name, names with commas between them, or a list. Unknown values raise AccountsError (not_found for an account
    not saved)."""
    from . import autoloop
    more = dict((k, v) for k, v in (('line', line), ('rank', rank), ('pick_hold', pick_hold),
                                    ('sign_lines', sign_lines)) if v is not None)
    return autoloop.save_settings(enabled=enabled, session_at=session_at, weekly_at=weekly_at, model_at=model_at,
                                  model=model, strategy=strategy, rule=rule, **more)


def auto_status() -> Dict[str, object]:
    """The loop's heartbeat: {'pid', 'stage', 'last_pass_at', 'stuck_for_s', 'lease', 'waiting_for'} and more
    ('restart' and 'why' when it is stuck; see autoloop.status)."""
    from . import autoloop
    return autoloop.status()


def request_need_switch(account_id: str, why: str) -> Optional[SwitchResult]:
    """A switch something needs right now (e.g. Claude in Chrome follows the extension's account). Goes only
    when that account has room; at most once per 10 minutes per reason. None when it didn't go."""
    from . import autoloop
    return autoloop.need_switch(account_id, why)


# ---------- the uninstaller ----------

def remove_all() -> List[str]:
    """For the uninstaller's --all (`switchboard tidy --all`, after it removed each account): every account, and every
    login of Switchboard's still in the Keychain: "Switchboard login", "Switchboard login (previous)" and "Switchboard
    recovery" (a refresh's successor, a switch's safety copy), with the recovery files. Claude Code's live login is
    never touched. Returns one plain line per kind of thing removed. Raises AccountsError, removing nothing, inside a
    run, while a run holds an account, or while a cut-off switch can't be settled yet; and, naming what is left, when
    an item could not be removed (the records that find it stay, so it can run again)."""
    from . import autoloop, bearer, context, uninstall
    said = uninstall.remove_all(context.current())
    autoloop.forget_rules()
    n = bearer.remove_all(context.current())
    if n:
        said.append('One-year tokens and API keys kept as accounts: %d removed.' % n)
    return said


# ---------- a one-year token or an API key kept as an account (bearer.py) ----------

def _account_or_token(ctx, target):
    """The id of a saved account named `target`, else of a kept token or key called that; AccountsError as find."""
    from . import bearer, roster
    try:
        return roster.find_id(ctx, target)
    except AccountsError as e:
        if e.kind != 'not_found':
            raise
        bid = bearer.find_id(roster._load(ctx), target)
        if bid is None:
            raise
        return bid


def kept_tokens() -> List[Dict[str, object]]:
    """Every one-year token and API key kept as an account (bearer.view: no secret), with 'active' marking the one
    Claude Code is on. Reads metadata, and the cached live view only when one is kept."""
    from . import bearer, context, roster
    ctx = context.current()
    doc = roster._load(ctx)
    if not bearer.records(doc):
        return []                                       # none kept: the live login is not looked at
    try:
        lv = roster.live_view(ctx)
    except AccountsError:
        lv = None
    return bearer.view(doc, getattr(lv, 'bearer', None))


def find_token(name: str) -> Optional[Dict[str, object]]:
    """A kept token or key by its id, short name or label (bearer.view's entry), or None."""
    from . import bearer, context, roster
    doc = roster._load(context.current())
    bid = bearer.find_id(doc, name)
    return next((x for x in bearer.view(doc) if x['id'] == bid), None) if bid else None


def add_token(text: str, *, for_target: Optional[str] = None, label: Optional[str] = None,
              alias: Optional[str] = None) -> Tuple[Dict[str, object], str]:
    """Keep a one-year token or an API key as an account (bearer.add): for the saved account for_target (a token
    only), or under a label of its own. Returns (its entry, 'added' | 'replaced' | 'same'). Sends nothing anywhere."""
    from . import bearer, context, roster
    ctx = context.current()
    roster._refuse_inside_run(ctx)
    aid = roster.find_id(ctx, for_target) if for_target else None
    rec, what = bearer.add(ctx, text, for_id=aid, label=label, alias=alias)
    entry = next((x for x in bearer.view(roster._load(ctx)) if x['id'] == rec['id']), None)
    return entry or {}, what


def remove_token(name: str) -> str:
    """Forget a kept token or key (bearer.remove). Returns what it was called."""
    from . import bearer, context, roster
    ctx = context.current()
    roster._refuse_inside_run(ctx)
    bid = bearer.find_id(roster._load(ctx), name)
    if bid is None:
        raise AccountsError('not_found', 'No one-year token or API key is called "%s".' % name)
    return bearer.remove(ctx, bid)


def set_token_rule(name: str, rule: str) -> str:
    """A kept token's or key's rule in auto-switch (bearer.set_rule: a key only 'out', a token 'out' or 'last')."""
    from . import bearer, context, roster
    ctx = context.current()
    bid = bearer.find_id(roster._load(ctx), name)
    if bid is None:
        raise AccountsError('not_found', 'No one-year token or API key is called "%s".' % name)
    return bearer.set_rule(ctx, bid, rule)
