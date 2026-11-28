"""Putting Claude Code's sign-in back when Claude Code lost it and nobody signed out.

What Claude Code does (as of 2.1.329 and 2.1.331): every running Claude Code process saves the
whole Keychain item from its own copy. When a save fails, Claude Code writes the object to its plaintext login file
and deletes the Keychain item; a process that reads while the item is gone holds "no login", and its next save brings
the item back without the account login (only connector logins). Many processes save at once right after a switch
(the VS Code extension restarts one per open chat), so that is when the login can be lost this way. Claude Code's
settings (oauthAccount in its global config) still name the account, and nothing in Claude Code puts the login back.
A running process trusts its cached read of the item for 30 s, so a chat may say "Not logged in" that long after the
login is back.

A sign-out the person chooses (/logout) deletes the item too, and then clears oauthAccount (the sign-out fails when it
can't). So settings that name nobody are a chosen sign-out, and nothing here ever undoes one. The item goes a moment
before the settings change, so the login must be seen missing, with the settings naming the same saved account, for
PUT_BACK_AFTER_S at least; and everything is read again under the locks just before the write.

decide() is the rule, with no I/O. look() reads what the rule needs (one Keychain read while a login is there),
decides, and writes when the rule says so: under the locks a switch takes, never over an account login (older or
newer), with the live item's machine-wide parts kept as they are now, read back before it is said.

Processes that read the item while it was gone may save it again a few seconds after a put-back, without the login.
So each put-back within PUT_BACK_SPAN_S waits longer than the one before (PUT_BACK_WAITS), and after PUT_BACK_MAX it
stops and asks for /login. Several processes saving the item at once also refuse each other's writes (security exits
45), so a write that does not land is tried again at once, up to TRIES times within TRIES_WITHIN_S, each after a fresh
read; only a write that reads back counts. A look whose tries all fail spaces the next tries by PUT_BACK_WAITS too,
without counting toward the limit. When the login was first seen missing and when each put-back landed are
kept in <data>/put-back.json (no secrets) under the account lock, so every process that looks shares one wait and one
limit, and a restart resets neither.
"""
import hashlib
import json
import os
import random
import secrets
import stat
import threading
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from . import capture, ccfiles, identity, keychain, live as live_mod, switcher, vault
from .api import ABSENT, PRESENT, UNREADABLE, PutBack, Read

PUT_BACK_AFTER_S = 4.0          # the login must be seen missing at least this long before the first put-back
PUT_BACK_WAITS = (PUT_BACK_AFTER_S, 15.0, 60.0, 300.0)     # the wait before each put-back within PUT_BACK_SPAN_S
PUT_BACK_MAX = 4                # put-backs within PUT_BACK_SPAN_S before it stops and asks for /login
PUT_BACK_SPAN_S = 3600.0
TRIES = 5                       # writes tried at once when one does not land
TRIES_WITHIN_S = 3.0
TRY_GAP_S = (0.1, 0.4)          # a random gap between two tries, so processes saving at once fall apart
CC_LOCKS_S = 2.0                # the wait for Claude Code's two credential locks (a renewal holds them about a second)
STATE = 'put-back.json'
SIGNINS = 'logins'              # the page's sign-in folders (connect.py): one not marked over is under way
SIGNIN_OVER = 'over'
SIGNIN_FRESH_S = 1800.0         # connect.py hands a sign-in folder to the engine only when it is this fresh
# A Claude Code session open during a switch can write the earlier account's name back into the settings a moment later
# (capture._label_back corrects it, but only while a login is there). A loss first seen this soon after a switch away
# from the account the settings name is not put back by that name, unless that account was signed in again after the
# switch: its record carries a sign-in end stamp that is not the sign-in the switch left (the switch's record keeps that
# one as 'left_rx'; a renewal keeps the stamp, a new sign-in moves it: facts.same_sign_in), or Claude Code's plain login
# file holds a login proven to be its newest (the switch rewrote an existing file with the other account's login, so
# that one was written since). The time a login was saved tells nothing here: Switchboard's own renewal of an idle
# saved login, or a `switchboard run` as that account, saves it too.
SWITCHED_AWAY_S = 600.0

NO_LOGIN = ('no_login', 'empty')
# Claude Code 2.1.331, behind a server flag, can keep its item in a split form: the item carries a pointer
# (facts.OVERFLOW_KEY) to overflow items that hold the parts that grow, such as connector logins. An ordinary
# account's login stays in the item itself, so an item with the pointer and no login is a lost sign-in like any other,
# and a put-back keeps the item's own pointer (live.compose). Only when the pointer itself carries 'login' does the
# account login sit in an overflow item (live.parse calls that 'sealed'): signed in, in a form Switchboard cannot read,
# so never missing.
SEALED = 'sealed'
SEALED_LINE = ("Claude Code keeps this login in a form Switchboard cannot read (a split Keychain item); Switchboard "
               "left the item as it is")
LIMIT_WORDS = ('Claude Code keeps losing its sign-in. Switchboard has stopped putting it back for now. Run /login in '
               'Claude Code once.')
_RAND = random.Random()

# Proof of the fault. Claude Code writes its plaintext login file only in its fallback after a Keychain save failed
# (checked in 2.1.329 and 2.1.331). A sign-out the person chooses never writes it: it deletes the item, removes the
# file, then clears the settings' account a moment later (a sign-in inside Claude Code runs the same steps first). So
# only a fresh write of that file by Claude Code is proof: the sign-in watch saw it written since the last look, or a
# look finds it written less than FAULT_SEEN_S ago. The file going away is not (a sign-out removes it), nor is a file
# that has been there a while (after a pile-up of saves it often stays for good beside a good item), nor a rewrite of
# it by a write of Switchboard's own. Measured on the real Keychain machinery: every save that wrote the item without
# the login came within 5.3 s of the last fallback write, and each refused save writes the file again. With fresh
# proof, two sightings FAULT_AFTER_S apart are enough; without it the login must be seen missing for PUT_BACK_AFTER_S,
# so the moment of a chosen sign-out is never undone. Never one look: a sign-in inside Claude Code writes the item
# twice within about a second, the first time small. A loss within FAULT_SEEN_S of a write of ours is proof only
# inside an episode that has seen proof: a person who sees "Not logged in" after a put-back and signs out or in at
# once makes the same loss.
FAULT_SEEN_S = 10.0
FAULT_AFTER_S = 0.5
# With proof, a try after looks whose writes all failed waits this long, by the count of such looks in a row (without
# proof, PUT_BACK_WAITS): a Keychain that refuses every write is not asked again and again.
FAULT_FAIL_WAITS = (0.5, 1.0, 2.0, 4.0, 8.0, 15.0, 30.0, 60.0)
# With evidence a put-back is made again as often as it is undone, within these limits. An episode runs from its first
# write until EPISODE_QUIET_S pass with no loss seen, and never longer than EPISODE_MAX_S.
EPISODE_WRITES = 12
EPISODES_PER_HOUR = 4
EPISODE_QUIET_S = 60.0
EPISODE_MAX_S = 300.0
# After a write of ours the sign-in watch sees every save of the item by itself (each one replaces the Keychain file),
# so a process that read the item while it was gone and saves it again is seen at once. A look is still asked for at
# these seconds after the write, in case a change was missed, and none after the last.
AFTER_WRITE_LOOKS = (1.0, 2.0, 4.0, 8.0, 15.0)
AFTER_WRITE_NEXT_S = AFTER_WRITE_LOOKS[0]
AFTER_WRITE_S = AFTER_WRITE_LOOKS[-1]
# A landed write is noted (how it was made, a SHA-256 of the item's text and of its account login, the plaintext
# file's stamp after it, the settings file's size before it; no secret) for this long, and taken back within it when
# it met a sign-out (_undo).
TAKE_BACK_S = 60.0
TAKEN_BACK_LINE = ('Claude Code was signing out when Switchboard put back the login of %s; Switchboard took its write '
                   'back')
PROMOTE = 'promote'
PROMOTE_LINE = ('Claude Code kept its sign-in only in its plain login file. Switchboard put it back in the '
                'Keychain.')
# Each `security` call takes 0.3 to 1.1 s on a busy Mac, and while the login is gone every Claude Code process that
# saves writes an item without it. So the look that decides decides from the live read and the account's record in the
# accounts file (see(saved=RECORD): the saved login's fingerprints and times, written with every saved login), with no
# Keychain read of the saved login; that login is read once under the engine's locks, before Claude Code's save lock
# is taken (never inside it), and must be the one the record describes, or a full login newer than it
# (_newer_than_record); under the save lock come the fresh live read, the decision again, the write and the read-back.
# A login Claude Code keeps in its plaintext file needs no saved login unless the file holds an older generation.
RECORD = 'record'
UNLIKE_RECORD_LINE = ("The saved login of %s is not the one Switchboard's record of it describes, so Switchboard did "
                      "not write it into Claude Code's Keychain item")
# A look that follows a sighting of the loss on record goes straight to the locks (_straight): it reads nothing of
# Claude Code's item outside them, and its live read is the one under Claude Code's save lock, where the whole decision
# is made again anyway. The record notes how the loss was seen ('missing_how', one of SIGHTED): 'item' (the item gone or
# without a login, no login in the plaintext file: a put-back), 'file' (the Keychain said "no such item" and the login
# is in the plaintext file: a promote), 'beside' (an item without a login beside a plaintext file holding a proven
# login: the repair). A sighting older than SIGHTING_STALE_S is not trusted: that look reads outside the locks first.
SIGHTING_STALE_S = 30.0
# After a look that went straight to Claude Code's save lock and got no answer to its read there (the call was cut at
# the short limit calls have under that lock, or Switchboard's own Keychain gate was busy for all of it), this
# process's looks read outside the locks first again for this long. That lock is Claude Code's own, and its saves wait
# while it is held: it is not taken again for a read until a read made without it has been answered.
STRAIGHT_OFF_S = 10.0
SIGHTED = ('item', 'file', 'beside')
# A chosen sign-out (/logout, `claude auth logout`) deletes Claude Code's item and its plain login file, then clears
# oauthAccount in its settings a moment later (2.1.329, read in its program text). While the standing copy is on, an
# item that is gone with no plain login file beside it, or with only our copy beside it, is what a sign-out passes
# through: no fallback write of Claude Code's proves a fault there. So, without that proof, nothing is written back
# until the item has been missing this long with the settings still naming the account; then it is a loss.
SIGN_OUT_S = 10.0


@dataclass(frozen=True)
class Seen:
    """What one look found. live: the Read kind of Claude Code's login (PRESENT, ABSENT, UNREADABLE); degraded: only a
    fallback copy could be read; login: Login.kind when present; file_login: Claude Code's
    plaintext login file holds an account login. config: the Read kind of Claude Code's settings; named: the saved
    account its identity block names, or None. saved ('ok', 'missing', 'unreadable', 'incomplete') and the marks after
    it are about that account. part_way: a switch's journal is there; signing_in: a sign-in is under way on the page.
    left_at: wall time of the last recorded switch when it went from the named account to another, else None (also
    once that account was signed in again after the switch: _signed_in_since).
    from_file: the Keychain said "no such item" and the account login came from Claude Code's plaintext file; full: that
    login has both tokens; owner: it is the named account's (its refresh token is the saved one's, or it is the same
    sign-in), never older than the saved copy; older: it is that account's sign-in, older than the saved copy;
    file_there: the plaintext file exists (looked at once the login is missing); file_fresh: it was written less than
    FAULT_SEEN_S ago by its modification time. Beside an item without a login (file_login), full, owner and older are
    about the login in that file. standing: the standing copy of the sign-in is on (mirror_on); file_ours: the plain
    login file is one of ours (the sign-in hold's note knows its stamp)."""
    live: str
    degraded: bool = False
    login: Optional[str] = None
    file_login: bool = False
    config: str = PRESENT
    named: Optional[str] = None
    saved: Optional[str] = None
    refused: bool = False
    ended: bool = False
    conflict: bool = False
    unproven: bool = False
    claimed: bool = False
    renewing: bool = False
    part_way: bool = False
    signing_in: bool = False
    left_at: Optional[float] = None
    from_file: bool = False
    full: bool = False
    owner: bool = False
    older: bool = False
    file_there: bool = False
    file_fresh: bool = False
    standing: bool = False
    file_ours: bool = False


@dataclass(frozen=True)
class Verdict:
    """action: 'put_back', 'promote' (put the login in Claude Code's plaintext file back in the Keychain), 'wait' (seen
    missing; look again), 'leave' or 'limit'. account: the saved account the
    settings name. key: what is said once ('refused', 'limit', ...), None when there is nothing to say. missing:
    (account, wall time first seen missing), kept for the next look. watch: the next look should come soon."""
    action: str
    account: Optional[str] = None
    reason: str = ''
    key: Optional[str] = None
    missing: Optional[Tuple[str, float]] = None
    watch: bool = False


def _kind(login):
    """Login.kind, or None when there is no login object."""
    if login is None:
        return None
    return login.kind


def is_missing(seen):
    """Claude Code's login is gone, as an authoritative answer: no item (and no login file), or an item (or file)
    that holds no account login. Never a failed read, a fallback copy, an API key or an emptied login."""
    if seen.live == ABSENT:
        return True
    return seen.live == PRESENT and not seen.degraded and seen.login in NO_LOGIN


# The marks that keep an account's saved login out of use, in the order they are told: (key, words after "the saved
# login of <email>").
MARKS = (('missing', 'is not in the Keychain'),
         ('unreadable', "can't be read from the Keychain right now"),
         ('incomplete', 'is incomplete'),
         ('refused', 'was refused by Anthropic'),
         ('ended', 'has passed its sign-in end date'),
         ('conflict', 'may belong to another account'),
         ('unproven', 'was not checked with Anthropic yet'),
         ('claimed', 'is in use by a `switchboard run`'),
         ('renewing', 'is being renewed, or its renewal was cut off'))


def _marked(seen):
    return {'missing': seen.saved == 'missing', 'unreadable': seen.saved == 'unreadable',
            'incomplete': seen.saved == 'incomplete', 'refused': seen.refused, 'ended': seen.ended,
            'conflict': seen.conflict, 'unproven': seen.unproven, 'claimed': seen.claimed, 'renewing': seen.renewing}


def wait_for(done, now):
    """How long the login must be seen missing before the next put-back, by the put-backs made within the span."""
    n = sum(1 for t in done if now - t < PUT_BACK_SPAN_S)
    return PUT_BACK_WAITS[min(n, len(PUT_BACK_WAITS) - 1)]


def decide(seen, before, done, now, failed=(), fault=False, episode=None, episodes=()):
    """The rule (no I/O). before: (account, wall time) the login was first seen missing, or None. done: wall times of
    the put-backs that landed. failed: wall times of the looks whose writes all failed since the last one landed; the
    next try waits PUT_BACK_WAITS after the last of them, by their count. now: wall seconds. A time past the clock (it
    was set back) starts the wait again, and a put-back dated past it still counts toward the limit. fault: there is
    evidence of the fault (FAULT_SEEN_S); episode and episodes: the current episode of writes and the wall times
    episodes started (load_record). With evidence the episode limits decide how often, and failed writes space the next
    try by FAULT_FAIL_WAITS; without it PUT_BACK_WAITS, PUT_BACK_MAX and the spacing after failed writes do, and, with
    the standing copy on (seen.standing), an item gone with no plain login file beside it waits SIGN_OUT_S at least."""
    if seen.live == UNREADABLE:
        return Verdict('leave', reason="Claude Code's login can't be read right now", missing=before)
    if seen.degraded:
        return Verdict('leave', reason="only a fallback copy of Claude Code's login could be read", missing=before)
    if seen.from_file and seen.login == 'oauth':
        return _decide_promote(seen, episode, episodes, now, before, fault, failed)
    if not is_missing(seen):
        words = {'wiped': 'Claude Code emptied its login after Anthropic refused it',
                 'api_key': 'Claude Code uses an API key', 'unparseable': "Claude Code's login can't be understood",
                 SEALED: 'Claude Code keeps this login in a form Switchboard cannot read'}
        return Verdict('leave', reason=words.get(seen.login, 'Claude Code has a login'))
    if seen.file_login and not _repairable(seen):
        return Verdict('leave', reason="its plain login file still holds a login", key='file')
    if seen.config != PRESENT:
        return Verdict('leave', reason="Claude Code's settings can't be read" if seen.config == UNREADABLE else
                       'Claude Code has no settings file')
    if not seen.named:
        return Verdict('leave', reason="Claude Code's settings name no saved account (a sign-out, or an account "
                                       "Switchboard does not keep)")
    aid = seen.named
    if seen.part_way:
        return Verdict('leave', aid, 'a switch is part-way', watch=True)
    if seen.signing_in:
        return Verdict('leave', aid, 'a sign-in is under way on the page')
    since = before[1] if before and before[0] == aid and before[1] <= now else now
    keep = (aid, since)
    if seen.left_at is not None and since - seen.left_at < SWITCHED_AWAY_S and not seen.owner:
        return Verdict('leave', aid, 'its settings name the account a switch just left', key='switched', missing=keep)
    marked = _marked(seen)
    for key, words in MARKS:
        # the marks describe the saved copy: they do not stop the file's own login going into an item without one
        if marked[key] and not (seen.file_login and seen.owner):
            return Verdict('leave', aid, 'its saved login %s' % words, key=key, missing=keep)
    if fault:
        if not room(episode, episodes, now):
            return Verdict('limit', aid, LIMIT_WORDS, key='limit', missing=keep)
        if now - since < FAULT_AFTER_S:
            return Verdict('wait', aid, 'seen missing; looking again', missing=keep, watch=True)
        if _spaced(failed, now, FAULT_FAIL_WAITS):
            return Verdict('wait', aid, 'the last write did not land; looking again', missing=keep, watch=True)
        return Verdict('put_back', aid, 'its login is missing and its settings still name it', missing=keep, watch=True)
    if sum(1 for t in done if now - t < PUT_BACK_SPAN_S) >= PUT_BACK_MAX:
        return Verdict('limit', aid, LIMIT_WORDS, key='limit', missing=keep)
    signing_out = seen.standing and seen.live == ABSENT and not seen.file_there     # (SIGN_OUT_S)
    if now - since < max(wait_for(done, now), SIGN_OUT_S if signing_out else 0.0):
        return Verdict('wait', aid, 'seen missing; looking again', missing=keep, watch=True)
    fails = [t for t in failed if 0 <= now - t < PUT_BACK_SPAN_S]
    if fails and now - max(fails) < PUT_BACK_WAITS[min(len(fails), len(PUT_BACK_WAITS)) - 1]:
        return Verdict('wait', aid, 'the last write did not land; looking again', missing=keep, watch=True)
    return Verdict('put_back', aid, 'its login is missing and its settings still name it', missing=keep, watch=True)


def _spaced(failed, now, waits):
    """The next try must wait: failed (wall times of looks whose writes all failed, in a row) holds n within the span,
    and the last is less than waits[n - 1] ago (the last wait for any more)."""
    fails = [t for t in failed if 0 <= now - t < PUT_BACK_SPAN_S]
    return bool(fails) and now - max(fails) < waits[min(len(fails), len(waits)) - 1]


def _repairable(seen):
    """An item without a login beside Claude Code's plaintext file whose full account login is proven the named
    account's, as the promote decides: its own ('owner'), or an older generation with a usable saved copy. Claude Code
    is signed out then (a present item wins over the file), and no sign-out leaves this (it removes the file), so it is
    put back as a lost login is, from the file."""
    return seen.live == PRESENT and not seen.from_file and seen.full and \
        (seen.owner or (seen.older and seen.saved == 'ok' and not any(_marked(seen).values())))


def episode_open(episode, now):
    """The episode of writes is still going: its last loss or write was less than EPISODE_QUIET_S ago and it started
    less than EPISODE_MAX_S ago (an episode dated past the clock is over)."""
    if not episode:
        return False
    return 0 <= now - episode['start'] < EPISODE_MAX_S and now - episode['last'] < EPISODE_QUIET_S


def room(episode, episodes, now):
    """One more write is within the limits: fewer than EPISODE_WRITES in the open episode, or, to start a new one,
    fewer than EPISODES_PER_HOUR started in the last hour (one dated past the clock still counts)."""
    if episode_open(episode, now):
        return episode['writes'] < EPISODE_WRITES
    return sum(1 for t in episodes if now - t < PUT_BACK_SPAN_S) < EPISODES_PER_HOUR


def _decide_promote(seen, episode, episodes, now, before=None, fault=False, failed=()):
    """The rule for an account login that sits only in Claude Code's plaintext file (the Keychain said "no such item"):
    Claude Code works from that file, and one more unlucky save removes it too. A chosen sign-out passes through this
    state too, between its removal of the item and its removal of the file, so it is never acted on at the first look:
    the login must be found there by two looks FAULT_AFTER_S apart with proof of the fault (fault), else
    PUT_BACK_AFTER_S apart (SIGN_OUT_S when the file is the standing copy of ours: seen.standing and seen.file_ours),
    the settings naming the same account at both (before: as for decide()); after looks whose
    writes all failed (failed), the next try waits FAULT_FAIL_WAITS (with proof) or PUT_BACK_WAITS by their count. A
    state first seen less than SWITCHED_AWAY_S after a switch away from the account the settings name is left as it is,
    as a missing login is, unless the file's login is proven that account's newest (owner)."""
    if seen.config != PRESENT:
        return Verdict('leave', reason="Claude Code's settings can't be read" if seen.config == UNREADABLE else
                       'Claude Code has no settings file')
    if not seen.named:
        return Verdict('leave', reason="Claude Code's settings name no saved account (a sign-out, or an account "
                                       "Switchboard does not keep)")
    aid = seen.named
    if not seen.full:
        return Verdict('leave', aid, 'the login in its plain login file is incomplete')
    if not seen.owner and not (seen.older and seen.saved == 'ok' and not any(_marked(seen).values())):
        return Verdict('leave', aid, "the login in its plain login file is not proven to be the account's newest",
                       key='file_owner')
    if seen.part_way:
        return Verdict('leave', aid, 'a switch is part-way', watch=True)
    if seen.signing_in:
        return Verdict('leave', aid, 'a sign-in is under way on the page')
    since = before[1] if before and before[0] == aid and before[1] <= now else now
    keep = (aid, since)
    if seen.left_at is not None and not seen.owner:
        if since - seen.left_at < SWITCHED_AWAY_S:
            return Verdict('leave', aid, 'its settings name the account a switch just left', key='switched',
                           missing=keep)
    if not room(episode, episodes, now):
        return Verdict('leave', aid, 'Claude Code keeps moving its sign-in to its plain login file', key='file_limit')
    wait = FAULT_AFTER_S if fault else SIGN_OUT_S if seen.standing and seen.file_ours else PUT_BACK_AFTER_S
    if now - since < wait:
        return Verdict('wait', aid, 'its login sits only in its plain login file; looking again', missing=keep,
                       watch=True)
    if _spaced(failed, now, FAULT_FAIL_WAITS if fault else PUT_BACK_WAITS):
        return Verdict('wait', aid, 'the last write did not land; looking again', missing=keep, watch=True)
    return Verdict(PROMOTE, aid, 'its login sits only in its plain login file', missing=keep, watch=True)


# ---------- reading what the rule needs ----------

def _signing_in(ctx, now):
    """A sign-in on the page is under way: one of its folders is not marked over and is fresh."""
    folder = ctx.path(SIGNINS)
    try:
        names = os.listdir(folder)
    except OSError:
        return False
    for name in names:
        d = os.path.join(folder, name)
        try:
            if not os.path.isdir(d) or os.path.exists(os.path.join(d, SIGNIN_OVER)):
                continue
            newest = max([os.path.getmtime(d)] + [os.path.getmtime(os.path.join(d, n)) for n in os.listdir(d)])
        except OSError:
            return True                                 # can't be told: taken as under way
        if -60 <= now - newest < SIGNIN_FRESH_S:
            return True
    return False


def _file_login(ctx):
    """Claude Code's plaintext login file holds an account login (never written over here), or can't be read."""
    from . import facts
    r, raw = ccfiles.read_bytes(facts.credentials_file(ctx.env, ctx.home))
    if r.kind != PRESENT:
        return r.kind == UNREADABLE
    try:
        return live_mod.parse(raw.decode('utf-8')).kind not in NO_LOGIN
    except UnicodeDecodeError:
        return True


def _file_text(ctx):
    """The text of Claude Code's plaintext login file, or None when it is not there or can't be read as text."""
    r, raw = ccfiles.read_bytes(_cred_path(ctx))
    if r.kind != PRESENT:
        return None
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return None


def _stamp(path):
    """(device, inode, modification time in ns, size) of a file, or None when it is not there (one stat; the file is
    never opened). A rewrite of the file, even with the same bytes, changes it."""
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        return None
    return (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)


file_stamp = _stamp             # for live.write_live


def see(ctx, now, interactive=False, lr=None, saved=None):
    """(Seen, the live LiveRead, the saved Login or None, the account's email or None). While a login is there, this
    is one Keychain read; the rest is read only once the login is missing. lr: a live read already made (the live item
    is not read again). saved: None to read the account's saved login from the Keychain; RECORD to describe it from the
    account's record instead (no Keychain read; when the record holds no fingerprint it is read); or the Read of it
    already made. A login described from the record has no text (_described): nothing is ever written from it."""
    if lr is None:
        lr = live_mod.read_live(ctx, fresh=True, interactive=interactive)
    login = lr.login
    seen = Seen(live=lr.read.kind, degraded=lr.read.degraded, login=_kind(login))
    from_file = lr.read.kind == PRESENT and lr.read.provenance == 'file' and not lr.read.degraded and \
        seen.login == 'oauth'
    if not is_missing(seen) and not from_file:
        return seen, lr, None, None
    from . import facts
    path = facts.credentials_file(ctx.env, ctx.home)
    stamp = _stamp(path)
    file_there = lr.read.provenance == 'file' or stamp is not None
    more = dict(from_file=from_file, full=from_file and login.full_pair, file_there=file_there,
                file_fresh=stamp is not None and abs(now - stamp[2] / 1e9) < FAULT_SEEN_S, standing=mirror_on(ctx),
                file_ours=stamp is not None and stamp in _hold_known(ctx, _hold_note(ctx), path))
    file_login = lr.read.provenance != 'file' and _file_login(ctx)
    cfg_r, block, _ = live_mod.config_identity(ctx)
    try:
        doc = vault.load(ctx)
    except vault.VaultError:
        return Seen(live=seen.live, login=seen.login, file_login=file_login, config=UNREADABLE, **more), lr, None, \
            None
    aid = identity.label_id(doc, block) if cfg_r.kind == PRESENT else None
    last = (doc.get('switches') or [None])[-1]
    left = isinstance(last, dict) and last.get('from') == aid and last.get('to') != aid
    left_at = _num(last.get('at')) if left and aid else None
    if left_at is not None and _signed_in_since(doc['accounts'][aid], last):
        left_at = None                  # a new sign-in of that account since the switch
    base = dict(live=seen.live, login=seen.login, file_login=file_login, config=cfg_r.kind, named=aid,
                part_way=switcher.read_journal(ctx)[0].kind != ABSENT, signing_in=_signing_in(ctx, now),
                left_at=left_at, **more)
    if aid is None:
        return Seen(**base), lr, None, None
    a = doc['accounts'][aid]
    described = _recorded(a) if saved == RECORD else None
    if described is not None:
        vr, saved = Read(PRESENT, provenance='record'), described
    else:
        vr = saved if isinstance(saved, Read) else vault.read_current(ctx, aid, fresh=True, interactive=interactive)
        saved = live_mod.parse(vr.value) if vr.kind == PRESENT else None
    if vr.kind == UNREADABLE:
        state = 'unreadable'
    elif saved is None:
        state = 'missing'
    else:
        state = 'ok' if saved.full_pair else 'incomplete'
    stamps = [x for x in (a.get('rx_ms'), saved.refresh_expires_ms if saved is not None else None) if x]
    fp = saved.fingerprint if saved is not None else None
    from . import tokens
    pending = tokens._UNPERSISTED.get(aid)
    marker = (doc.get('refreshing') or {}).get(aid) or {}
    mirrored = more['standing'] and more['file_ours']     # a standing copy of ours: only its saved login or newer
    if from_file:
        whose = _mirror_whose(login, a, saved, _file_is_theirs(login, doc, block, aid, saved, vr.kind), mirrored)
        base['owner'], base['older'] = whose == 'theirs', whose == 'older'
    elif file_login and seen.live == PRESENT:
        filed = live_mod.parse(_file_text(ctx) or '')
        if filed.kind == 'oauth':
            whose = _mirror_whose(filed, a, saved, _file_is_theirs(filed, doc, block, aid, saved, vr.kind), mirrored)
            base['full'], base['owner'], base['older'] = filed.full_pair, whose == 'theirs', whose == 'older'
    seen = Seen(saved=state, refused=bool(fp) and (a.get('strike') or {}).get('fp') == fp,
                ended=any(x <= now * 1000 for x in stamps), conflict=bool(a.get('conflict')),
                unproven=(a.get('proof') or {}).get('kind') == 'label',
                claimed=bool(vault.claimed(ctx, doc, aid)) or aid in (doc.get('claims') or {}),
                renewing=bool(fp) and (marker.get('fp') == fp or bool(pending and pending[0] == fp)), **base)
    return seen, lr, saved, a.get('email') or aid


def _signed_in_since(a, switch):
    """The account's record a carries a sign-in end stamp (rx_ms) that is not the sign-in the switch's record says it
    left ('left_rx'), by facts.same_sign_in (a renewal keeps the stamp within SAME_SIGN_IN_MS; end stamps are never
    compared exactly): that account was signed in again since. A switch's record without 'left_rx' (an older build's)
    never says so."""
    from . import facts
    rx, left_rx = _num(a.get('rx_ms')), _num(switch.get('left_rx'))
    return bool(rx and left_rx and rx > 0 and left_rx > 0) and not facts.same_sign_in(rx, left_rx)


def _file_is_theirs(login, doc, block, aid, saved, saved_kind):
    """Whose the login in Claude Code's plaintext file is: 'theirs' when it is account aid's by the proof the keep pass
    accepts (its refresh token is the saved one's, or it is the same sign-in by facts.same_sign_in while the settings
    name aid) and not older than the saved copy; 'older' when it is aid's sign-in but an older generation than the
    saved copy (a process wrote what it had read before a renewal: its refresh token is used up); else None. Never a
    login the saved accounts prove to be another account's; never when the saved copy can't be read now."""
    from . import facts
    if saved_kind == UNREADABLE:
        return None
    a = doc['accounts'][aid]
    proof = identity.offline(login, doc, block)
    if proof.account_id not in (None, aid):
        return None
    fps = [x for x in (a.get('fp'), saved.fingerprint if saved is not None else None) if x]
    stamp = (saved.refresh_expires_ms if saved is not None else None) or a.get('rx_ms')
    if not ((login.fingerprint and login.fingerprint in fps) or facts.same_sign_in(login.refresh_expires_ms, stamp)):
        return None
    if saved is not None and live_mod.compare(login, saved) == 'older':
        return 'older'
    return 'theirs'


def _mirror_whose(login, a, saved, whose, mirrored):
    """_file_is_theirs' answer for a plain login file that is the standing copy of ours (mirrored): its own login
    ('theirs') only when it is the saved copy (the same refresh token as the record's or the saved login's) or newer
    than the saved copy by its access end (live.compare). The copy follows the item within a look, and the saved copy
    follows a renewal within a look too; one whose age against the saved copy can't be told is not written into the
    item ('not proven': nothing). An older one stays 'older': the newer saved copy is what goes in. Any other file:
    whose, unchanged."""
    if not mirrored or whose != 'theirs':
        return whose
    fps = [x for x in (a.get('fp'), saved.fingerprint if saved is not None else None) if x]
    if login.fingerprint and login.fingerprint in fps:
        return whose
    return whose if saved is not None and live_mod.compare(login, saved) == 'newer' else None


def _recorded(a):
    """An account's saved login as its record describes it (vault.note_login keeps these with every saved login): the
    fingerprints and the two expiry times, taken as a full login. None when the record holds no fingerprint."""
    def ms(x):
        v = _num(x)
        return int(v) if v is not None and v > 0 else None
    if not isinstance(a.get('fp'), str) or not a['fp']:
        return None
    return live_mod.Login(text='', kind='oauth', fingerprint=a['fp'], access_fp=a.get('access_fp'),
                          expires_ms=ms(a.get('expires_ms')), refresh_expires_ms=ms(a.get('rx_ms')),
                          has_refresh=True, has_access=True)


def _newer_than_record(saved, a, doc, aid):
    """A saved login read from the Keychain that is not the one its record a describes, and still to be written: a full
    login whose access expiry is later than the record's, and that the saved accounts (doc) do not prove to be another
    account's than aid (identity.offline: its fingerprint, or its exact sign-in end, is another account's own). The
    two differ only when Switchboard stopped between saving a login and noting it, and then the Keychain copy is the
    newer one; once it is written the record notes it (_note_newer)."""
    exp = _num(a.get('expires_ms'))
    if not (saved is not None and saved.full_pair and saved.expires_ms is not None and exp is not None and
            saved.expires_ms > exp):
        return False
    return identity.offline(saved, doc).account_id in (None, aid)


def _note_newer(ctx, aid, who, saved, now):
    """After a write of a saved login newer than its record (_newer_than_record): the record notes it, as the step that
    saved it would have (the caller holds the account lock). The keep step does not: the saved copy already equals
    Claude Code's login."""
    try:
        doc = vault.load(ctx)
        a = doc['accounts'].get(aid)
        if a is not None and a.get('fp') != saved.fingerprint:
            vault.note_login(a, saved, None, now)
            vault.save(ctx, doc)
    except (vault.VaultError, ccfiles.CCFileError, OSError) as e:
        ctx.log('The newer saved login of %s was put back but not noted in its record (%s)'
                % (who, e.__class__.__name__))


def _described(saved):
    """The saved login was described from its record (_recorded), not read: a parsed account login never has no
    text."""
    return saved is not None and saved.kind == 'oauth' and not saved.text


# ---------- what is kept between looks ----------

def _num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def load_record(ctx):
    """The shared record: {'missing': (account, wall time) or None, 'missing_how': how that loss was seen (SIGHTED) or
    None, 'done': [wall times the put-backs landed],
    'failed': [wall times of looks whose writes all failed], 'limit_at': when the limit was said or None, 'last':
    {'at', 'account'} or None, 'readable': False when the file is there and can't be used (then nothing is written),
    'why': the reason it can't be used, or None, 'mirror_said': the standing copy's first History line went out}."""
    r, obj = ccfiles.read_json(ctx.path(STATE))
    obj = obj if r.kind == PRESENT else {}
    done, failed = ([t for t in (_num(x) for x in obj.get(k) or []) if t is not None]
                    if isinstance(obj.get(k), list) else [] for k in ('done', 'failed'))
    m = obj.get('missing')
    missing = (m[0], _num(m[1])) if isinstance(m, list) and len(m) == 2 and isinstance(m[0], str) and \
        _num(m[1]) is not None else None
    last = obj.get('last') if isinstance(obj.get('last'), dict) and _num(obj['last'].get('at')) is not None else None
    rec = {'missing': missing, 'done': done, 'failed': failed, 'limit_at': _num(obj.get('limit_at')), 'last': last,
           'readable': r.kind != UNREADABLE, 'why': r.why if r.kind == UNREADABLE else None}
    rec['missing_how'] = obj.get('missing_how') if missing is not None and obj.get('missing_how') in SIGHTED else None
    rec.update(_load_episodes(obj))
    s = obj.get('settings_seen')
    rec['settings_seen'] = s if isinstance(s, str) and len(s) == 16 else None
    rec['failed_said'] = obj.get('failed_said') is True
    rec['mirror_said'] = obj.get('mirror_said') is True
    return rec


def _heal_record(ctx, now):
    """A record that is there but is not a JSON object (a bad restore, a hand edit) holds only times: it is started
    again, said once in the log. A record that can't be read at all is left as it is. True when the record is usable
    now."""
    try:
        with vault.account_lock(ctx, timeout=2.0):
            rec = load_record(ctx)
            if rec['readable'] or rec['why'] != 'unparseable':
                return rec['readable']
            ctx.log("Switchboard's note of earlier put-backs (%s) could not be understood; it starts a new one" % STATE)
            _save_record(ctx, {'missing': None, 'done': [], 'failed': []}, now)
            return True
    except (vault.LockBusy, ccfiles.CCFileError, OSError) as e:
        ctx.log('Could not start a new note of put-backs: %s' % e.__class__.__name__)
        return False


def _load_episodes(obj):
    """The parts of the record about evidence and episodes: 'fault_at' (when proof of the fault was last seen),
    'wrote_at' (when a put-back or a promote last landed), 'episode' ({'start', 'writes', 'last', 'said', 'proof',
    'put_back_said'} or None; said and put_back_said: the promote's and the put-back's History lines were said in it;
    proof: proof of the fault was seen while it was open, or was fresh when it opened) and 'episodes' (wall times
    episodes started)."""
    e = obj.get('episode')
    episode = None
    if isinstance(e, dict) and _num(e.get('start')) is not None and _num(e.get('last')) is not None and \
            _num(e.get('writes')) is not None:
        episode = {'start': _num(e['start']), 'last': _num(e['last']), 'writes': int(_num(e['writes'])),
                   'said': e.get('said') is True, 'proof': e.get('proof') is True,
                   'put_back_said': e.get('put_back_said') is True}
    starts = [t for t in (_num(x) for x in obj.get('episodes') or []) if t is not None] \
        if isinstance(obj.get('episodes'), list) else []
    return {'fault_at': _num(obj.get('fault_at')), 'wrote_at': _num(obj.get('wrote_at')), 'episode': episode,
            'episodes': starts, 'written': _load_written(obj.get('written'))}


def _load_written(w):
    """The note of the last landed write of ours: {'at', 'account', 'how' ('created' or 'over'), 'sha' (SHA-256 of the
    item's text then), 'login_sha' (of its account login, _login_sha, or None), 'file' (the plaintext file's stamp
    right after it, or None), 'settings_size' (the settings file's size in bytes right before it, or None),
    'settings_user' and 'settings_keys' (_settings_mark right before it, or None), 'anew_said' (a settings file made
    anew was said), 'said' (the write's own History line went out)}, or None."""
    if not isinstance(w, dict) or _num(w.get('at')) is None or not isinstance(w.get('account'), str) or \
            w.get('how') not in ('created', 'over') or not isinstance(w.get('sha'), str) or len(w['sha']) != 64:
        return None
    f = w.get('file')
    f = [int(x) for x in f] if isinstance(f, list) and len(f) == 4 and \
        all(isinstance(x, int) and not isinstance(x, bool) for x in f) else None
    ls, size, user, keys = w.get('login_sha'), w.get('settings_size'), w.get('settings_user'), w.get('settings_keys')
    return {'at': _num(w['at']), 'account': w['account'], 'how': w['how'], 'sha': w['sha'],
            'login_sha': ls if isinstance(ls, str) and len(ls) == 64 else None, 'file': f,
            'settings_size': size if isinstance(size, int) and not isinstance(size, bool) and size >= 0 else None,
            'settings_user': user if isinstance(user, str) and len(user) == 16 else None,
            'settings_keys': keys if isinstance(keys, int) and not isinstance(keys, bool) and keys >= 0 else None,
            'anew_said': w.get('anew_said') is True, 'said': w.get('said') is True}


def fault(rec, now):
    """There is evidence of the fault: it was seen less than FAULT_SEEN_S ago (one dated past the clock does not
    count)."""
    t = rec.get('fault_at')
    return t is not None and 0 <= now - t < FAULT_SEEN_S


def _evidence(rec, now):
    """decide()'s keyword arguments about evidence and episodes, from the record."""
    return {'fault': fault(rec, now), 'episode': rec.get('episode'), 'episodes': rec.get('episodes') or []}


def _note_write(rec, now):
    """A put-back or a promote landed: it is one more write of the open episode, or the first of a new one. Returns
    the episode."""
    ep = rec.get('episode')
    if episode_open(ep, now):
        ep = dict(ep, writes=ep['writes'] + 1, last=now)
    else:
        ep = {'start': now, 'writes': 1, 'last': now, 'said': False, 'proof': fault(rec, now), 'put_back_said': False}
        rec['episodes'] = list(rec.get('episodes') or []) + [now]
    rec['episode'] = ep
    rec['wrote_at'] = now
    return ep


def _save_record(ctx, rec, now):
    """Write the record (the caller holds the account lock). Times older than the span are dropped."""
    keep = {'missing': list(rec['missing']) if rec.get('missing') else None,
            'missing_how': rec.get('missing_how') if rec.get('missing') and rec.get('missing_how') in SIGHTED else None,
            'done': [t for t in rec['done'] if now - t < PUT_BACK_SPAN_S][-2 * PUT_BACK_MAX:],
            'failed': [t for t in rec.get('failed') or [] if now - t < PUT_BACK_SPAN_S][-2 * PUT_BACK_MAX:],
            'limit_at': rec.get('limit_at'), 'last': rec.get('last')}
    starts = [t for t in rec.get('episodes') or [] if now - t < PUT_BACK_SPAN_S][-2 * EPISODES_PER_HOUR:]
    w = rec.get('written')
    keep.update({'fault_at': rec.get('fault_at'), 'wrote_at': rec.get('wrote_at'), 'episode': rec.get('episode'),
                 'episodes': starts, 'written': w if w and 0 <= now - w['at'] < TAKE_BACK_S else None,
                 'settings_seen': rec.get('settings_seen'), 'failed_said': bool(rec.get('failed_said')),
                 'mirror_said': bool(rec.get('mirror_said'))})
    os.makedirs(ctx.data_dir, mode=0o700, exist_ok=True)
    ccfiles.atomic_write(ctx.path(STATE), (json.dumps(keep) + '\n').encode('utf-8'), mode=0o600)


def _change_record(ctx, change, now):
    """Read, change and write the record under the account lock. False when it could not be done now."""
    try:
        with vault.account_lock(ctx, timeout=2.0):
            rec = load_record(ctx)
            if not rec['readable']:
                return False
            change(rec)
            _save_record(ctx, rec, now)
            return True
    except (vault.LockBusy, ccfiles.CCFileError, OSError) as e:
        ctx.log('Could not note a look for a lost sign-in: %s' % e.__class__.__name__)
        return False


def last_put_back(ctx):
    """{'at', 'account'} of the last put-back, or None (for doctor)."""
    return load_record(ctx)['last']


class _Said:
    """What this process has said in the current episode of a missing login (said once each)."""

    def __init__(self):
        self.keys = set()


_SAID = {}
_SAID_LOCK = threading.Lock()
_SETTINGS = {}                  # data folder -> who the settings named at this process's last look (_settings_seen)
# data folder -> (account, fingerprint, text, ctx.mono()) of the saved login a look of this process read when it
# decided to wait (_keep_saved). In this process only: never written anywhere, logged or returned.
_KEPT_SAVED = {}
_STRAIGHT_OFF = {}              # data folder -> ctx.mono() of this process's last unanswered read under the save lock


def _said(ctx):
    with _SAID_LOCK:
        return _SAID.setdefault(ctx.data_dir, _Said())


def reset(ctx=None):
    """Forget what this process has said (a new process starts with nothing) and the saved login it kept
    (_KEPT_SAVED). Without ctx, also who the settings named at this process's last look (_SETTINGS) and what the
    sign-in hold knows (_HOLDER, and the spare's _HOLD_LATE, _HOLD_ABSENT, _HOLD_DID: with ctx they are kept, since a
    look that finds the login there must not end the hold's few seconds, nor a late removal still owed an answer)."""
    with _SAID_LOCK:
        if ctx is None:
            _SAID.clear()
            _SETTINGS.clear()
            _KEPT_SAVED.clear()
            _STRAIGHT_OFF.clear()
        else:
            _SAID.pop(ctx.data_dir, None)
            _KEPT_SAVED.pop(ctx.data_dir, None)
            _STRAIGHT_OFF.pop(ctx.data_dir, None)
    if ctx is None:
        with _HOLD_LOCK:
            _HOLDER.clear()
            _HOLD_FAILED.clear()
            _HOLD_SETTING.clear()
            _MIRROR_SETTING.clear()
            _NOT_ADOPTED.clear()
            _REMADE.clear()
            _HOLD_OWN.clear()
            _HOLD_GAVE.clear()
            _HOLD_LATE.clear()
            _HOLD_ABSENT.clear()
            _HOLD_DID.clear()


def _keep_saved(ctx, aid, saved):
    """Keep the saved login of aid that a look read when it decided to wait (a parsed Login, or None when it was not
    there or could not be read: then nothing is kept), for the look that writes (_saved_kept)."""
    with _SAID_LOCK:
        if aid is not None and saved is not None and saved.text and saved.fingerprint:
            _KEPT_SAVED[ctx.data_dir] = (aid, saved.fingerprint, saved.text, ctx.mono())
        else:
            _KEPT_SAVED.pop(ctx.data_dir, None)


def _saved_kept(ctx, aid, fp):
    """The saved login of aid as an earlier look of this process read it (_keep_saved), as a Read: only when it is at
    most SIGHTING_STALE_S old and fp, the fingerprint the account's record carries now (read under the account lock),
    is its own; else None, and it is read from the Keychain. The keep step and every renewal change the record's
    fingerprint, so an older generation is never taken from here."""
    with _SAID_LOCK:
        k = _KEPT_SAVED.get(ctx.data_dir)
    if k is None or k[0] != aid or not fp or k[1] != fp or not 0 <= ctx.mono() - k[3] <= SIGHTING_STALE_S:
        return None
    return Read(PRESENT, value=k[2], provenance='keychain')


def _once(ctx, key):
    s = _said(ctx)
    if key in s.keys:
        return False
    s.keys.add(key)
    return True


def _left_line(v, who, from_file=False):
    """What is said once when the rule leaves Claude Code's login as it is. from_file: the login sits only in Claude
    Code's plaintext file (not lost: Claude Code works from it)."""
    if v.key == 'file':
        return ("Claude Code lost its sign-in, and its plain login file still holds a login; Switchboard left it as "
                "it is")
    if v.key == 'switched' and from_file:
        return ("Claude Code keeps its sign-in only in its plain login file, and its settings name %s, the account "
                "Switchboard just switched away from; Switchboard left it as it is" % who)
    if v.key == 'switched':
        return ("Claude Code lost its sign-in, and its settings name %s, the account Switchboard just switched away "
                "from; Switchboard did not put it back" % who)
    if v.key == 'file_owner':
        return ("Claude Code moved its sign-in to its plain login file, and Switchboard can't tell that it is the "
                "newest login of %s; it left the file as it is" % who)
    if v.key == 'file_limit':
        return ("Claude Code keeps moving its sign-in to its plain login file. Switchboard has put it back in the "
                "Keychain often in a short time, so it leaves it in the file for now (Claude Code works from it)")
    if v.key == 'record':
        return ("Claude Code lost its sign-in, and Switchboard's note of earlier put-backs (%s) can't be read; it did "
                "not put %s back" % (STATE, who))
    return ('Claude Code lost its sign-in, and the saved login of %s %s; Switchboard did not put it back'
            % (who, dict(MARKS).get(v.key, v.reason)))


# ---------- one look ----------

def look(ctx, hint=None):
    """One look for a lost sign-in, and a put-back (or a promote of the login in Claude Code's plaintext file) when the
    rule says so. hint: what the sign-in watch saw change ({'keychain', 'login_file', 'settings', 'tick',
    'login_file_written'}), or None; a write of the login file it saw is proof of the fault (a hint without
    login_file_written: login_file 'appeared' or 'changed'). Returns api.PutBack, with next_s and seen."""
    out = {}
    res = _look(ctx, hint, out)
    vault.keep_shared(ctx, out.get('lr'))          # the Mac-wide parts of an item read whole with a login, when new
    return _with_facts(ctx, res, out)


def _look(ctx, hint, out):
    """look() without next_s and seen; out gets what was read (seen, lr, saved, now, fault) and what was decided (aid,
    key)."""
    if capture.in_runas_env(ctx):
        return PutBack('leave', reason='inside a `switchboard run` session')
    now = ctx.wall()
    alone = _settings_alone(ctx, hint, now)
    if alone is not None:
        return alone
    went = _straight(ctx, hint, now, out)
    if isinstance(went, PutBack):
        return went
    went = _late(ctx, now, out, went)               # a removal of the plain login file the hold could not answer
    seen, lr, saved, email = see(ctx, now, saved=RECORD, lr=went)     # went: None, or the read under the save lock
    # a file the sign-in hold put there, when the look takes it away: read again without it. Beside an item without a
    # login it stays while it is what that item will be put back from (the newest login there is)
    if hold_tidy(ctx, lr=lr, repair=is_missing(seen) and bool(seen.file_login) and _repairable(seen)).get('did') == \
            'removed':
        seen, lr, saved, email = see(ctx, now, saved=RECORD)
    _follow(ctx, lr, out)                           # a file of ours that stays holds the login now in the item
    _mirror(ctx, lr, out)                           # the standing copy, when there is no file (mirror_on)
    out.update(seen=seen, lr=lr, saved=saved, now=now)
    if seen.login == SEALED and seen.live == PRESENT and not seen.degraded:
        line = None
        if _once(ctx, seen.login):
            line = SEALED_LINE
            ctx.log(line)
        out['key'] = seen.login
        return PutBack('leave', reason=SEALED_LINE, line=line)
    rec = load_record(ctx)
    if not rec['readable'] and rec['why'] == 'unparseable' and _heal_record(ctx, now):
        rec = load_record(ctx)
    out['rec'] = rec
    present = not is_missing(seen) and seen.live != UNREADABLE and not seen.degraded and not seen.from_file
    if present:
        reset(ctx)                                      # the login is there: an episode is over
    if not rec['readable']:
        if present:
            return PutBack('leave', reason='Claude Code has a login')
        v = decide(seen, None, [], now)                 # the rule still decides; only a write needs the record
        if v.action in ('put_back', 'wait', PROMOTE) and _described(saved):
            seen, lr, saved, email = see(ctx, now, lr=lr)       # nothing is written: the saved login's marks are told
            out.update(seen=seen, saved=saved)
            v = decide(seen, None, [], now)
        if v.action in ('put_back', 'wait', PROMOTE):
            v = Verdict('leave', v.account, 'the record of earlier put-backs cannot be read', key='record')
    else:
        marks = _sightings(seen, hint, now, *_file_now(ctx, lr, hint))
        changed = marks(rec)
        out['fault'] = fault(rec, now)
        v = decide(seen, rec['missing'], rec['done'], now, rec['failed'], **_evidence(rec, now))
        if v.action in ('wait', 'limit') and _described(saved):
            # a look that writes nothing reads the saved login, so its marks are told at the first sighting; the look
            # that writes takes it from here (_saved_kept)
            seen, lr, saved, email = see(ctx, now, lr=lr)
            out.update(seen=seen, saved=saved)
            _keep_saved(ctx, seen.named, saved)
            v = decide(seen, rec['missing'], rec['done'], now, rec['failed'], **_evidence(rec, now))
        # the login there again, or a verdict from a readable look that names no account (a sign-out, settings that
        # name nobody), ends an episode: its failed writes no longer space the next one, and its failed line is said
        # again in the next
        clear = present or (v.account is None and seen.live != UNREADABLE and not seen.degraded)
        how = (_how(seen) or rec['missing_how']) if v.missing else None     # an unreadable look keeps the last one
        if v.missing != rec['missing'] or changed or (clear and (rec['failed'] or rec['failed_said'])) or \
                how != rec['missing_how']:
            _change_record(ctx, lambda r: (marks(r), r.update(missing=v.missing, missing_how=how,
                                                              failed=[] if clear else r['failed'],
                                                              failed_said=False if clear else r['failed_said'])), now)
    out.update(aid=v.account, key=v.key)
    taken = _maybe_take_back(ctx, rec, lr, now)
    if taken is None:
        taken = _maybe_take_out(ctx, lr, now)
    if taken is not None:
        return taken
    who = email or v.account
    if v.action == 'limit':
        if rec['limit_at'] is not None and 0 <= now - rec['limit_at'] < PUT_BACK_SPAN_S:
            return PutBack('limit', account=who, reason=v.reason)
        if not _change_record(ctx, lambda r: r.update(limit_at=now), now) and not _once(ctx, 'limit'):
            return PutBack('limit', account=who, reason=v.reason)
        ctx.say(LIMIT_WORDS, 'warn', h='problem', account=who, by='auto')
        return PutBack('limit', account=who, reason=v.reason, line=LIMIT_WORDS)
    if v.action == 'leave' and v.key:
        return _left(ctx, v, who, seen.from_file)
    if v.action not in ('put_back', PROMOTE):
        return PutBack(v.action, account=who, reason=v.reason, watch=v.watch)
    res = (_promote if v.action == PROMOTE else _put_back)(ctx, v.account, who, now, out)
    if res.key is not None:                             # decided again under the locks with the saved login read
        out['key'] = res.key
    return res


def _settings_seen(ctx):
    """Who Claude Code's settings name, as a look notes it: 16 hex of a SHA-256 of the settings' read kind, account id
    and organization id (no email or id is kept). Parsed only when the file changed (config_identity's cache)."""
    cfg_r, block, _ = live_mod.config_identity(ctx, cached=True)
    uuid, _, org = live_mod.label(block)
    return _sha('%s\n%s\n%s' % (cfg_r.kind, uuid, org))[:16]


def _settings_alone(ctx, hint, now):
    """Every look notes who the settings name (in this process, and in the record when it changed). A look the sign-in
    watch asks for because Claude Code's settings file changed and nothing else did answers 'leave' with no Keychain
    read when the settings name the same account and organization as at the last look (this process's, else the
    record's), no loss is on record and no write of ours landed less than TAKE_BACK_S ago (it may still have to be
    taken back): every save of the item replaces the Keychain file, so with the item unchanged the look would find
    what the last one found. None: look as usual."""
    named = _settings_seen(ctx)
    rec = load_record(ctx)
    with _SAID_LOCK:
        last = _SETTINGS.get(ctx.data_dir)
        _SETTINGS[ctx.data_dir] = named
    if last is None:
        last = rec.get('settings_seen')
    if rec['readable'] and rec.get('settings_seen') != named:
        _change_record(ctx, lambda r: r.update(settings_seen=named), now)
    alone = isinstance(hint, dict) and bool(hint.get('settings')) and \
        not any(hint.get(k) for k in ('keychain', 'login_file', 'tick', 'login_file_written'))
    recent = [t for t in (rec.get('wrote_at'), (rec.get('written') or {}).get('at'))
              if t is not None and now - t < TAKE_BACK_S]      # a write dated past the clock counts as recent
    if alone and rec['readable'] and last == named and rec['missing'] is None and not recent:
        return PutBack('leave', reason="Claude Code's settings changed and name the same account as before")
    return None


def _left(ctx, v, who, from_file=False):
    """The answer for a verdict that leaves the login as it is with something to say: said once (_left_line)."""
    line = None
    if _once(ctx, v.key):
        line = _left_line(v, who, from_file)
        ctx.log(line)
    return PutBack('leave', account=who, reason=v.reason, line=line, watch=v.watch, key=v.key)


def _how(seen):
    """How a look saw the loss (SIGHTED), or None when it saw none."""
    if seen.from_file and seen.login == 'oauth':
        return 'file'
    if not is_missing(seen):
        return None
    return 'beside' if seen.file_login else 'item'


def _presumed(ctx, how):
    """What a look that follows a sighting takes for Claude Code's login until its read under Claude Code's save lock
    (_straight), with no Keychain call: for 'file', the plaintext login file's own text, as read_live gives it when the
    Keychain says "no such item" (None when the file holds no account login now); for 'item' and 'beside', an item
    without a login (see() reads whether the plaintext file beside it holds one). Nothing is ever written from it: the
    write is decided again from the read under the save lock."""
    from . import facts
    service = facts.keychain_service(ctx.env)
    if how != 'file':
        return live_mod.LiveRead(Read(PRESENT, value='', provenance='keychain'), live_mod.parse(''), service)
    text = _file_text(ctx)
    login = live_mod.parse(text) if text is not None and text.strip() else None
    if login is None or login.kind != 'oauth':
        return None
    return live_mod.LiveRead(Read(PRESENT, value=text, provenance='file'), login, service)


def _straight(ctx, hint, now, out):
    """A look that follows a sighting of the loss goes straight to the locks, with no read of Claude Code's item outside
    them (SIGHTING_STALE_S). Only when the record holds a sighting at most SIGHTING_STALE_S old with how it was seen,
    the account the settings name has a fingerprint in its record (no saved login is read here), and the rule, on the
    state the sighting was of (_presumed) with everything else read as any look reads it, says to write now: the
    settings naming that account, the wait, the spacing after failed looks, the limits, a switch part-way, a sign-in
    under way, the switched-away rule and the record's marks are all judged by decide(), after this look's proof of the
    fault is noted (the plaintext file's stamp against our last write's and the last switch's). Then the engine's locks,
    Claude Code's save lock, and there the live read, the whole decision again from it, the write and the read-back
    (_put_back, _promote). Returns the look's answer (api.PutBack) once a write was tried or a lock was busy; else, with
    nothing written and nothing said, what the look goes on from as any look does: the live read made under the save
    lock, or None (none was made: the look reads outside the locks). When that read under the save lock got no answer,
    this process's looks read outside the locks first for STRAIGHT_OFF_S: Claude Code's own lock is not held again
    for a read that may get none."""
    rec = load_record(ctx)
    sighted, how = rec['missing'], rec['missing_how']
    if sighted is None or how is None or not 0 <= now - sighted[1] <= SIGHTING_STALE_S:
        return None
    with _SAID_LOCK:
        off = _STRAIGHT_OFF.get(ctx.data_dir)
    if off is not None and 0 <= ctx.mono() - off < STRAIGHT_OFF_S:
        return None                                     # a read under the save lock got no answer a moment ago
    lr = _presumed(ctx, how)
    cfg_r, block, _ = live_mod.config_identity(ctx)
    try:
        doc = vault.load(ctx)
    except vault.VaultError:
        return None
    aid = identity.label_id(doc, block) if cfg_r.kind == PRESENT else None
    if lr is None or aid is None or _recorded(doc['accounts'][aid]) is None:
        return None                                     # see() would read the saved login here
    seen, _, saved, email = see(ctx, now, lr=lr, saved=RECORD)
    trial = dict(rec)
    marks = _sightings(seen, hint, now, *_file_now(ctx, None, hint))
    changed = marks(trial)                              # this look's proof is noted before the wait is judged
    v = decide(seen, trial['missing'], trial['done'], now, trial['failed'], **_evidence(trial, now))
    if v.action != (PROMOTE if how == 'file' else 'put_back'):
        return None
    if changed:
        _change_record(ctx, marks, now)
    sub = {'lr': lr, 'straight': {}}
    res = (_promote if how == 'file' else _put_back)(ctx, v.account, email or v.account, now, sub)
    cur = sub['straight'].get('read')
    if res is None:
        if cur is not None and (cur.read.kind == UNREADABLE or cur.read.degraded):
            with _SAID_LOCK:                            # the save lock was held for a read that got no answer
                _STRAIGHT_OFF[ctx.data_dir] = ctx.mono()
        return cur
    from dataclasses import replace
    told = replace(sub.get('seen', seen), live=cur.read.kind, degraded=cur.read.degraded, login=_kind(cur.login)) \
        if cur is not None else None                    # what is told of the look is what its own read found
    out.update(seen=told, lr=cur, saved=sub.get('saved', saved), now=now, rec=trial, fault=fault(trial, now),
               aid=v.account, key=res.key if res.key is not None else v.key)
    return res


def _sightings(seen, hint, now, file_now=None, file_is_item=False, switch_file=None, hold_file=None):
    """A change for the record from what this look saw: proof of the fault (the watch saw the plaintext login file
    written since its last look; this look found it written less than FAULT_SEEN_S ago; the login is missing again
    less than FAULT_SEEN_S after a write of ours, inside an episode that has seen proof), and the loss as the open
    episode's latest. Proof is noted at most once a second, and marks the open episode. A rewrite of the file by a
    write of ours is not proof: file_now is the file's stamp at this look (None: not there), the same as the one our
    last write left (or the file gone since a write of ours less than FAULT_SEEN_S ago), or as the one the last
    switch's record keeps (switch_file: a switch rewrites an existing file); hold_file: the stamp of the file the
    sign-in hold put back (bytes Claude Code wrote a moment before, not a new write of them): it is never proof by its
    age, and counts as no file for the rule above (the sign-in watch never reports the hold's own link as a write, so
    a write it reported is still one); file_is_item: the file holds the item's very bytes (one writer wrote both: a
    switch, or a put-back). Returns change(rec) -> True when it changed rec."""
    missing = seen.named is not None and seen.live != UNREADABLE and not seen.degraded and \
        (is_missing(seen) or seen.from_file)
    written = isinstance(hint, dict) and bool(
        hint.get('login_file_written') if 'login_file_written' in hint else
        hint.get('login_file') in ('appeared', 'changed'))

    def change(rec):
        wrote = rec.get('wrote_at')
        recent = wrote is not None and 0 <= now - wrote < FAULT_SEEN_S
        left = (rec.get('written') or {}).get('file')
        left = tuple(left) if left else None
        ours = (left is not None and file_now == left) or (switch_file is not None and file_now == switch_file)
        held = hold_file is not None and file_now == hold_file      # the hold's file: as if none were there
        by_us = file_is_item or ours or (left is not None and (file_now is None or held) and recent)
        ep = rec.get('episode')
        proof = (written and not by_us) or (seen.file_fresh and not ours and not held)
        undone = missing and recent and episode_open(ep, now) and ep.get('proof') is True
        changed = False
        old = rec.get('fault_at')
        if (proof or undone) and (old is None or not 0 <= now - old < 1.0):
            rec['fault_at'] = now
            changed = True
        if proof and episode_open(ep, now) and ep.get('proof') is not True:
            ep = rec['episode'] = dict(ep, proof=True)
            changed = True
        if missing and episode_open(ep, now) and not 0 <= now - ep['last'] < 1.0:
            rec['episode'] = dict(ep, last=now)
            changed = True
        return changed
    return change


def _file_now(ctx, lr, hint):
    """(the plaintext login file's stamp now, whether it holds the item's very bytes, the stamp the last switch's
    record keeps for it or None, its own stamp when the sign-in hold put it there or None) for _sightings. The bytes
    are compared only when the watch says the file was written and the item holds a login (one read of a small file);
    the switch's record and the hold's note are read only when the file was written or is fresh (the look right after
    a switch can come while a login is there, so it is not taken from see())."""
    from . import facts
    path = facts.credentials_file(ctx.env, ctx.home)
    stamp = _stamp(path)
    same = False
    written = isinstance(hint, dict) and (hint.get('login_file_written') or
                                          hint.get('login_file') in ('appeared', 'changed'))
    if written and stamp is not None and lr is not None and lr.usable and lr.read.provenance == 'keychain':
        r, raw = ccfiles.read_bytes(path)
        same = r.kind == PRESENT and raw == (lr.read.value or '').encode('utf-8')
    switched = held = None
    if stamp is not None and (written or abs(ctx.wall() - stamp[2] / 1e9) < FAULT_SEEN_S):
        switched = _switch_file(ctx)
        held = stamp if stamp in _hold_known(ctx, _hold_note(ctx), path) else None
    return stamp, same, switched, held


def _switch_file(ctx):
    """The plaintext login file's stamp right after the last switch's write, as its record in the accounts file keeps
    it ({'file': [device, inode, mtime ns, size]}, _stamp's form), or None (no file then, or a record without it)."""
    try:
        doc = vault.load(ctx)
    except vault.VaultError:
        return None
    last = (doc.get('switches') or [None])[-1]
    f = last.get('file') if isinstance(last, dict) else None
    if isinstance(f, list) and len(f) == 4 and all(isinstance(x, int) and not isinstance(x, bool) for x in f):
        return tuple(f)
    return None


def _with_facts(ctx, res, out):
    """res with next_s (when the caller should look again), seen (plain facts for the sign-in watch's record: words,
    booleans and 16-hex fingerprints, never a token, an email or an account id), and the account id and the rule's
    key, for the loop (they never reach the record)."""
    from dataclasses import replace
    from . import facts
    seen, lr, saved = out.get('seen'), out.get('lr'), out.get('saved')
    now = out.get('now', ctx.wall())
    wrote = (out.get('rec') or {}).get('wrote_at')
    if res.action in ('wait', 'failed'):
        next_s = FAULT_AFTER_S if out.get('fault') else PUT_BACK_AFTER_S
    elif res.action in ('put_back', PROMOTE):
        next_s = AFTER_WRITE_NEXT_S
    elif wrote is not None and 0 <= now - wrote:
        next_s = _after_write(now - wrote)
    else:
        next_s = None
    again = out.get('again')                        # a step with the plain login file found the save lock held
    if again is not None:
        next_s = again if next_s is None else min(next_s, again)
    login = lr.login if lr is not None else None
    plain = {'live': seen.live if seen is not None else None,
             'login': seen.login if seen is not None else None,
             'source': lr.read.provenance if lr is not None and lr.read.kind == PRESENT else None,
             'login_file': os.path.lexists(facts.credentials_file(ctx.env, ctx.home)),
             'named': bool(seen is not None and seen.named),
             'saved': seen.saved if seen is not None else None,
             'action': res.action,
             'fault': bool(out.get('fault')),
             'live_fp': login.fingerprint if login is not None else None,
             'saved_fp': saved.fingerprint if saved is not None else None}
    return replace(res, next_s=next_s, seen=plain, account_id=out.get('aid'), key=out.get('key'))


def _after_write(elapsed):
    """Seconds until the next of AFTER_WRITE_LOOKS for a write of ours made `elapsed` s ago, or None past the last. A
    point less than a quarter of a second away is skipped: the look that just ran stands for it."""
    for at in AFTER_WRITE_LOOKS:
        if at - elapsed >= 0.25:
            return at - elapsed
    return None


def watch_look(ctx, hint=None):
    """The sign-in watch's look (and the page's): look(); then, when Claude Code holds an account login that is not
    the saved copy of the account its settings name (Claude Code renewed it), the keep pass at once, so the saved copy
    is the newest generation within a second of a renewal and a later put-back never writes an older one. The keep
    pass is asked once per login while its answer stands (_keep_after). Returns api.PutBack; action 'kept' when the keep
    pass saved something."""
    from dataclasses import replace
    out = {}
    res = _with_facts(ctx, _look(ctx, hint, out), out)          # look(), keeping the live read for the keep step
    vault.keep_shared(ctx, out.get('lr'))          # the Mac-wide parts of an item read whole with a login, when new
    plain = dict(res.seen or {})
    if res.action == PROMOTE or plain.get('login') != 'oauth' or plain.get('source') not in ('keychain', 'file') or \
            not plain.get('live_fp'):
        return res
    cfg_r, block, _ = live_mod.config_identity(ctx)
    try:
        doc = vault.load(ctx)
    except vault.VaultError:
        return res
    aid = identity.label_id(doc, block) if cfg_r.kind == PRESENT else None
    if aid is None:
        return res
    plain['named'] = True
    saved_fp = doc['accounts'][aid].get('fp')
    plain['saved_fp'] = saved_fp
    if saved_fp == plain['live_fp']:
        return replace(res, seen=plain)
    lr = out.get('lr')
    note = _keep_after(ctx, aid, plain['live_fp'], lr.login if lr is not None else None)
    if not note:
        return replace(res, seen=plain)
    plain['action'] = 'kept'
    again = _after_keep(ctx, out)                   # what waited for this login's proof, at once
    next_s = res.next_s if again is None else (again if res.next_s is None else min(res.next_s, again))
    return replace(res, action='kept', reason=note, seen=plain, next_s=next_s)


def _tries(ctx, doc, aid, saved, note=None, filed=None, check=None, straight=None):
    """Under the locks: write the saved login until one write reads back, at most TRIES times within TRIES_WITHIN_S,
    each after a fresh read (interactive, so an item resting after a refused write is still read). Each try's read,
    write and read-back are made under Claude Code's save lock on the item, let go between tries. ('landed', None),
    ('present' or 'changed', words) when there is nothing to write any more, ('taken_back', words) when the write met
    a sign-out and was undone (_undo), ('busy', words) when Claude Code kept its save lock past the budget (nothing
    was tried: not a failed write), or ('failed', words). note (a dict) gets
    the landed write's facts (_written_note). filed: the text of Claude Code's plaintext file when its login goes in
    instead (an item without a login beside it; _try_once). check: the rule again on each try's fresh read (_again).
    straight: a dict on the straight path (_straight) that gets each try's read ('read') and whether a write was tried
    ('wrote')."""
    start = ctx.mono()
    why = None
    shared = {}                                     # the record of the Mac-wide parts, read once when it is needed
    for n in range(TRIES):
        try:
            with live_mod.storage_lock(ctx) as held:
                _lock_busy(ctx, False)
                outcome, why = _try_once(ctx, doc, aid, saved, held, note, filed, check, straight, shared)
        except ccfiles.LockBusy:
            _lock_busy(ctx, True)
            return 'busy', 'Claude Code was saving its login'
        if outcome is not None and outcome != 'again':
            return outcome, why
        if n + 1 >= TRIES or ctx.mono() - start >= TRIES_WITHIN_S:
            break
        if outcome is None:
            ctx.sleep(_RAND.uniform(*TRY_GAP_S))
    return 'failed', why


BUSY_LOOKS = 3                  # looks in a row that found Claude Code's save lock held before that is said, once
_BUSY = {}                      # data folder -> looks in a row that found it held (this process)


def _lock_busy(ctx, busy):
    """Count the looks in a row that found Claude Code's save lock held past its budget. At BUSY_LOOKS, say once in
    the log how long ago its holder made it: a Claude Code process that stopped part-way through a save keeps it until
    its 15 s stale time, and one that keeps touching it holds it for as long as it lives."""
    with _SAID_LOCK:
        n = _BUSY.get(ctx.data_dir, 0) + 1 if busy else 0
        _BUSY[ctx.data_dir] = n
    if n != BUSY_LOOKS:
        return
    from . import facts
    path = facts.storage_lock(ctx.env, ctx.home)
    try:
        st = os.lstat(path)
        age = '%.0f s ago' % max(0.0, time.time() - getattr(st, 'st_birthtime', st.st_mtime))
    except OSError:
        age = 'at a time not known'
    home = os.path.join(ctx.home, '')               # the home folder is said as ~: a log line names no account
    shown = '~/' + path[len(home):] if path.startswith(home) else path
    ctx.log("Claude Code's save lock (%s) was held at %d looks in a row; its holder made it %s. A Claude Code process "
            "may be stuck in a save" % (shown, BUSY_LOOKS, age))


def _try_once(ctx, doc, aid, saved, held, note=None, filed=None, check=None, straight=None, shared=None):
    """One try, holding Claude Code's save lock (held): a fresh read, the decision, the write, the read-back. When
    Claude Code's answer was "no such item", the write only creates the item: of two writers at that moment only one
    lands, and one that finds the item made first reads and decides again at once ('again'). filed: the text of
    Claude Code's plaintext file beside an item without a login; while the item still holds no login and the file
    still holds exactly that text, its login (or the newer saved copy, when the file's is an older generation) is
    written over the item with the item's machine-wide parts. check(cur): the rule on this read must still say
    'put_back' for aid (_again). straight: see _tries. shared: a dict that keeps the record of the Mac-wide parts
    between tries (_mac_parts). (outcome, words); outcome None: the write did not land, try again after a gap.

    With no live object at all (the Keychain says "no such item" and no plain login file holds an object), the saved
    login is written whole with the Mac-wide parts Switchboard last saw in Claude Code's item (vault.keep_shared), as
    a whole saved login brought them back before saved logins kept only their account's parts; the record is read here
    only then, and never over an item or a file that is there.

    With the sign-in hold's setting on, a write that lands leaves a plain login file holding exactly the text written
    into the item, for the processes that were told "no such item" (the spare, at the end of this file). With no file
    there, one is made just before the write (_spare_make), so a process that asks while the write runs finds a login;
    a file that is there (Claude Code's, or one of ours) is kept as the promote keeps its file (live.write_live,
    keep_file). Once the write has read back, the file is handed to the hold (hold_adopt), which takes it away by its
    rules. A write that does not land, or is taken back, takes away the file it made before the save lock is let go.
    With the setting off, Claude Code's file beside the item is removed once its login is in the item, when it is
    still the very file the write left."""
    cur = live_mod.read_live(ctx, fresh=True, interactive=True)
    if straight is not None:
        straight['read'] = cur
    if cur.read.kind == UNREADABLE or cur.read.degraded:
        return 'failed', "Claude Code's login can't be read (%s)" % (cur.read.why or 'a fallback copy')
    if not is_missing(Seen(live=cur.read.kind, login=_kind(cur.login))):
        return 'present', 'a login is there now'
    cfg_r, block, cfg_path = live_mod.config_identity(ctx)
    if cfg_r.kind != PRESENT or identity.label_id(doc, block) != aid:
        return 'changed', "Claude Code's settings changed"
    size = _settings_size(cfg_path)                 # the lived-in settings file, for a take-back (bound 5)
    user = _settings_mark(cfg_path)
    source = saved.blob
    if filed is not None:
        if cur.read.kind != PRESENT or cur.read.provenance != 'keychain':
            return 'changed', 'its Keychain item changed'
        if _file_text(ctx) != filed:
            return 'changed', 'its plain login file changed'
        login = live_mod.parse(filed)
        whose = _file_is_theirs(login, doc, block, aid, saved, PRESENT) if login.full_pair else None
        if whose not in ('theirs', 'older') or (whose == 'older' and _described(saved)):
            return 'changed', "the login in its plain login file is not proven to be the account's newest"
        source = login.blob if whose == 'theirs' else saved.blob
        if note is not None:
            note['source'] = 'file' if whose == 'theirs' else 'saved'
    if check is not None:
        v = check(cur)
        if v.action != 'put_back' or v.account != aid:
            return 'changed', v.reason
    if source is None:
        return 'changed', 'the saved login was not read'
    # no live object at all: the saved copy is written whole, as a switch does (switcher._critical), with the record of
    # the Mac-wide parts (a saved copy keeps only its account's own); an object without a login keeps its machine-wide
    # parts as they are now
    blob = cur.login.blob if cur.login is not None and isinstance(cur.login.blob, dict) else None
    whole = live_mod.compose(source, blob)
    if blob is None and filed is None:
        whole = dict(whole, **_mac_parts(ctx, cur, shared))
    composed = live_mod.dump(whole)
    absent = cur.read.kind == ABSENT or cur.read.provenance == 'file'     # the Keychain said "no such item"
    if straight is not None:
        straight['wrote'] = True
    keep = hold_on(ctx)
    spare = _spare_make(ctx, composed) if keep else None    # None when a file is there (or none could be made)
    landed = False
    try:
        wrote = {}
        done = live_mod.write_live(ctx, composed, interactive=True, create_only=absent, held=held, wrote=wrote,
                                   keep_file=keep)
        left = wrote.get('file')                    # the plaintext file this write left, by its own stamp
        if not done.ok and done.why == 'exists':
            return 'again', 'another process made the item first'
        back = live_mod.read_live(ctx, fresh=True, interactive=True)
        # the item's own answer: with "no such item" the read falls through to the plain login file, which may be the
        # spare made above, holding the very text written
        if not (back.usable and back.read.provenance == 'keychain' and back.login is not None and
                back.login.text == composed):
            return None, ('Keychain: %s' % done.why) if not done.ok else 'it did not read back'
        if _undo(ctx, held, 'created' if absent else 'over', _sha(back.read.value),
                 None if absent else cur.read.value, _login_sha(back.login), size, user) == 'taken':
            return 'taken_back', 'Claude Code was signing out'
        landed = True
    finally:
        if not landed:
            _spare_drop(ctx, spare)                 # still inside the same hold of the save lock
    path = _cred_path(ctx)
    allowed = (composed.encode('utf-8'),) + ((filed.encode('utf-8'),) if filed is not None else ())
    if keep:
        # handed to the hold, which takes it away by its rules; a file the note can't be written for goes now,
        # unless it is the spare, which its own note already names
        if left is not None and not hold_adopt(ctx, path, left) and spare != left and \
                _remove_if_same(ctx, path, left, allowed):
            left = None
        elif spare is not None and spare == left:
            _spare_said(ctx, 'spare', 'put_back')
    elif filed is not None and _remove_if_same(ctx, path, left, allowed):
        left = None
    _written_note(note, absent, back, left, size, user)
    return 'landed', None


def _mac_parts(ctx, cur, shared=None):
    """The Mac-wide parts a put-back into no live object at all adds to the saved login: the record vault.keep_shared
    keeps for Claude Code's item (cur.service), read at most once per put-back (shared: the dict _tries keeps). {} when
    there is none or it can't be read: the saved login is then written as it is."""
    from . import facts
    if shared is None:
        shared = {}
    if 'parts' not in shared:
        try:
            shared['parts'] = vault.read_shared(ctx, cur.service or facts.keychain_service(ctx.env)) or {}
        except Exception:  # noqa: BLE001 - a record that can't be read changes nothing
            shared['parts'] = {}
    return shared['parts']


def _cred_path(ctx):
    from . import facts
    return facts.credentials_file(ctx.env, ctx.home)


def _sha(text):
    return hashlib.sha256((text or '').encode('utf-8')).hexdigest()


def _written_note(note, created, back, left, size=None, user=None):
    """Fill note (when given) with a landed write's facts: how it was made, the SHA-256 of the item's text as read
    back and of its account login (_login_sha), the stamp of the plaintext file the write put there (left), and the
    size of Claude Code's settings file read right before it (size) with the mark of that file's own id and its
    number of keys (user: _settings_mark)."""
    if note is not None:
        mark, keys = user or (None, None)
        note.update(how='created' if created else 'over', sha=_sha(back.read.value), login_sha=_login_sha(back.login),
                    file=list(left) if left else None, settings_size=size, settings_user=mark, settings_keys=keys)


# ---------- a write of ours that met a sign-out, taken back ----------
#
# A chosen sign-out deletes Claude Code's item, removes its plaintext file, and clears the settings' account a moment
# later (a sign-in inside Claude Code runs the same steps first). A put-back, a promote or a repair from the file that
# lands in that moment would leave a login behind the sign-out. So such a write is taken back. This is the only place
# Switchboard removes a login from Claude Code's item outside a switch's roll back, and only within five bounds, all of
# which must hold:
#   1. it is our own write: the record notes each one that landed (how it was made, a SHA-256 of the item's text and
#      of its account login, the settings file's size);
#   2. it landed less than TAKE_BACK_S ago;
#   3. the settings name nobody: a settings file that is there and holds no account (never one that can't be read, and
#      never a missing one);
#   4. the item still holds the account login we wrote (read again under Claude Code's save lock just before): byte for
#      byte, or with another part changed by a chat that saved its own part in between and kept our login;
#   5. the settings file is the lived-in one, not one Claude Code made anew: it writes a small new file when it finds
#      none or cannot parse the old one, which names nobody too. Claude Code gives a settings file an id of its own
#      (`userID`) the first time it needs one, and a sign-out keeps that id (as of 2.1.332: the
#      sign-out clears the account and about twenty caches kept for it, kilobytes in a lived-in file, and nothing
#      else). So when our write noted that id, the file is the lived-in one while it still holds the same id and
#      has lost no more than TAKE_BACK_KEYS top-level keys since (a sign-out clears two dozen; a new file starts
#      with a handful, whatever id it is given, so a lived-in file's hundred are gone); else it was made anew, and
#      nothing is taken back (said once). A settings file that had no id at our write is judged by its size as
#      before: more than TAKE_BACK_SHRINK bytes smaller than at our write means made anew.
# An item that still holds byte for byte what we created is deleted, as a switch's roll back deletes it; one we wrote
# over gets back exactly what was there when the take-back comes in the same hold of the save lock as the write. Any
# other item keeps every part as that fresh read found it and loses only its account login, by an ordinary write (never
# a delete). The note lives its TAKE_BACK_S whatever the item holds meanwhile (the stamp of our own rewrite of the
# plaintext file lives in it, and the proof rule needs it for its first FAULT_SEEN_S); it ends early only when a newer
# write of ours replaces it or a take-back took. Every use checks at that moment that the item holds our login, so a
# note that outlives its login takes nothing back.
TAKE_BACK_SHRINK = 8192
TAKE_BACK_KEYS = 32             # top-level keys a sign-out may take out of the settings file (it clears 23 in 2.1.332)
ANEW_LINE = ("Claude Code's settings file is much smaller than a minute ago; Switchboard left its write as it is")
ANEW_ID_LINE = ("Claude Code's settings file is not the one from a minute ago; Switchboard left its write as it is")


def _names_nobody(cfg_r, block):
    """Claude Code's settings file is there and names no account (what a chosen sign-out leaves)."""
    uuid, email, _ = live_mod.label(block)
    return cfg_r.kind == PRESENT and not uuid and not email


def _login_sha(login):
    """SHA-256 of a login's account login object (claudeAiOauth) as canonical JSON (sorted keys, no spaces), or None
    when it has none."""
    from . import facts
    o = login.blob.get(facts.OAUTH_KEY) if login is not None and isinstance(login.blob, dict) else None
    if not isinstance(o, dict):
        return None
    return hashlib.sha256(json.dumps(o, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def _settings_size(path):
    """The size in bytes of Claude Code's settings file (one stat, no parse), or None."""
    try:
        return os.stat(path).st_size
    except (OSError, TypeError, ValueError):
        return None


def _settings_mark(path):
    """(a mark of the id Claude Code's settings file holds for itself: 16 hex of a SHA-256 of its `userID`, or None
    when it has none; its number of top-level keys), or (None, None) when the file can't be read. The id is not a
    secret and names no account; only its mark is kept."""
    r, obj = ccfiles.read_json(path)
    if r.kind != PRESENT:
        return None, None
    uid = obj.get('userID')
    return (_sha(uid)[:16] if isinstance(uid, str) and uid else None), len(obj)


def _made_anew(path, size, user=None, keys=None):
    """Bound 5 fails. With the mark of the settings file's own id noted at our write (user): the file now holds
    another id, or none, or over TAKE_BACK_KEYS fewer top-level keys than it had then (keys), or can't be read.
    Without a mark: the file is more than TAKE_BACK_SHRINK bytes smaller than at our write (size), or either size
    is not known."""
    if user is not None:
        mark, count = _settings_mark(path)
        return mark != user or (keys is not None and keys - count > TAKE_BACK_KEYS)
    now = _settings_size(path)
    return size is None or now is None or now < size - TAKE_BACK_SHRINK


def _ours(read, sha, login_sha):
    """How a Keychain read still holds a write of ours: 'bytes' (byte for byte), 'login' (its account login is the one
    we wrote and another part changed), or None."""
    if not (read is not None and read.usable and read.read.provenance == 'keychain'):
        return None
    if _sha(read.read.value) == sha:
        return 'bytes'
    if login_sha is not None and _login_sha(read.login) == login_sha:
        return 'login'
    return None


def _undo(ctx, held, how, sha, prior=None, login_sha=None, size=None, user=None, said='wrote its Keychain item'):
    """Holding Claude Code's save lock (held): take back a write of ours (the bounds above; the caller holds the note).
    how: 'created' or 'over'; sha and login_sha: the SHA-256 of what the item held right after it and of its account
    login; prior: the item's text before it (for 'over', when known); size and user: the settings file's size and
    (mark of its own id, number of keys) right before it; said: what Switchboard did, for the line of a failure.
    'kept' (the settings name someone: nothing to do), 'anew' (the settings file was made anew: nothing to do), 'gone'
    (the item does not hold our login now: nothing is touched), 'taken', or 'failed' (it did not read back).
    ccfiles.LockBusy when the lock given is no longer this process's: nothing is deleted or written without it."""
    from . import facts
    cfg_r, block, cfg_path = live_mod.config_identity(ctx)
    if not _names_nobody(cfg_r, block):
        return 'kept'
    if _made_anew(cfg_path, size, *(user or (None, None))):
        return 'anew'
    cur = live_mod.read_live(ctx, fresh=True, interactive=True)
    match = _ours(cur, sha, login_sha)
    if match is None:
        return 'gone'
    if match == 'bytes' and how == 'created':
        if not held.still_held():                       # taken over, or let go on the way out (ccfiles.release_all)
            raise ccfiles.LockBusy(held.path)
        done = ctx.keychain.delete(facts.keychain_service(ctx.env), facts.keychain_account(ctx.env))
        back = live_mod.read_live(ctx, fresh=True, interactive=True)
        ok = done.ok and (back.read.kind == ABSENT or (back.usable and back.read.provenance == 'file'))
    else:
        if prior is None or match != 'bytes':
            blob = dict(cur.login.blob) if cur.login is not None and isinstance(cur.login.blob, dict) else {}
            blob.pop(facts.OAUTH_KEY, None)
            prior = live_mod.dump(blob)
        done = live_mod.write_live(ctx, prior, interactive=True, held=held)
        back = live_mod.read_live(ctx, fresh=True, interactive=True)
        ok = done.ok and back.usable and back.login is not None and back.login.text == prior
    if not ok:
        ctx.log("Claude Code was signing out when Switchboard %s, and taking that back did not work (%s); the next "
                "look tries again" % (said, 'Keychain: %s' % done.why if not done.ok else 'it did not read back'))
    return 'taken' if ok else 'failed'


def _taken_back(ctx, who, said=False):
    """The answer for a write taken back: the write does not count, and nothing is notified (nothing the person did
    not choose is left). said: the write's own History line went out (a take-back at a later look), so this one is
    said in History too, once for that write; else it is said once in the log."""
    line = TAKEN_BACK_LINE % who
    if said:
        ctx.say(line, 'info', h='repair', account=who, by='auto')
    elif _once(ctx, 'taken_back'):
        ctx.log(line)
    else:
        line = None
    return PutBack('leave', account=who, reason='Claude Code was signing out', line=line)


def _uncount(rec, w):
    """A write of ours taken back at a later look does not count: the one entry of rec['done'] that is its (its time is
    the note's 'at') goes, and the open episode's count of writes goes down by one (never below zero). An episode that
    write opened and that holds no other write goes with its start, so it counts toward no limit. rec['last'] stays: it
    is what happened."""
    at = w['at']
    done = list(rec.get('done') or [])
    if at in done:
        done.remove(at)
    rec['done'] = done
    ep = rec.get('episode')
    if ep and ep['start'] <= at:
        writes = max(0, ep['writes'] - 1)
        if writes == 0 and ep['start'] == at:
            rec['episode'] = None
            rec['episodes'] = [t for t in rec.get('episodes') or [] if t != at]
        else:
            rec['episode'] = dict(ep, writes=writes)


def _maybe_take_back(ctx, rec, lr, now):
    """At a look: the record's note of our last write, held to the bounds above. An item that does not hold our login
    now is left as it is and the note stays (it lives its TAKE_BACK_S); a settings file made anew is said once and the
    note stays until it ages out. None when nothing was taken back, else the look's answer."""
    w = rec.get('written') if rec.get('readable') else None
    if not w or not 0 <= now - w['at'] < TAKE_BACK_S:
        return None
    if lr is None or lr.read.kind == UNREADABLE or lr.read.degraded:
        return None                                     # can't tell now; a later look may
    if _ours(lr, w['sha'], w.get('login_sha')) is None:
        return None
    cfg_r, block, cfg_path = live_mod.config_identity(ctx)
    if not _names_nobody(cfg_r, block):
        return None
    if _made_anew(cfg_path, w.get('settings_size'), w.get('settings_user'), w.get('settings_keys')):
        if not w.get('anew_said'):
            ctx.log(ANEW_ID_LINE if w.get('settings_user') else ANEW_LINE)
            _change_record(ctx, lambda r: r.update(written=dict(w, anew_said=True)) if r.get('written') == w else
                           None, now)
        return None
    return _take_back(ctx, w, now)


def _take_back(ctx, w, now):
    """Take back the write the note w describes, under the locks a put-back takes and Claude Code's save lock."""
    aid = w['account']
    outcome = None
    try:
        with vault.switch_lock(ctx, timeout=0.5), vault.consume_lock(ctx, aid, timeout=2.0), \
                vault.account_lock(ctx, timeout=5.0):
            rec = load_record(ctx)
            if not rec['readable'] or rec.get('written') != w:
                return None                             # taken back by another look, or a newer write since
            who = (vault.load(ctx)['accounts'].get(aid) or {}).get('email') or aid
            with live_mod.storage_lock(ctx) as held:
                outcome = _undo(ctx, held, w['how'], w['sha'], login_sha=w.get('login_sha'),
                                size=w.get('settings_size'), user=(w.get('settings_user'), w.get('settings_keys')))
            if outcome == 'taken':                      # 'gone' leaves the note: it lives its TAKE_BACK_S
                rec['written'] = None
                _uncount(rec, w)
                _save_record(ctx, rec, now)
    except (vault.LockBusy, ccfiles.LockBusy, vault.VaultError, ccfiles.CCFileError, OSError) as e:
        ctx.log('Taking back a write that met a sign-out waits: %s' % e.__class__.__name__)
        return PutBack('wait', reason='busy', watch=True)
    if outcome == 'taken':
        return _taken_back(ctx, who, w.get('said'))
    if outcome == 'failed':
        return PutBack('wait', account=who, reason='taking back a write that met a sign-out did not work', watch=True)
    return None


def _maybe_take_out(ctx, lr, now):
    """At a look in the sign-in watch's own process: the login that a file of the hold's held, found in Claude Code's
    item while its settings name nobody. A sign-out chosen in the seconds after one of Claude Code's refused saves
    removes the plain login file while the settings still name the account; the hold puts the file back until the
    settings change a moment later; a Claude Code process that saves in that moment reads the login from the file and
    saves it into the item. That is a login behind a sign-out, there because of a file of ours, so it is taken out
    again, within the bounds of a write of ours that met a sign-out (above): the hold put that file in place less than
    TAKE_BACK_S ago; the settings name nobody; the settings file is the lived-in one; and the item, read again under
    Claude Code's save lock, still holds that very account login. The item keeps every other part and loses only its
    account login, by an ordinary write, never a delete. None when nothing was taken out, else the look's answer."""
    with _HOLD_LOCK:
        g = _HOLD_GAVE.get(ctx.data_dir)
    if not g or not 0 <= now - g['at'] < TAKE_BACK_S or g['login_sha'] is None:
        return None
    if lr is None or lr.read.kind == UNREADABLE or lr.read.degraded or _ours(lr, g['sha'], g['login_sha']) is None:
        return None
    cfg_r, block, cfg_path = live_mod.config_identity(ctx)
    marks = (g['settings_user'], g['settings_keys'])
    if not _names_nobody(cfg_r, block) or _made_anew(cfg_path, g['settings_size'], *marks):
        return None
    try:
        with vault.switch_lock(ctx, timeout=0.5), vault.account_lock(ctx, timeout=5.0), \
                live_mod.storage_lock(ctx) as held:
            outcome = _undo(ctx, held, 'over', g['sha'], login_sha=g['login_sha'], size=g['settings_size'],
                            user=marks, said='put its plain login file back')
    except (vault.LockBusy, ccfiles.LockBusy, vault.VaultError, ccfiles.CCFileError, OSError) as e:
        ctx.log('Taking a login out that met a sign-out waits: %s' % e.__class__.__name__)
        return PutBack('wait', reason='busy', watch=True)
    if outcome == 'failed':
        return PutBack('wait', reason='taking a login out that met a sign-out did not work', watch=True)
    if outcome != 'taken':
        return None
    with _HOLD_LOCK:
        if _HOLD_GAVE.get(ctx.data_dir) is g:
            del _HOLD_GAVE[ctx.data_dir]
    ctx.log(HOLD_TAKEN_OUT_LINE)
    return PutBack('leave', reason='Claude Code was signing out')


def _again(ctx, now, rec, vr, beside_file=None):
    """The rule again on a fresh live read made under Claude Code's save lock: check(cur) -> Verdict, with the record
    read under the account lock (rec) and the saved login read before the save lock (vr; None: described from its
    record). No Keychain call: only the files the rule reads. beside_file: whether the write decided before the save
    lock comes from Claude Code's plaintext file beside an item without a login (a put-back); a plaintext login file
    that appeared or went since is another decision, so nothing is written this time."""
    def check(cur):
        seen = see(ctx, now, interactive=True, lr=cur, saved=vr if vr is not None else RECORD)[0]
        if beside_file is not None and seen.file_login != beside_file:
            return Verdict('leave', reason='its plain login file changed')
        return decide(seen, rec['missing'], rec['done'], now, rec['failed'], **_evidence(rec, now))
    return check


def _unlike_record(ctx, who):
    """The answer when the saved login read under the locks is not the one its record describes and not newer than it
    (_newer_than_record): nothing written, said once in the log."""
    line = None
    if _once(ctx, 'unlike_record'):
        line = UNLIKE_RECORD_LINE % who
        ctx.log(line)
    return PutBack('leave', account=who, reason='the saved login is not the one its record describes', line=line)


def _put_back(ctx, aid, who, now, out=None):
    """The write, under the switch lock, the account's consume lock and the account lock: the saved login is taken from
    the first look's read while the record still describes it (_saved_kept), else read there once (before Claude
    Code's save lock; not for the file's own login), and must be the one its record describes or newer than it
    (_newer_than_record); the rule decides again with it and the live read the look made (out['lr']), and again under
    the save lock with a fresh live read (_try_once); with any account login in the item, nothing is written. The
    tries are made holding Claude Code's two credential locks too (taken after the account lock and before the save
    lock, in the switch's order; CC_LOCKS_S): when they stay held, nothing is written and the answer is 'wait'. The
    PutBack's `at` is when the episode's first write landed, so every put-back of one episode makes one notification.
    out['straight'] (a dict) on the straight path (_straight), where out['lr'] is what the sighting was of, not a
    read: then None is returned when nothing was written and nothing was tried, and nothing is said (the look goes on
    from the read under the save lock, out['straight']['read'], when one was made)."""
    out = {} if out is None else out
    straight = out.get('straight')
    at, more, tell = now, None, True
    try:
        with vault.switch_lock(ctx, timeout=0.5), vault.consume_lock(ctx, aid, timeout=2.0), \
                vault.account_lock(ctx, timeout=5.0):
            rec = load_record(ctx)
            seen, lr, saved, _ = see(ctx, now, interactive=True, lr=out.get('lr'), saved=RECORD)
            vr = None
            if not (seen.file_login and seen.owner and _described(saved)):   # the file's own login needs no saved one
                vr = _saved_kept(ctx, aid, (vault.load(ctx)['accounts'].get(aid) or {}).get('fp')) or \
                    vault.read_current(ctx, aid, fresh=True, interactive=True)
                seen, lr, saved, _ = see(ctx, now, interactive=True, lr=lr, saved=vr)
                out.update(seen=seen, saved=saved)
            again = decide(seen, rec['missing'], rec['done'], now, rec['failed'], **_evidence(rec, now)) \
                if rec['readable'] else None
            if straight is not None and (again is None or again.action != 'put_back' or again.account != aid or
                                         saved is None):
                return None                             # decided from no read of the item: the look goes on
            if again is not None and again.action == 'leave' and again.key and again.account == aid:
                return _left(ctx, again, who)
            if again is None or again.action != 'put_back' or again.account != aid or saved is None:
                return PutBack('wait', account=who, reason='not now: %s' % (again.reason if again else 'no record'),
                               watch=bool(again and again.watch))
            doc = vault.load(ctx)
            if vr is not None and saved.fingerprint != doc['accounts'][aid].get('fp') and \
                    not _newer_than_record(saved, doc['accounts'][aid], doc, aid):
                return None if straight is not None else _unlike_record(ctx, who)
            note = {}
            filed = _file_text(ctx) if seen.file_login else None
            check = _again(ctx, now, rec, vr, filed is not None)
            # Claude Code's two credential locks, as a switch takes them, only around the tries (a chat that renews
            # holds them until it has saved, so it can't save between the last look and the write); never around the
            # reads above, which may wait on a Keychain dialog while Claude Code's renewals would wait on us
            with ccfiles.credential_locks(ctx.env, ctx.home, budget_s=CC_LOCKS_S, log=ctx.log):
                outcome, why = _tries(ctx, doc, aid, saved, note, filed, check, straight)
            if straight is not None and outcome in ('present', 'changed', 'failed') and not straight.get('wrote'):
                return None                             # the read under the save lock found something else
            source = note.pop('source', None)
            if outcome == 'landed' and source != 'file' and vr is not None and \
                    saved.fingerprint != doc['accounts'][aid].get('fp'):
                _note_newer(ctx, aid, who, saved, now)
            if outcome == 'landed':
                rec['done'].append(now)
                rec['missing'] = None
                rec['failed'] = []
                rec['limit_at'] = None                  # a limit reached again later is said again
                rec['last'] = {'at': now, 'account': who, 'how': 'put_back'}
                rec['written'] = dict(note, at=now, account=aid)
                ep = _note_write(rec, now)
                at = ep['start']
                # in an episode that saw proof of the fault (up to EPISODE_WRITES land in it) the History line is
                # its first put-back's, the rest are counted in the log; without proof each put-back says it (at
                # most PUT_BACK_MAX an hour)
                if ep.get('proof') and ep.get('put_back_said'):
                    more = '; %d writes of its login since the first one, %.0f s ago' % (ep['writes'], now - at)
                else:
                    rec['episode'] = dict(ep, put_back_said=True)
                    more = None
                rec['written']['said'] = more is None   # its History line goes out once the locks are let go
                # the note is saved after the Keychain write, outside Claude Code's save lock (never held for a file
                # write): a process that stops in between leaves a login of ours with no note
                try:
                    _save_record(ctx, rec, now)
                except (ccfiles.CCFileError, OSError) as e:
                    ctx.log('The put-back of %s was not noted (%s): it does not count toward the limit'
                            % (who, e.__class__.__name__))
            elif outcome == 'failed':
                rec['failed'].append(now)
                tell = not rec.get('failed_said')       # said once per episode, whichever process tries
                rec['failed_said'] = True
                try:
                    _save_record(ctx, rec, now)
                except (ccfiles.CCFileError, OSError) as e:
                    ctx.log('A put-back that did not land was not noted (%s)' % e.__class__.__name__)
    except (vault.LockBusy, vault.VaultError, ccfiles.LockBusy, ccfiles.CCFileError, OSError) as e:
        ctx.log('Putting the sign-in back waits: %s' % e.__class__.__name__)
        return PutBack('wait', account=who, reason='busy', watch=True)
    if outcome in ('present', 'changed'):
        return PutBack('leave', account=who, reason=why)
    if outcome == 'taken_back':
        return _taken_back(ctx, who)
    if outcome == 'busy':
        return PutBack('wait', account=who, reason=why, watch=True)
    if outcome == 'failed':
        line = None
        if tell and _once(ctx, 'failed'):
            line = ('Claude Code lost its sign-in, and putting %s back did not work (%s); it tries again, less often '
                    'each time' % (who, why))
            ctx.log(line)
        return PutBack('failed', account=who, reason=why, line=line, watch=True)
    reset(ctx)
    if source == 'file':
        ctx.log("Put the login of %s from Claude Code's plain login file in its Keychain item, which held no login "
                "(%s)%s" % (who, live_mod.parse(filed).fingerprint or 'no refresh token', more or ''))
    elif source == 'saved':
        ctx.log("Put the saved login of %s in Claude Code's Keychain item, which held no login, instead of the older "
                "one in its plain login file (%s)%s" % (who, saved.fingerprint or 'no refresh token', more or ''))
    else:
        ctx.log('Put the saved login of %s back in Claude Code, which had lost its sign-in (%s)%s'
                % (who, saved.fingerprint or 'no refresh token', more or ''))
    line = None
    if more is None:
        line = 'Claude Code lost its sign-in; Switchboard put %s back' % who
        ctx.say(line, 'info', h='repair', account=who, by='auto')
    return PutBack('put_back', account=who, reason='its login was missing and its settings still named it', line=line,
                   watch=True, at=at)


# ---------- the login in Claude Code's plaintext file, put back in the Keychain ----------

def _promote(ctx, aid, who, now, out=None):
    """Put the login Claude Code keeps only in its plaintext file back into the Keychain: under the switch lock, the
    account's consume lock and the account lock, the rule decides again with the live read the look made (out['lr']);
    then, under Claude Code's save lock, a fresh read and the rule again, and the file's own bytes (Claude Code's whole
    object, connector logins included) are written with "create only if still absent", read back, and the file is
    removed only when it is still the very file the write left (as Claude Code does after a save that found the item
    absent). When the file holds an older generation of the account's sign-in than its saved copy (its refresh token
    is used up), the saved copy (the first look's read, or read once before the save lock; the one its record
    describes, or newer) is written instead, with the file's machine-wide parts. Then the saved copy is brought up to
    the login when that is newer (the keep pass). Said once per episode, with no notification: nothing was lost.
    out['straight']: as for _put_back."""
    out = {} if out is None else out
    straight = out.get('straight')
    said, tell = None, True
    try:
        with vault.switch_lock(ctx, timeout=0.5), vault.consume_lock(ctx, aid, timeout=2.0), \
                vault.account_lock(ctx, timeout=5.0):
            rec = load_record(ctx)
            seen, lr, saved, _ = see(ctx, now, interactive=True, lr=out.get('lr'), saved=RECORD)
            vr = None
            if not (seen.owner and _described(saved)):      # an older generation in the file: the saved copy goes in
                vr = _saved_kept(ctx, aid, (vault.load(ctx)['accounts'].get(aid) or {}).get('fp')) or \
                    vault.read_current(ctx, aid, fresh=True, interactive=True)
                seen, lr, saved, _ = see(ctx, now, interactive=True, lr=lr, saved=vr)
                out.update(seen=seen, saved=saved)
            again = decide(seen, rec['missing'], rec['done'], now, rec['failed'], **_evidence(rec, now)) \
                if rec['readable'] else None
            if straight is not None and (again is None or again.action != PROMOTE or again.account != aid):
                return None                             # decided from no read of the item: the look goes on
            if again is not None and again.action == 'leave' and again.key and again.account == aid:
                return _left(ctx, again, who, True)
            if again is None or again.action != PROMOTE or again.account != aid:
                return PutBack('wait', account=who, reason='not now: %s' % (again.reason if again else 'no record'),
                               watch=bool(again and again.watch))
            text = lr.read.value
            if not seen.owner:                  # the file holds an older generation: the newer saved copy goes in
                accounts = vault.load(ctx)
                a = accounts['accounts'][aid]
                if vr is None or saved is None or (saved.fingerprint != a.get('fp') and
                                                   not _newer_than_record(saved, a, accounts, aid)):
                    return None if straight is not None else _unlike_record(ctx, who)
                text = live_mod.dump(live_mod.compose(saved.blob, lr.login.blob))
            note = {}
            outcome, why = _promote_tries(ctx, aid, lr.read.value, text, note, _again(ctx, now, rec, vr), straight)
            if straight is not None and outcome in ('present', 'changed', 'failed') and not straight.get('wrote'):
                return None                             # the read under the save lock found something else
            if outcome == 'landed' and not seen.owner and \
                    saved.fingerprint != vault.load(ctx)['accounts'][aid].get('fp'):
                _note_newer(ctx, aid, who, saved, now)
            if outcome == 'landed':
                ep = _note_write(rec, now)
                rec['missing'] = None
                rec['failed'] = []
                rec['last'] = {'at': now, 'account': who, 'how': PROMOTE}
                rec['written'] = dict(note, at=now, account=aid)
                if not ep.get('said'):
                    rec['episode'] = dict(ep, said=True)
                    said = PROMOTE_LINE
                rec['written']['said'] = said is not None   # said in History once the locks are let go
                try:
                    _save_record(ctx, rec, now)
                except (ccfiles.CCFileError, OSError) as e:
                    ctx.log('The promote for %s was not noted (%s): it does not count toward the limit'
                            % (who, e.__class__.__name__))
            elif outcome == 'failed':
                rec['failed'].append(now)
                tell = not rec.get('failed_said')       # said once per episode, whichever process tries
                rec['failed_said'] = True
                try:
                    _save_record(ctx, rec, now)
                except (ccfiles.CCFileError, OSError) as e:
                    ctx.log('A promote that did not land was not noted (%s)' % e.__class__.__name__)
    except (vault.LockBusy, vault.VaultError, ccfiles.CCFileError, OSError) as e:
        ctx.log('Putting the sign-in back in the Keychain waits: %s' % e.__class__.__name__)
        return PutBack('wait', account=who, reason='busy', watch=True)
    if outcome in ('present', 'changed'):
        return PutBack('leave', account=who, reason=why)
    if outcome == 'taken_back':
        return _taken_back(ctx, who)
    if outcome == 'busy':
        return PutBack('wait', account=who, reason=why, watch=True)
    if outcome == 'failed':
        line = None
        if tell and _once(ctx, 'failed'):
            line = ("Claude Code moved its sign-in to its plain login file, and putting it back in the Keychain "
                    "did not work (%s); it tries again, less often each time" % why)
            ctx.log(line)
        return PutBack('failed', account=who, reason=why, line=line, watch=True)
    reset(ctx)
    ctx.log('Put the login of %s from Claude Code\'s plain login file back in the Keychain (%s%s)'
            % (who, live_mod.parse(text).fingerprint or 'no refresh token',
               '' if text == lr.read.value else ', the saved copy: the file held an older one'))
    if said:
        ctx.say(said, 'info', h='repair', account=who, by='auto')
    _keep_after(ctx)
    return PutBack(PROMOTE, account=who, reason='its login sat only in its plain login file', line=said, watch=True,
                   at=ep['start'])


def _promote_tries(ctx, aid, filed, text, note=None, check=None, straight=None):
    """Under the locks: write text (the plaintext file's bytes, filed, or the newer saved copy) as the item, until one
    write reads back, at most TRIES times within TRIES_WITHIN_S, each try's fresh read, write, read-back and removal of
    the file under Claude Code's save lock. ('landed', None), ('present' or 'changed', words) when there is nothing to
    do any more, ('taken_back', words) when the write met a sign-out and was undone (_undo), ('busy', words) when
    Claude Code kept its save lock past the budget, or ('failed', words). note (a dict) gets the landed write's facts
    (_written_note). check: the rule again on each try's fresh read (_again). straight: as for _tries."""
    start = ctx.mono()
    why = None
    for n in range(TRIES):
        try:
            with live_mod.storage_lock(ctx) as held:
                _lock_busy(ctx, False)
                outcome, why = _promote_once(ctx, aid, filed, text, held, note, check, straight)
        except ccfiles.LockBusy:
            _lock_busy(ctx, True)
            return 'busy', 'Claude Code was saving its login'
        if outcome is not None and outcome != 'again':
            return outcome, why
        if n + 1 >= TRIES or ctx.mono() - start >= TRIES_WITHIN_S:
            break
        if outcome is None:
            ctx.sleep(_RAND.uniform(*TRY_GAP_S))
    return 'failed', why


def _promote_once(ctx, aid, filed, text, held, note=None, check=None, straight=None):
    """One try, holding Claude Code's save lock (held). The item still absent, the file's bytes still filed, the
    settings still naming aid and the rule on this read still saying 'promote' for aid (check(cur), _again): create the
    item with text. 'exists': someone made the item first, so read and decide again ('again'). straight: as for
    _tries. (outcome, words); outcome None: the write did not land, try again after a gap.

    The plain login file is not removed here. The Claude Code processes that were told "no such item" while the item
    was gone trust that answer for 30 s and read their login from the file meanwhile; removing it now would leave
    them with no login anywhere. So the file stays: exactly as it is when it holds the very login now in the item,
    rewritten with that login when it held an older one (live.write_live, keep_file). It is handed to the sign-in
    hold (hold_adopt), which takes it away once the item has been back for HOLD_TIDY_S. With the hold's setting off,
    or when its note can't be written, the file is removed at once, as long as it is still the one this write left."""
    from . import facts
    cur = live_mod.read_live(ctx, fresh=True, interactive=True)
    if straight is not None:
        straight['read'] = cur
    if cur.read.kind == UNREADABLE or cur.read.degraded:
        return 'failed', "Claude Code's login can't be read (%s)" % (cur.read.why or 'a fallback copy')
    if cur.read.kind != PRESENT or cur.read.provenance != 'file':
        return 'present', 'the Keychain item is there now'
    if cur.read.value != filed:
        return 'changed', 'its plain login file changed'
    cfg_r, block, cfg_path = live_mod.config_identity(ctx)
    if cfg_r.kind != PRESENT or identity.label_id(vault.load(ctx), block) != aid:
        return 'changed', "Claude Code's settings changed"
    size = _settings_size(cfg_path)                 # the lived-in settings file, for a take-back (bound 5)
    user = _settings_mark(cfg_path)
    if check is not None:
        v = check(cur)
        if v.action != PROMOTE or v.account != aid:
            return 'changed', v.reason
    if straight is not None:
        straight['wrote'] = True
    wrote = {}
    keep = hold_on(ctx)
    done = live_mod.write_live(ctx, text, interactive=True, create_only=True, held=held, wrote=wrote, keep_file=keep)
    path = facts.credentials_file(ctx.env, ctx.home)
    left = wrote.get('file')                        # the plaintext file as this write left it, by its own stamp
    if not done.ok and done.why == 'exists':
        return 'again', 'another process made the item first'
    back = live_mod.read_live(ctx, fresh=True, interactive=True)
    if not (back.usable and back.read.provenance == 'keychain' and back.login is not None and
            back.login.text.strip() == text.strip()):
        return None, ('Keychain: %s' % done.why) if not done.ok else 'it did not read back'
    if _undo(ctx, held, 'created', _sha(back.read.value), None, _login_sha(back.login), size, user) == 'taken':
        return 'taken_back', 'Claude Code was signing out'      # its file stays, for its sign-out to remove
    _written_note(note, True, back, left, size, user)
    if keep and hold_adopt(ctx, path, left):
        return 'landed', None                       # the file stays for now; a look takes it away (hold_tidy)
    if _remove_if_same(ctx, path, left, (filed.encode('utf-8'), text.encode('utf-8'))):
        _written_note(note, True, back, None, size, user)   # nothing of ours is left in the file
    return 'landed', None


def _remove_if_same(ctx, path, left, allowed):
    """Remove Claude Code's plaintext login file only when it is still the very file our write left: the stamp of the
    file the write put there (left) and bytes that are one of allowed. A file written since, even with
    the same bytes, stays: a Claude Code process whose save was refused writes the file again and deletes the item a
    moment later, and removing that fresh file would leave no login anywhere. True when removed."""
    if left is None:
        return False
    r, raw = ccfiles.read_bytes(path)
    if r.kind != PRESENT or raw not in allowed or _stamp(path) != left:
        return False
    try:
        os.unlink(path)
    except OSError as e:
        ctx.log("Could not remove Claude Code's plain login file after putting its login back in the Keychain "
                "(%s)" % e.__class__.__name__)
        return False
    return True


_KEPT = {}                      # data folder -> (account, fingerprint, ctx.mono()) the keep pass last answered for here
KEPT_FOR_S = 60.0               # an answer of the keep pass stands this long for the same login


def _keep_after(ctx, aid=None, fp=None, login=None):
    """Bring the saved copy up to Claude Code's login when that is a newer generation: the keep pass, asked now (it
    decides whose the login is the one way it always does). Called outside every lock. aid and fp (and the live Login),
    when given: once the keep pass answered for that login (a note, or nothing left to keep: _nothing_to_keep) it is
    not asked about it again by this process for KEPT_FOR_S; a pass that returned nothing for another reason (a lock
    busy, a renewal under way) is asked again at the next look. Returns the keep pass's note or None."""
    if aid is not None:
        with _SAID_LOCK:
            k = _KEPT.get(ctx.data_dir)
            if k is not None and k[:2] == (aid, fp) and 0 <= ctx.mono() - k[2] < KEPT_FOR_S:
                return None
    try:
        note = capture.keep_pass(ctx, force=True)
    except Exception as e:  # noqa: BLE001 - keeping is retried by the loop's own pass
        ctx.log('Keeping the newer login waits: %s' % e.__class__.__name__)
        return None
    if aid is not None and (note or _nothing_to_keep(ctx, login)):
        with _SAID_LOCK:
            _KEPT[ctx.data_dir] = (aid, fp, ctx.mono())
    return note


def _nothing_to_keep(ctx, login):
    """After a keep pass that returned no note: nothing is left for it to keep of this live login, by the accounts file
    alone. The login is the saved copy of the account it is proven to be, or not newer than it (the keep pass keeps
    only a newer generation), or proven to be no saved account's (the keep pass decides that one in its own way, and
    is not asked again at every look). False when it can't be told: the pass is asked again."""
    if login is None:
        return False
    try:
        doc = vault.load(ctx)
    except vault.VaultError:
        return False
    proof = identity.offline(login, doc)
    a = doc['accounts'].get(proof.account_id) if proof.account_id else None
    if a is None:
        return proof.account_id is None
    saved = _recorded(a)
    return saved is not None and (login.fingerprint == saved.fingerprint or
                                  live_mod.compare(login, saved) != 'newer')


# ---------- the sign-in hold: Claude Code's own plain login file, put back the moment it goes ----------
#
# The fault has two halves (Claude Code as of 2.1.329, and storms of stand-ins in its save order on
# the real Keychain machinery): a process whose save macOS refused writes its login to its plain login file and deletes
# the Keychain item 0.1 to 0.35 s later; another process, whose save landed after it had read "no such item", removes
# that file. When the removal lands between the two, nothing is left anywhere, and every process that saves from then
# on writes an item without the login. In every such storm measured the removal came within 0.67 s of the write that
# made the file. Putting the item back takes Keychain calls, seconds on a loaded Mac; putting the file back takes a
# millisecond. So the sign-in watch tells the hold each time the file appears, changes or goes (hold_seen), and the
# hold puts Claude Code's own bytes back.
#
# This is one of the places Switchboard creates Claude Code's plain login file (the others, a spare copy of the
# sign-in in the Keychain item and the standing copy of it, are at the end of this file), and here only within every
# one of these bounds:
#   1. the bytes are exactly what Claude Code itself wrote there: a full account login, read from a regular file of at
#      most HOLD_READ_MAX bytes written less than HOLD_FOR_S ago (by its modification time), kept in this process's
#      memory only (never on disk, never logged); never a login the saved accounts prove to be another account's than
#      the one the settings name (a process that read it before a switch can write it after);
#   2. the file went less than HOLD_FOR_S after that write (by the wall clock and the awake clock both), and fewer than
#      HOLD_MAX times since it;
#   3. Claude Code's settings name an account, the same account and organization as when the bytes were kept, read
#      fresh before the write and again after it: a chosen sign-out clears that name, and once it is cleared nothing is
#      put back and a file of ours is taken away;
#   4. no switch is part-way, no sign-in is under way on the page, and the setting `hold_signin_file` is on;
#   5. there is no file: the bytes go to a temporary file beside it, owner-only from its first byte, and into place with
#      os.link, which fails rather than replace a file Claude Code wrote a moment before.
# A file of ours is known by its stamp (device, inode, modification time in ns, size), taken from the temporary file
# before the link (a link keeps all four): in this process's holder and in <data>/signin-hold.json (the path, the
# stamp, the wall time; no secret), which is written as pending before the link (naming the temporary file) and settled
# after it, so a process stopped between the two leaves no file nobody knows is ours. It is taken away again
# (hold_tidy) as soon as the settings name nobody or another account, by any write of Claude Code's item
# (live.write_live), at the sign-in watch's start, by the uninstaller, and by the first look that finds the Keychain
# item there once the file's bytes are HOLD_TIDY_S old (Claude Code reads the file only while the item is absent; the
# sign-in watch has a look made at that moment). Not sooner: the process whose save was refused deletes the item
# with a call of its own after it wrote the file (a moment later on a quiet Mac, seconds later on a loaded one), so
# a look that read the item just before that delete would take away the one login left.
# Beside an item that holds no login, a file the look will put that item back from (the rule for a plain login file
# proven the account's own) stays for that, up to HOLD_KEEP_S: it is the newest login there is, newer than the saved
# copy when the save that was refused followed a renewal. Never a file Claude Code wrote since. While the item is
# missing, a file of ours is Claude Code's sign-in and is treated as the file Claude Code wrote: promoted by the rule
# for such a file, or left; one that keeps Claude Code signed in past HOLD_KEEP_S stays, said once.
# One more file is the hold's to take away, though the hold did not put it there: the plain login file whose login a
# promote just wrote into the Keychain item (hold_adopt). Removed at once, it left a hole: the Claude Code processes
# that were told "no such item" while the item was gone go on reading their login from that file (HOLD_TIDY_S, below).
# So it stays, untouched, under the same rules as a file of ours, and goes HOLD_TIDY_S after the write.
# What it cannot tell apart: a sign-out chosen less than HOLD_FOR_S after one of Claude Code's own refused saves removes
# the file while the settings still name the account, so the file is put back until the sign-out clears the settings a
# moment later; a Claude Code process that saves in that moment can read the login from it.
HOLD_FOR_S = 3.0
# How old a file of ours must be before a look that finds the Keychain item takes it away. A Claude Code process
# trusts each read of the Keychain item for 30 s (as of 2.1.329 to 2.1.332), a "no such item"
# answer too, and a process holding that answer reads its login from the plain login file at every request. So a
# process that asked while the item was gone goes on reading the file for up to 30 s after the item is back (plus
# the 2 s its Keychain call may take), and with the file gone sooner it is signed out for the rest of those 30 s.
# Seen on the real Keychain tool: requests of stand-in processes failed from the moment the file was removed until
# their 30 s were over.
HOLD_TIDY_S = 35.0
HOLD_MAX = 5
HOLD_KEEP_S = 120.0
HOLD_READ_MAX = 256 * 1024
HOLD_FILE = 'signin-hold.json'
HOLD_STAYS_LINE = ("Claude Code has kept its sign-in only in its plain login file for over two minutes, and its "
                   "Keychain item is still missing; Switchboard leaves the file there, since Claude Code reads its "
                   "login from it")
HOLD_TAKEN_OUT_LINE = ("Claude Code was signing out when Switchboard put its plain login file back, and a Claude Code "
                       "process saved the login from that file into the Keychain; Switchboard took that login out "
                       "again")
HOLD_PENDING_S = 10.0           # a pending note this old was left by a process stopped part-way (it takes milliseconds)
HOLD_LOOK_S = 0.2               # a look the hold asks for is made this long after the moment it is asked for
HOLD_AGAIN_S = 5.0              # a file of ours old enough to go and still there: a look is asked for again this soon
HOLD_AGAIN_LATER_S = 60.0       # ... and this seldom once its bytes are HOLD_KEEP_S old (it is Claude Code's sign-in)
_HOLD_LOCK = threading.Lock()   # guards the dicts below; held for microseconds, never across a file, a call or a lock
# Serialises this process's own changes to the file and the note (putting one in place, taking one away), so a look's
# tidy in another thread never meets the watch's put-back half done. Held for milliseconds, around file operations
# only: never across a Keychain call, the network, or the wait for another lock.
_HOLD_FILES = threading.Lock()
# data folder -> what this process's hold knows: 'text' (the bytes Claude Code wrote, or absent), 'at' (ctx.mono() of
# that write), 'wall_at' (its wall time), 'named' (_named() then), 'made' (files put back since), 'fill' (counts each
# keep and each emptying), 'path', 'ours' (the stamp of the file it put there last, or None). Never written anywhere.
_HOLDER = {}
_HOLD_FAILED = {}               # data folder -> quiet failures of the hold in this process
_HOLD_OWN = {}                  # data folder -> the stamp of the plain login file this process's own write of Claude
                                # Code's item last put there (hold_own): a rewrite of ours, not Claude Code's write
# data folder -> what the file the hold last put in place held: 'at' (wall), 'sha' (of its text), 'login_sha' (of its
# account login), and the settings file's size, mark and number of keys then. Hashes and numbers only; never written.
_HOLD_GAVE = {}
_HOLD_SETTING = {}              # Switchboard's settings path -> (its stamp, hold_signin_file)
_MIRROR_SETTING = {}            # Switchboard's settings path -> (its stamp, keep_signin_file)
_link = os.link                 # the one call that puts a file of ours in place (tests look at the temporary file here)


def hold_on(ctx):
    """The setting `auto_policy` -> `hold_signin_file` in Switchboard's settings.json (missing, or a file that can't be
    read, means on), read again only when the file's stamp changed."""
    path = ctx.path('settings.json')
    st = _stamp(path)
    with _HOLD_LOCK:
        hit = _HOLD_SETTING.get(path)
    if hit is not None and st is not None and hit[0] == st:
        return hit[1]
    r, obj = ccfiles.read_json(path)
    policy = obj.get('auto_policy') if r.kind == PRESENT else None
    on = not (isinstance(policy, dict) and policy.get('hold_signin_file') is False)
    if st is not None:
        with _HOLD_LOCK:
            _HOLD_SETTING[path] = (st, on)
    return on


def mirror_on(ctx):
    """The setting `auto_policy` -> `keep_signin_file` in Switchboard's settings.json (missing, or a file that can't
    be read, means on), with the sign-in hold's own setting on (hold_on): Claude Code's plain login file is kept as a
    standing copy of its Keychain item (the end of this file). Read again only when the file's stamp changed."""
    if not hold_on(ctx):
        return False
    path = ctx.path('settings.json')
    st = _stamp(path)
    with _HOLD_LOCK:
        hit = _MIRROR_SETTING.get(path)
    if hit is not None and st is not None and hit[0] == st:
        return hit[1]
    r, obj = ccfiles.read_json(path)
    policy = obj.get('auto_policy') if r.kind == PRESENT else None
    on = not (isinstance(policy, dict) and policy.get('keep_signin_file') is False)
    if st is not None:
        with _HOLD_LOCK:
            _MIRROR_SETTING[path] = (st, on)
    return on


def _named(ctx):
    """16 hex of a SHA-256 of the account id and organization id Claude Code's settings name now (read fresh), or None
    when they name no account id or can't be read."""
    cfg_r, block, _ = live_mod.config_identity(ctx)
    uuid, _, org = live_mod.label(block)
    if cfg_r.kind != PRESENT or not uuid:
        return None
    return _sha('%s\n%s' % (uuid, org))[:16]


def _hold_read_note(ctx, anywhere=False):
    """(kind, note) of signin-hold.json. kind: 'ok' with the note {'path', 'tmp', 'stamp' (a tuple, or None for a
    pending note written before the temporary file was made), 'prev' (a pending note's other stamp while a file of
    ours is replaced by a new one: _hold_swap; else None), 'at', 'wrote' (the wall time Claude Code wrote the bytes
    the file holds, or None), 'state' ('pending' or 'settled'), 'said'}; 'none' (no file); 'bad' (it can't be used);
    'other' (a sound note that names another file than Claude Code's plain login file as this process knows it: it is
    another process's, with another folder in its environment, and is left alone). anywhere (the uninstaller, whose
    environment may name another folder than the watch's): any file of that name. A temporary name is used only when
    it is one the hold makes, beside that file. So a damaged note never makes this code remove another file."""
    r, obj = ccfiles.read_json(ctx.path(HOLD_FILE))
    if r.kind == ABSENT:
        return 'none', None
    if r.kind != PRESENT or not isinstance(obj.get('path'), str) or obj.get('state') not in ('pending', 'settled'):
        return 'bad', None
    def four(s):
        return tuple(s) if isinstance(s, list) and len(s) == 4 and \
            all(isinstance(x, int) and not isinstance(x, bool) for x in s) else None
    stamp = four(obj.get('stamp'))
    prev = four(obj.get('prev')) if obj['state'] == 'pending' else None
    path, cred = obj['path'], _cred_path(ctx)
    if (obj['state'] == 'settled' and stamp is None) or not os.path.isabs(path) or \
            os.path.basename(path) != os.path.basename(cred):
        return 'bad', None
    if path != cred and not anywhere:
        return 'other', None
    tmp = obj.get('tmp') if isinstance(obj.get('tmp'), str) and _hold_tmp_of(path, obj['tmp']) else None
    return 'ok', {'path': path, 'tmp': tmp, 'stamp': stamp, 'prev': prev, 'at': _num(obj.get('at')),
                  'wrote': _num(obj.get('wrote')), 'state': obj['state'], 'said': obj.get('said') is True}


def _hold_note(ctx):
    """signin-hold.json as a note this process can use (_hold_read_note), or None."""
    return _hold_read_note(ctx)[1]


def _hold_tmp_name(path):
    """A new temporary name of the hold's, beside the file at path."""
    folder, name = os.path.split(path)
    return os.path.join(folder, '.%s.%s.hold' % (name[:40], secrets.token_hex(6)))


def _hold_tmp_of(path, tmp):
    """tmp is a temporary name the hold makes for the file at path (_hold_tmp_name): beside it, and of that form."""
    folder, name = os.path.split(path)
    head, tail = '.%s.' % name[:40], '.hold'
    base = os.path.basename(tmp)
    mid = base[len(head):-len(tail)]
    return os.path.dirname(tmp) == folder and base.startswith(head) and base.endswith(tail) and \
        len(mid) == 12 and all(c in '0123456789abcdef' for c in mid)


def _hold_write_note(ctx, note, sync=True):
    """Write signin-hold.json. sync=False (a pending note, before the file is in place): without the wait for the
    disk, which would be time Claude Code has no login; the note outlives this process, and a power cut in that
    moment leaves at most a file nobody knows is ours, which is then treated as the file Claude Code wrote."""
    os.makedirs(ctx.data_dir, mode=0o700, exist_ok=True)
    out = dict(note, stamp=list(note['stamp']) if note.get('stamp') else None)
    ccfiles.atomic_write(ctx.path(HOLD_FILE), (json.dumps(out) + '\n').encode('utf-8'), mode=0o600, sync=sync)


def _hold_unlink(path):
    """Remove a name of ours (a temporary file, the note); one already gone is fine."""
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        pass


def _hold_drop_note(ctx, note):
    """Remove the note (and the temporary name it gives) only while it is still the one read (note; None: one that
    can't be used): a process that put a new file of ours in place meanwhile keeps its own, and a sound note of
    another process's folder is never removed."""
    if note is not None:
        _hold_unlink(note['tmp'])
    kind, now = _hold_read_note(ctx)
    if (note is None and kind == 'bad') or (note is not None and kind == 'ok' and now == note):
        _hold_unlink(ctx.path(HOLD_FILE))


def _hold_known(ctx, note, path):
    """The stamps a file at path has when it is the hold's: the one this process put there last, and the note's. A
    pending note has its stamp from the moment the temporary file is made, before the link; one without a stamp was
    written before that, and no file of ours is in place for it. A pending note of a replacement (_hold_swap) knows
    the file it replaces too ('prev'), so whichever of the two is there is ours. (The temporary name is another name
    of the very file put in place, so its own stamp would follow a rewrite of that file by Claude Code: it is never
    asked.)"""
    known = set()
    with _HOLD_LOCK:
        h = _HOLDER.get(ctx.data_dir) or {}
        if h.get('ours') and h.get('path') == path:
            known.add(h['ours'])
    if note is not None and note['path'] == path:
        known.update(s for s in (note['stamp'], note.get('prev')) if s is not None)
    return known


def _hold_fail(ctx):
    with _HOLD_LOCK:
        _HOLD_FAILED[ctx.data_dir] = _HOLD_FAILED.get(ctx.data_dir, 0) + 1


def _hold_facts(ctx, out):
    """out with the counts the record keeps: made (files put back since the last keep), failed (quiet failures)."""
    with _HOLD_LOCK:
        h = _HOLDER.get(ctx.data_dir) or {}
        out.setdefault('made', h.get('made', 0))
        out['failed'] = _HOLD_FAILED.get(ctx.data_dir, 0)
    return out


def _hold_empty(ctx, stamp=None):
    """Empty the holder: no bytes, nothing known as put there (stamp: only when it is still the one known)."""
    with _HOLD_LOCK:
        h = _HOLDER.get(ctx.data_dir) or {}
        if stamp is None or h.get('ours') in (None, stamp):
            _HOLDER[ctx.data_dir] = {'fill': h.get('fill', 0) + 1, 'made': 0, 'ours': None}


def _hold_let_go(ctx, stamp):
    """A file of ours went: the holder no longer knows it (its bytes stay for their HOLD_FOR_S)."""
    with _HOLD_LOCK:
        h = _HOLDER.get(ctx.data_dir)
        if h is not None and h.get('ours') == stamp:
            h['ours'] = None


def hold_seen(ctx, event):
    """The sign-in watch saw Claude Code's plain login file 'appeared', 'changed' or 'gone' (at once, in its own loop).
    'appeared' or 'changed', when the file is not the hold's own: keep the bytes Claude Code wrote (bound 1 above), or
    empty the holder when they are not a full account login. 'gone': put them back within the bounds above; when
    nothing can be put back from memory, a look that answers from the Keychain item may be owed (_hold_late: 'late',
    with 'look_in' 0). Returns plain facts for the record: {'did': 'kept', 'held', 'removed' or None, 'ours': the file
    there is the hold's own, 'made', 'failed'}. No Keychain call, no network, no lock that is waited for, no file over
    HOLD_READ_MAX read; any failure is quiet and counted."""
    path = _cred_path(ctx)
    _hold_expire(ctx)
    if event in ('appeared', 'changed'):
        out = _hold_keep(ctx, path)
    elif event == 'gone':
        out = _hold_put(ctx, path)
        if out.get('did') is None and _hold_late(ctx):
            out.update(late=True, look_in=0.0)
    else:
        out = {'did': None}
    return _hold_facts(ctx, out)


def _hold_expire(ctx):
    """Let go of bytes kept HOLD_FOR_S ago or longer (by either clock): they are never put back after that, and a
    login's text does not stay in this process's memory for nothing."""
    with _HOLD_LOCK:
        h = _HOLDER.get(ctx.data_dir)
        if h is not None and h.get('text') and not (0 <= ctx.mono() - h['at'] < HOLD_FOR_S and
                                                    -HOLD_FOR_S < ctx.wall() - h['wall_at'] < HOLD_FOR_S):
            h.pop('text', None)


def hold_own(ctx, stamp):
    """For live.write_live: the stamp of the plain login file its write of Claude Code's item is about to put in place
    (ccfiles.bump_credentials_file rewrites one that exists). Told before the rename, so the sign-in watch's call for
    that change already knows the write is ours."""
    with _HOLD_LOCK:
        _HOLD_OWN[ctx.data_dir] = tuple(stamp)


def _hold_own_write(ctx, stamp):
    """The plain login file with this stamp was put there by a write of Switchboard's own into Claude Code's item: this
    process's latest (hold_own), one the put-back's record or the last switch's record notes (another process's), or
    any write while a switch is part-way. Such bytes are not what Claude Code's fallback wrote: they are never kept."""
    with _HOLD_LOCK:
        if _HOLD_OWN.get(ctx.data_dir) == stamp:
            return True
    if os.path.exists(switcher.journal_path(ctx)):
        return True
    left = (load_record(ctx).get('written') or {}).get('file')
    return (left is not None and tuple(left) == stamp) or _switch_file(ctx) == stamp


def _hold_keep(ctx, path):
    try:
        st = os.stat(path)
    except OSError:
        return {'did': None}
    stamp = (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)
    if stamp in _hold_known(ctx, _hold_note(ctx), path):
        return {'did': None, 'ours': True}              # the file the hold put there: not a write of Claude Code's
    if _hold_own_write(ctx, stamp):
        # a rewrite by a write of ours into the item (a switch, a put-back, a promote): not kept, and no reason to let
        # go of what Claude Code wrote before it, which keeps its own seconds
        return {'did': None}
    with _HOLD_LOCK:                                    # a refused save: its process deletes the item right after
        _HOLD_ABSENT[ctx.data_dir] = min(ctx.wall(), st.st_mtime_ns / 1e9)
    keep = None
    age = ctx.wall() - st.st_mtime_ns / 1e9
    if hold_on(ctx) and stat.S_ISREG(st.st_mode) and st.st_size <= HOLD_READ_MAX and abs(age) < HOLD_FOR_S:
        text = _hold_read(path)
        login = live_mod.parse(text) if text is not None else None
        named = _named(ctx) if login is not None and login.full_pair else None
        if named is not None and not _hold_anothers(ctx, login):
            keep = {'text': text, 'at': ctx.mono() - max(0.0, age), 'wall_at': st.st_mtime_ns / 1e9, 'named': named,
                    'made': 0, 'path': path, 'ours': None}
    with _HOLD_LOCK:
        fill = (_HOLDER.get(ctx.data_dir) or {}).get('fill', 0) + 1
        _HOLDER[ctx.data_dir] = dict(keep, fill=fill) if keep else {'fill': fill, 'made': 0, 'ours': None}
    # a look once the bytes' time is over: it takes a file of ours away, and lets go of the bytes (hold_tidy)
    return {'did': 'kept', 'look_in': HOLD_FOR_S + HOLD_LOOK_S} if keep else {'did': None}


def _hold_anothers(ctx, login):
    """The saved accounts prove login to be another account's than the one Claude Code's settings name now (by its
    fingerprint or its exact sign-in end, identity.offline: no Keychain, no network). A process that read the item
    before a switch can write that login to the plain login file after it; put back, it would run Claude Code on the
    account the switch left. False when nothing proves it, and when the saved accounts can't be read."""
    try:
        doc = vault.load(ctx)
    except vault.VaultError:
        return False
    block = live_mod.config_identity(ctx)[1]
    who = identity.offline(login, doc, block).account_id
    return who is not None and who != identity.label_id(doc, block)


def _hold_read(path):
    """A file's text, at most HOLD_READ_MAX bytes of it (None when longer, not there, or not UTF-8)."""
    try:
        with open(path, 'rb') as f:
            raw = f.read(HOLD_READ_MAX + 1)
    except OSError:
        return None
    if len(raw) > HOLD_READ_MAX:
        return None
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return None


def _hold_put(ctx, path):
    with _HOLD_LOCK:
        h = dict(_HOLDER.get(ctx.data_dir) or {})
    if not h.get('text'):
        return {'did': None}
    if not (0 <= ctx.mono() - h['at'] < HOLD_FOR_S and -HOLD_FOR_S < ctx.wall() - h['wall_at'] < HOLD_FOR_S):
        with _HOLD_LOCK:                                # past its time: the bytes are let go
            cur = _HOLDER.get(ctx.data_dir)
            if cur is not None and cur.get('fill') == h['fill']:
                cur.pop('text', None)
        return {'did': None}
    if h['made'] >= HOLD_MAX:
        return {'did': None}
    if not hold_on(ctx) or os.path.exists(switcher.journal_path(ctx)) or _signing_in(ctx, ctx.wall()):
        return {'did': None}
    if not h.get('named') or _named(ctx) != h['named']:
        _hold_empty(ctx)                                # the settings name nobody, or another account
        return {'did': None}
    with _HOLD_FILES:
        with _HOLD_LOCK:
            emptied = (_HOLDER.get(ctx.data_dir) or {}).get('fill') != h['fill']
        if emptied or os.path.lexists(path):
            return {'did': None}                        # a look emptied the holder meanwhile, or a file is there
        stamp = _hold_make(ctx, path, h['text'], h['wall_at'])
        if stamp is None:
            return {'did': None}
        with _HOLD_LOCK:
            cur = _HOLDER.get(ctx.data_dir)
            same = cur is not None and cur.get('fill') == h['fill']
            if same:
                cur['made'] = cur.get('made', 0) + 1
                cur['ours'] = stamp
    # the settings again, after the link (parsed outside the file lock): a sign-out or a sign-in that changed them
    # meanwhile, or a look that emptied the holder meanwhile, gets no file of ours
    after = _named(ctx)
    _hold_gave(ctx, h['text'])
    if not same or after != h['named']:
        with _HOLD_FILES:
            _hold_remove(ctx, path, stamp)
            _hold_drop_note(ctx, _hold_note(ctx))
        _hold_empty(ctx)
        return {'did': 'removed', 'why': 'nobody' if after is None else 'another'}
    return {'did': 'held', 'look_in': max(0.0, HOLD_TIDY_S - (ctx.wall() - h['wall_at'])) + HOLD_LOOK_S}


def _hold_gave(ctx, text):
    """Note, in this process only, what a file the hold just put in place holds (hashes and numbers; _HOLD_GAVE): for
    the look that finds that login in Claude Code's item after a sign-out (_maybe_take_out)."""
    from . import facts
    cfg_path = facts.global_config(ctx.env, ctx.home)
    user, keys = _settings_mark(cfg_path)
    gave = {'at': ctx.wall(), 'sha': _sha(text), 'login_sha': _login_sha(live_mod.parse(text)),
            'settings_size': _settings_size(cfg_path), 'settings_user': user, 'settings_keys': keys}
    with _HOLD_LOCK:
        _HOLD_GAVE[ctx.data_dir] = gave


def _hold_make(ctx, path, text, wrote=None):
    """Put text in place as a new file at path, only if none is there; its stamp, or None (quiet; counted unless a file
    was there first). wrote: the wall time Claude Code wrote these bytes, for the note. The note goes first (pending,
    naming the temporary file), again with the temporary file's stamp before the link (the file put in place has that
    very stamp, so a process stopped right after the link leaves a file the note knows exactly), the settled note
    after the link, the temporary name last; a failure on the way leaves nothing of ours behind. The two pending notes
    do not wait for the disk: until the link, Claude Code has no login."""
    tmp = _hold_tmp_name(path)
    note = {'path': path, 'tmp': tmp, 'stamp': None, 'at': ctx.wall(), 'wrote': wrote, 'state': 'pending'}
    try:
        _hold_write_note(ctx, note, sync=False)
    except (OSError, ccfiles.CCFileError):
        _hold_fail(ctx)
        return None
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            view = memoryview(text.encode('utf-8'))
            while view:
                view = view[os.write(fd, view):]
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        st = os.stat(tmp)
        stamp = (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)
        _hold_write_note(ctx, dict(note, stamp=stamp), sync=False)
        _link(tmp, path)                                # create only: a file there now is Claude Code's and stays
    except (OSError, ccfiles.CCFileError) as e:
        _hold_unlink(tmp)
        _hold_unlink(ctx.path(HOLD_FILE))
        if not isinstance(e, FileExistsError):
            _hold_fail(ctx)
        return None
    try:
        _hold_write_note(ctx, dict(note, stamp=stamp, state='settled'))
    except (OSError, ccfiles.CCFileError):
        _hold_fail(ctx)
        _hold_remove(ctx, path, stamp)
        _hold_unlink(tmp)
        _hold_unlink(ctx.path(HOLD_FILE))
        return None
    _hold_unlink(tmp)
    return stamp


def _hold_remove(ctx, path, stamp):
    """Remove the file at path only while its stamp is still stamp (compared, then unlinked: a file Claude Code wrote
    since is its own and stays). True when removed."""
    if stamp is None or _stamp(path) != stamp:
        return False
    try:
        os.unlink(path)
    except FileNotFoundError:
        return False
    except OSError:
        _hold_fail(ctx)
        return False
    _hold_let_go(ctx, stamp)
    return True


def hold_tidy(ctx, lr=None, start=False, repair=False):
    """Take away a plain login file the hold put there, when it serves nobody or must not stay: Claude Code's settings
    name nobody, or (known to this process's holder) another account or organization than when its bytes were kept:
    removed, and the holder emptied; lr (a look's live read) finds the Keychain item there, with or without a login,
    and the file's bytes are at least HOLD_TIDY_S old: removed (younger, it stays: the item may be deleted a
    moment after that read, and the file is then the one login left; with repair, the look's word that it will put
    an item without a login back from this file, it stays up to HOLD_KEEP_S); start (the sign-in watch's start, no
    holder): removed unless its bytes are a full login of the account the settings name. A note that matches no file
    goes, with a temporary name of ours left beside the file. One that stays past HOLD_KEEP_S while it keeps Claude
    Code signed in is said once (log, sign-in record). An lr that says "no such item" starts the HOLD_TIDY_S of a file
    of ours there again (_hold_absent). Returns {'did': 'removed' or None, 'why', 'ours', 'look_in'
    (seconds until a look can take a young file of ours away; a few seconds for one that is old enough and still
    there)}. Never removes a file Claude Code wrote."""
    _hold_expire(ctx)
    with _HOLD_LOCK:
        kept = bool((_HOLDER.get(ctx.data_dir) or {}).get('text'))
    # Claude Code's settings (megabytes at times) are parsed outside the hold's file lock, and only while there is
    # something to take away: bytes kept, or a note of a file put back
    noted = os.path.lexists(ctx.path(HOLD_FILE))
    standing = noted and mirror_on(ctx)
    # the standing copy keeps a note at every look: then the parse is the one every look already makes, read again
    # whenever the file's inode, time or size moved (Claude Code replaces it with a rename at each write)
    cfg = live_mod.config_identity(ctx, cached=standing and not kept)[:2] if kept or noted else None
    keeps = standing and _mirror_keeps(ctx, lr, cfg)
    with _HOLD_FILES:
        return _hold_tidy(ctx, lr, start, cfg, repair, standing, keeps)


def _hold_young(ctx, note, limit=HOLD_TIDY_S):
    """Seconds until the bytes in the file of ours that note describes are limit old, counted from the moment Claude
    Code wrote them (the note's 'wrote'; the put-back's own time for a note without it), or None when they are that
    old already, or their age is not known. A time a little past the clock (it was set back) counts as now; one far
    past it is not believed."""
    since = None if note is None else note['wrote'] if note['wrote'] is not None else note['at']
    if since is None:
        return None
    age = ctx.wall() - since
    if -HOLD_TIDY_S < age < 0:
        return limit
    return limit - age if 0 <= age < limit else None


def _hold_tidy(ctx, lr, start, cfg=None, repair=False, standing=False, keeps=False):
    """hold_tidy under the hold's file lock. standing: the standing copy is on (mirror_on); keeps: lr finds what the
    standing copy is kept for (_mirror_keeps), so the file's age is no reason to take it away."""
    path = _cred_path(ctx)
    note = _hold_note(ctx)
    stamp = _stamp(path)
    ours = stamp is not None and stamp in _hold_known(ctx, note, path)
    if lr is not None and not lr.read.degraded and (lr.read.kind == ABSENT or (lr.read.kind == PRESENT and
                                                                            lr.read.provenance == 'file')):
        now_note = _hold_absent(ctx, note if ours else None)    # the Keychain said "no such item"
        note = now_note if ours else note
    with _HOLD_LOCK:
        h = dict(_HOLDER.get(ctx.data_dir) or {})
    reason = block = None
    if ours or h.get('text'):
        # naming nobody, or another account than the bytes were kept with, ends both at once
        cfg_r, block = cfg if cfg is not None else live_mod.config_identity(ctx)[:2]
        uuid, _, org = live_mod.label(block)
        if _names_nobody(cfg_r, block):
            reason = 'nobody'
        elif h.get('named') and (h.get('text') or h.get('ours') == stamp) and cfg_r.kind == PRESENT and \
                _sha('%s\n%s' % (uuid, org))[:16] != h['named']:
            reason = 'another'
        if reason is not None:
            _hold_empty(ctx)
    if not ours:
        # the file was removed or replaced since (or never put in place): nothing of ours is there. A pending note is
        # the trace of a put-back stopped part-way only once it is HOLD_PENDING_S old (or at the watch's start: only
        # the watch puts files back); younger, another process may be putting one in place this moment
        young = note is not None and note['state'] == 'pending' and not start and note['at'] is not None and \
            0 <= ctx.wall() - note['at'] < HOLD_PENDING_S
        if not young and (note is not None or os.path.lexists(ctx.path(HOLD_FILE))):
            _hold_drop_note(ctx, note)
        with _HOLD_LOCK:
            cur = _HOLDER.get(ctx.data_dir)
            if cur is not None and cur.get('ours') is not None and cur.get('ours') != stamp:
                cur['ours'] = None
        return {'did': None, 'why': reason}
    item = lr is not None and lr.read.kind == PRESENT and lr.read.provenance == 'keychain' and not lr.read.degraded
    young = _hold_young(ctx, note, HOLD_KEEP_S if item and repair else HOLD_TIDY_S)
    if reason is None and item and young is None and not keeps:
        reason = 'item'
    if reason is None and start and h.get('ours') != stamp and not _hold_theirs(ctx, path, block):
        reason = 'start'
    if reason is None:
        _hold_stays(ctx, note, stamp, lr)
        out = {'did': None, 'ours': True}
        if (item and repair) or (standing and young is None):
            # the look that puts the item back from it paces itself; and once its HOLD_TIDY_S are over the standing
            # copy is never taken away for its age: a look that finds it serves nobody any more takes it away
            # (every change of the Keychain item or of the file brings one), so no look is asked for here
            return out
        if young is not None:
            out['look_in'] = young + HOLD_LOOK_S
        else:
            # old enough to go, and this call could not take it away (it read no item, or the item is missing): a
            # look is asked for again, so the file never waits for the next regular look
            old = _hold_young(ctx, note, HOLD_KEEP_S) is None
            out['look_in'] = HOLD_AGAIN_LATER_S if old else HOLD_AGAIN_S
        return out
    removed = _hold_remove(ctx, path, stamp)
    _hold_drop_note(ctx, note)
    return {'did': 'removed' if removed else None, 'why': reason}


def _hold_theirs(ctx, path, block):
    """The bytes of a file of ours are a full account login of the account Claude Code's settings name, by the proof the
    promote takes (_file_is_theirs: its own, or an older generation of its sign-in)."""
    text = _hold_read(path)
    login = live_mod.parse(text) if text is not None else None
    if login is None or not login.full_pair:
        return False
    try:
        doc = vault.load(ctx)
    except vault.VaultError:
        return False
    aid = identity.label_id(doc, block) if block else None
    if aid is None:
        return False
    return _file_is_theirs(login, doc, block, aid, _recorded(doc['accounts'][aid]), PRESENT) in ('theirs', 'older')


def _hold_stays(ctx, note, stamp, lr):
    """A file of ours that stays: a pending note is settled (it was put in place by a process stopped before it could
    say so) and its temporary name removed; past HOLD_KEEP_S, while Claude Code reads its login from it (the Keychain
    item missing), it is said once, in the log and the sign-in record."""
    if note is None:
        return
    if note['state'] == 'pending':
        try:
            _hold_write_note(ctx, dict(note, stamp=stamp, state='settled', at=ctx.wall()))
        except (OSError, ccfiles.CCFileError):
            _hold_fail(ctx)
            return
        _hold_unlink(note['tmp'])
        return
    if note['tmp'] and os.path.lexists(note['tmp']):
        _hold_unlink(note['tmp'])                       # a process stopped before it removed its temporary name
    from_file = lr is not None and lr.read.kind == PRESENT and lr.read.provenance == 'file' and not lr.read.degraded
    if note['said'] or not from_file or note['at'] is None or ctx.wall() - note['at'] < HOLD_KEEP_S:
        return
    try:
        _hold_write_note(ctx, dict(note, said=True))
    except (OSError, ccfiles.CCFileError):
        return
    ctx.log(HOLD_STAYS_LINE)
    try:
        from . import signin_watch
        signin_watch.Record(ctx.path(signin_watch.RECORD)).add('hold', did='stays')
    except Exception:  # noqa: BLE001 - the log line stands; the record is a best effort
        pass


def hold_drop(ctx, env=None):
    """For live.write_live, right after its write of Claude Code's item landed (under Claude Code's save lock): when
    Claude Code's plain login file is one the hold put there, it is removed instead of rewritten (the item holds the
    login now), with its note; with the standing copy on (mirror_on) and the settings naming an account, it is left
    as it is instead, for the caller to bring up to the item (mirror_put: a switch and its roll back; a take-back
    meets settings that name nobody). True when write_live must not touch the file (removed, or left for the caller);
    False when the file is not ours (or became Claude Code's a moment before), and it is rewritten as before."""
    env = ctx.env if env is None else env
    from . import facts
    path = facts.credentials_file(env, ctx.home)
    if path != _cred_path(ctx):
        return False
    standing = mirror_on(ctx) and _named(ctx) is not None       # the settings are parsed outside the file lock
    with _HOLD_FILES:
        note = _hold_note(ctx)
        stamp = _stamp(path)
        if stamp is None or stamp not in _hold_known(ctx, note, path):
            return False
        if standing:
            return True
        if not _hold_remove(ctx, path, stamp):
            return False
        _hold_drop_note(ctx, note)
    return True


def hold_adopt(ctx, path, stamp):
    """For the promote and the put-back, once its write of Claude Code's item landed and read back: Claude Code's
    plain login file, which holds the login now in the item, becomes one the hold takes away (hold_tidy) instead of
    being removed now: once the item has been back for HOLD_TIDY_S (counted from now: the note's 'wrote'), at once
    when the settings name nobody, by a switch's write, at uninstall. stamp: the file's stamp when the write landed; a
    file written since is Claude Code's and is left to it. True when the note is written (the file is now known as
    one to take away); False leaves the caller to remove it."""
    if stamp is None or path != _cred_path(ctx):
        return False
    stamp = tuple(stamp)
    with _HOLD_FILES:
        if _stamp(path) != stamp:
            return False
        old = _hold_note(ctx)
        at = ctx.wall()
        try:
            _hold_write_note(ctx, {'path': path, 'tmp': None, 'stamp': stamp, 'at': at, 'wrote': at,
                                   'state': 'settled'})
        except (OSError, ccfiles.CCFileError):
            _hold_fail(ctx)
            return False
        if old is not None:
            _hold_unlink(old['tmp'])                # a temporary name the note before this one still named
    return True


UNINSTALL_TRIES = 3             # the uninstaller's reads of Claude Code's item, when one gets no answer...
UNINSTALL_AGAIN_S = 3.0         # ... this far apart


def hold_uninstall(ctx, told=None):
    """For the uninstaller (signin_watch --tidy), once the watch, the page and the loop are stopped: a plain login file
    the hold put there, known by the note's stamp, is removed, with a temporary name of ours and the note. One file
    stays: the one Claude Code is reading its sign-in from (its Keychain says its item is not there, and the file
    holds a full login of the account its settings name). A read that gets no answer (on a busy Mac, a locked
    Keychain) is tried again, UNINSTALL_TRIES times UNINSTALL_AGAIN_S apart; when it still gets none, the file and its
    note stay, a line asks for the uninstaller to be run again, and told (a dict, when given) gets 'again': True
    (signin_watch --tidy exits EXIT_AGAIN then). Its bytes are what Claude Code wrote, it is the
    only sign-in Claude Code has, and Claude Code removes it itself the next time it saves its login; a line says so.
    One in another Claude Code folder than this process sees (the note names it) can't be judged, and stays too.
    Never a file Claude Code wrote. The standing copy's first History line is forgotten (_mirror_forget). Returns
    plain lines of what was done."""
    lines = []
    _mirror_forget(ctx)
    kind, note = _hold_read_note(ctx, anywhere=True)
    if note is None:
        if kind == 'bad':
            _hold_unlink(ctx.path(HOLD_FILE))
        return lines
    path = note['path']
    home = os.path.join(ctx.home, '')
    shown = '~/' + path[len(home):] if path.startswith(home) else path
    stamp = _stamp(path)
    if stamp is not None and stamp in _hold_known(ctx, note, path):
        if path != _cred_path(ctx):
            lines.append("Left the plain login file Switchboard had kept for Claude Code where it is (%s): it is "
                         "in another Claude Code folder than this command sees. Claude Code removes it itself the "
                         "next time it saves its login." % shown)
            return lines
        cfg_r, block = live_mod.config_identity(ctx)[:2]
        # only a full login of the account the settings name can be the sign-in Claude Code is using: a file reads that
        # succeeded prove to be anything else goes whatever the Keychain answers
        sign_in = _may_be_the_sign_in(ctx, path, cfg_r, block)
        absent = False
        if sign_in:
            lr = live_mod.read_live(ctx, fresh=True)
            for _ in range(UNINSTALL_TRIES - 1):
                if lr.read.kind != UNREADABLE and not lr.read.degraded:
                    break
                ctx.sleep(UNINSTALL_AGAIN_S)
                lr = live_mod.read_live(ctx, fresh=True)
            if lr.read.kind == UNREADABLE or lr.read.degraded:
                # no answer is not "not there": the file and its note stay, so the next run can judge it
                lines.append("Could not read Claude Code's Keychain item just now, so the plain login file "
                             "Switchboard keeps for Claude Code was left where it is (%s), with Switchboard's note of "
                             "it. Run the uninstaller again to finish." % shown)
                if told is not None:
                    told['again'] = True
                return lines
            absent = lr.read.kind == ABSENT or (lr.read.kind == PRESENT and lr.read.provenance == 'file')
        if sign_in and absent:
            lines.append("Left Claude Code's plain login file where it is (%s): Claude Code's Keychain item is "
                         "missing, so this file is the sign-in it is using. Claude Code removes it itself the next "
                         "time it saves its login." % shown)
        else:
            with _HOLD_FILES:
                if _hold_remove(ctx, path, stamp):
                    lines.append("Removed the plain login file Switchboard had kept for Claude Code (%s)." % shown)
    with _HOLD_FILES:
        _hold_unlink(note['tmp'])
        _hold_unlink(ctx.path(HOLD_FILE))
    return lines


def _may_be_the_sign_in(ctx, path, cfg_r, block):
    """For the uninstaller: the file of ours at path may be the sign-in Claude Code is using. False only when reads
    that succeeded prove it is not: the settings name nobody, the file holds no full login, or it is not a login of the
    account the settings name (_file_is_theirs). Settings or saved accounts that can't be read, settings naming an
    account Switchboard does not keep, or a file that can't be read: not known, so True (the Keychain decides)."""
    if cfg_r.kind != PRESENT:
        return True
    if _names_nobody(cfg_r, block):
        return False
    text = _hold_read(path)
    if text is None:
        return True
    login = live_mod.parse(text)
    if not login.full_pair:
        return False
    try:
        doc = vault.load(ctx)
    except vault.VaultError:
        return True
    aid = identity.label_id(doc, block) if block else None
    if aid is None:
        return True
    return _file_is_theirs(login, doc, block, aid, _recorded(doc['accounts'][aid]), PRESENT) in ('theirs', 'older')


def _mirror_forget(ctx):
    """For the uninstaller: the standing copy's first History line is said again after the next install."""
    if load_record(ctx).get('mirror_said'):
        _change_record(ctx, lambda r: r.update(mirror_said=False), ctx.wall())


# ---------- a spare copy of the sign-in in Claude Code's plain login file ----------
#
# A Claude Code process trusts each read of its Keychain item for 30 s (as of 2.1.329 to 2.1.332),
# a "no such item" answer too, and while it holds that answer it reads its login from the plain login file at every
# request. A request that finds no login there fails at once, is not tried again, and puts that chat on its sign-in
# screen until the person presses Cancel. So once the sign-in is back in the item, the processes that asked while it
# was gone still read the file for up to 30 s more. With the setting hold_signin_file on, three steps keep the sign-in
# there, each holding Claude Code's save lock, so no save of Claude Code's comes between the read of the item and the
# file:
#   - a put-back leaves one (_try_once): made just before its write when no file is there, the file that is there kept
#     otherwise, and handed to the hold once the write has read back;
#   - a removal of the file that the hold could not answer from memory is answered from the item (_late), in the
#     sign-in watch's process, while the item was known absent less than HOLD_TIDY_S ago (a fallback write of Claude
#     Code's the hold saw, a look that read "no such item", a put-back, a promote). A process whose save landed after
#     it had read "no such item" removes the file, and that can come seconds after the hold's HOLD_FOR_S. It is never
#     answered from bytes kept in memory past HOLD_FOR_S: after a renewal they are older than the item, and a sign-out
#     chosen in those seconds would be answered by a file put back;
#   - a file of ours follows the item (_follow): a process that renews while its Keychain read answers "no such item",
#     "empty", "unparsable" or "locked" takes its refresh token from this file (the renewal's own fresh read falls
#     through to it then). One that a renewal by another process has spent is refused, and Claude Code then blanks
#     its login if the stored refresh token is still the refused one, as a fresh read of this file at that moment
#     finds it. So a look that finds in the item a full login of the same account other than the file's brings the
#     file to the item's exact text, and a file beside another account's login goes.
# A spare holds exactly the text in the item at that moment: nothing is stripped from it (a file without a refresh
# token would be saved back into the item by the next process whose read falls through to it). It is made create-only
# and owner-only, as the hold makes its files (_hold_make), and is then a file of ours, taken away by the hold's rules:
# by a look that finds the item once its note's 'wrote' (when the item was last known absent; a look that reads "no
# such item" while it is there starts it again) is HOLD_TIDY_S old, at once when the settings name nobody, by a
# switch's write, at the sign-in watch's start, by the uninstaller; never a file Claude Code wrote since. A step that
# finds Claude Code's save lock held does not wait for it: the look asks for another one in SPARE_AGAIN_S.
SPARE_AGAIN_S = 1.0
SPARE_LATE_LINE = ("Claude Code's plain login file was removed while Claude Code processes may still read their "
                   "sign-in from it; Switchboard put the sign-in in its Keychain item back in that file")
FOLLOW_LINE = ("Claude Code's login in its Keychain item changed while Switchboard kept its plain login file; "
               "Switchboard brought the file up to it")
FOLLOW_GONE_LINE = ("Claude Code's Keychain item holds another account's login; Switchboard took away the plain login "
                    "file it kept for Claude Code")
# data folder -> {'at': the wall time Claude Code's plain login file went, 'absent': when the Keychain item was last
# known absent then}: a removal the hold could not answer from memory, owed an answer by a look of this process
_HOLD_LATE = {}
# data folder -> the wall time of the last fallback write of Claude Code's the hold saw, or of the last look that read
# "no such item"
_HOLD_ABSENT = {}
_HOLD_DID = {}                  # data folder -> (did, why) of the last step a look of this process took with the file


def _spare_make(ctx, text):
    """The spare, made just before a put-back's write: a new plain login file holding text, only when no file is
    there (create-only and owner-only, with the note that names it the hold's: _hold_make). Its stamp, or None (a
    file is there, or it could not be made: quiet)."""
    path = _cred_path(ctx)
    with _HOLD_FILES:
        if os.path.lexists(path):
            return None
        return _hold_make(ctx, path, text, ctx.wall())


def _spare_drop(ctx, stamp):
    """Take away a spare (stamp: its stamp; None for none) while it is still that very file, with its note. A file
    Claude Code wrote since stays."""
    if stamp is None:
        return
    path = _cred_path(ctx)
    with _HOLD_FILES:
        _hold_remove(ctx, path, stamp)
        note = _hold_note(ctx)
        if note is not None and note['stamp'] == stamp:
            _hold_drop_note(ctx, note)


def _spare_said(ctx, did, why):
    """Note the step a look of this process took with the plain login file, for the sign-in watch's record: 'spare'
    (why 'put_back' or 'gone'), 'followed', or 'removed' (why 'another')."""
    with _HOLD_LOCK:
        _HOLD_DID[ctx.data_dir] = (did, why)


def hold_spared(ctx):
    """For the sign-in watch's call after a look (api.watch_hold('looked')): {'did', 'why'} of the step that look took
    with the plain login file (_spare_said), said once; {} when it took none."""
    with _HOLD_LOCK:
        d = _HOLD_DID.pop(ctx.data_dir, None)
    return {'did': d[0], 'why': d[1]} if d else {}


def _absent_at(ctx):
    """The wall time Claude Code's Keychain item was last known absent, or None: the last fallback write of Claude
    Code's this process's hold saw (the process whose save was refused deletes the item right after it) and the last
    look of this process that read "no such item" (_hold_absent), the last put-back or promote of any process (the
    put-back record's 'wrote_at'), and the 'wrote' of the note of a file of ours (a put-back, a promote, a spare made
    from the item, Claude Code's own write the hold put back, or a look that read "no such item" beside it)."""
    with _HOLD_LOCK:
        seen = _HOLD_ABSENT.get(ctx.data_dir)
    note = _hold_note(ctx)
    times = [t for t in (seen, load_record(ctx).get('wrote_at'), note['wrote'] if note is not None else None)
             if t is not None]
    return max(times) if times else None


def _hold_absent(ctx, note):
    """A look's read said "no such item" now (hold_tidy): noted in this process (_absent_at), and a file of ours there
    (note: its note, as the caller read it; None when there is none) counts its HOLD_TIDY_S again from now, by its
    note's 'wrote': the processes that asked now read the file for 30 s. The note is rewritten at most once a second,
    without waiting for the disk (it only moves a time later: a write lost at a power cut costs no more than a tidy look
    that comes sooner), and only while it is still the one read (its path, stamp, 'prev' and state): another process
    that put a new file of ours in place meanwhile keeps its note, and so its file. Returns the note to go on from (the
    caller's own when nothing was written)."""
    now = ctx.wall()
    with _HOLD_LOCK:
        _HOLD_ABSENT[ctx.data_dir] = now
    if note is None or (note['wrote'] is not None and 0 <= now - note['wrote'] < 1.0):
        return note
    cur = _hold_note(ctx)
    if cur is None or any(cur.get(k) != note.get(k) for k in ('path', 'stamp', 'prev', 'state')):
        return note
    new = dict(cur, wrote=now)
    try:
        _hold_write_note(ctx, new, sync=False)
    except (OSError, ccfiles.CCFileError):
        _hold_fail(ctx)
        return note
    return new


def _hold_late(ctx):
    """For hold_seen('gone') when the hold put nothing back from memory: when the Keychain item was last known absent
    less than HOLD_TIDY_S ago (_absent_at), processes told "no such item" then may still read the file, so a look is
    owed that answers from the item (_late). True when one is owed. Files only: no Keychain call, no lock."""
    if not hold_on(ctx):
        return False
    t = _absent_at(ctx)
    now = ctx.wall()
    if t is None or not -HOLD_TIDY_S < now - t < HOLD_TIDY_S:   # a time a little past the clock counts as now
        return False
    with _HOLD_LOCK:
        _HOLD_LATE[ctx.data_dir] = {'at': now, 'absent': t}
    return True


def _late_done(ctx, late):
    """The removal late was answered: no longer owed (a newer one noted meanwhile stays)."""
    with _HOLD_LOCK:
        if _HOLD_LATE.get(ctx.data_dir) is late:
            del _HOLD_LATE[ctx.data_dir]


def _late(ctx, now, out, went):
    """A look's answer to a removal of the plain login file the hold could not answer from memory (_hold_late), in the
    sign-in watch's process: holding Claude Code's save lock, a fresh read of the item; when it holds a full login of
    the account the settings name and no file is there, the spare is made from its exact text (_spare_from). An item
    that is absent or holds no login is left to the put-back's own rules, which leave a spare. went: the read the look
    already has (_straight's: that lock is let go now), or None. Returns the read the look goes on from: the one made
    here under the save lock, else went. Nothing is waited for: with the save lock held, with a read already made, or
    while this process holds that lock for no read (STRAIGHT_OFF_S), out['again'] asks for another look; past
    HOLD_TIDY_S after the item was last absent the removal is no longer answered."""
    with _HOLD_LOCK:
        late = _HOLD_LATE.get(ctx.data_dir)
    if late is None:
        return went
    if not -HOLD_TIDY_S < now - late['absent'] < HOLD_TIDY_S or not hold_on(ctx):
        _late_done(ctx, late)
        return went
    with _SAID_LOCK:
        off = _STRAIGHT_OFF.get(ctx.data_dir)
    wait = STRAIGHT_OFF_S - (ctx.mono() - off) if off is not None and 0 <= ctx.mono() - off < STRAIGHT_OFF_S else None
    if went is not None or wait is not None:
        out['again'] = SPARE_AGAIN_S if wait is None else max(SPARE_AGAIN_S, wait)
        return went
    # Claude Code's settings (megabytes at times) and the saved accounts are read before its save lock is taken: that
    # lock is held for the read of the item and the file operations only
    cfg_r, block, _ = live_mod.config_identity(ctx)
    try:
        doc = vault.load(ctx)
    except vault.VaultError:
        doc = None
    made = None
    try:
        with live_mod.storage_lock(ctx, budget_s=0.0):
            cur = live_mod.read_live(ctx, fresh=True, interactive=True)
            answered = cur.read.kind != UNREADABLE and not cur.read.degraded
            if answered:
                made = _spare_from(ctx, cur, now, late['absent'], block if cfg_r.kind == PRESENT else None, doc)
    except ccfiles.LockBusy:
        out['again'] = SPARE_AGAIN_S
        return None
    if not answered:
        with _SAID_LOCK:                                # that lock is not held again for a read for a while
            _STRAIGHT_OFF[ctx.data_dir] = ctx.mono()
        out['again'] = STRAIGHT_OFF_S
        return cur
    if made is None and _late_unproven(ctx, cur, block if cfg_r.kind == PRESENT else None, doc):
        return cur                                      # still owed: the keep pass may prove it at this look (_after_keep)
    _late_done(ctx, late)
    if made is not None:
        _spare_said(ctx, 'spare', 'gone')
        ctx.log(SPARE_LATE_LINE)
    return cur


def _late_unproven(ctx, cur, block, doc):
    """_late made no spare only because the item's full login can't be proven yet to be the account the settings name:
    no saved account's proof says whose it is (a new sign-in, such as Claude Code's own /login, that the keep pass has
    not saved). The removal then stays owed while HOLD_TIDY_S lasts, so the look after the keep answers it. A login
    proven to be another account's, a file already there, a switch part-way or settings that can't be read: False."""
    if doc is None or not block or not (cur.usable and cur.read.provenance == 'keychain' and cur.login is not None and
                                        cur.login.full_pair):
        return False
    if os.path.lexists(_cred_path(ctx)) or os.path.exists(switcher.journal_path(ctx)):
        return False
    aid = identity.label_id(doc, block)
    if aid is None or identity.offline(cur.login, doc, block).account_id is not None:
        return False
    return _file_is_theirs(cur.login, doc, block, aid, _recorded(doc['accounts'][aid]), PRESENT) is None


def _after_keep(ctx, out):
    """watch_look's keep pass saved a new login of the account the settings name (_keep_after): the steps that wait for
    a login's proof are made again at once, now that it is saved, so a process told "no such item" a moment ago finds
    the login in the plain login file at this look, not the next: the answer to a removal of that file still owed
    (_late) and, while it is on, the standing copy (_mirror). Each takes Claude Code's save lock without waiting; held,
    out['again'] asks for the next look. Returns the soonest look asked for, or None."""
    again = {}
    cur = _late(ctx, ctx.wall(), again, None)
    lr = cur if cur is not None else out.get('lr')
    _mirror(ctx, lr, again)
    return again.get('again')


def _spare_from(ctx, cur, now, absent, block, doc):
    """Holding Claude Code's save lock, cur a fresh read of the item: the spare made from the item's exact text, only
    when the item holds a full login of the account the settings name (proven as a promote proves the file's login,
    _file_is_theirs, and not older than its saved copy), no switch is part-way, no sign-in is under way on the page
    and no plain login file is there; the settings are read again after it, and a change in that moment takes it away.
    absent: when the item was last known absent, the note's 'wrote': the processes it is for asked then, so it goes
    HOLD_TIDY_S after that, not after now. block and doc: the identity block the settings hold (falsy when they can't
    be read) and the saved accounts (None when they can't be read), both read before the save lock was taken. Its
    stamp, or None."""
    if not (cur.usable and cur.read.provenance == 'keychain' and cur.login is not None and cur.login.full_pair):
        return None
    if os.path.exists(switcher.journal_path(ctx)) or _signing_in(ctx, now):
        return None
    aid = identity.label_id(doc, block) if doc is not None and block else None
    if aid is None or _file_is_theirs(cur.login, doc, block, aid, _recorded(doc['accounts'][aid]), PRESENT) != \
            'theirs':
        return None
    uuid, _, org = live_mod.label(block)
    path = _cred_path(ctx)
    with _HOLD_FILES:
        if os.path.lexists(path):
            return None
        stamp = _hold_make(ctx, path, cur.read.value, min(absent, ctx.wall()))
    if stamp is not None and _named(ctx) != _sha('%s\n%s' % (uuid, org))[:16]:
        _spare_drop(ctx, stamp)                         # the settings changed in that moment
        return None
    return stamp


def _same_login(a, b):
    """Two parsed logins hold the same account login: the same access and refresh tokens, and the same access end
    (the rest of the item's text changes at every save of Claude Code's and is not compared)."""
    return a is not None and b is not None and a.kind == 'oauth' and b.kind == 'oauth' and \
        (a.access_token, a.refresh_token, a.expires_ms) == (b.access_token, b.refresh_token, b.expires_ms)


def _one_account(item, filed, doc, block):
    """The login in the item (item) and the one in a file of ours (filed) are one account's: the same refresh token;
    or the file's login is proven, offline, to be a saved account's (identity.offline: a fingerprint or an exact
    sign-in end) and the item's is proven to be that same account's, or, proven to be nobody's, is a renewal of the
    file's sign-in (identity.renewal_of: its end stamp kept or a little lower, never higher). The end stamps alone
    prove nothing more: two accounts signed in a minute apart sit as close. Anything else is another account's, or
    can't be told, and the file goes. doc: the saved accounts, None when they can't be read."""
    if item.refresh_token is not None and item.refresh_token == filed.refresh_token:
        return True
    pf = identity.offline(filed, doc, block).account_id if doc is not None else None
    if pf is None:
        return False
    pi = identity.offline(item, doc, block).account_id
    if pi is not None:
        return pi == pf
    return identity.renewal_of(item.refresh_expires_ms, filed.refresh_expires_ms)


def _ours_now(ctx, path):
    """(stamp, note, text) of the plain login file at path when it is a file of ours (by its stamp), else None. One
    stat, the note, and a read of at most HOLD_READ_MAX bytes."""
    stamp = _stamp(path)
    if stamp is None:
        return None
    note = _hold_note(ctx)
    if stamp not in _hold_known(ctx, note, path):
        return None
    return stamp, note, _hold_read(path)


def _follow(ctx, lr, out):
    """At every look (lr, its live read): when a file of ours is there and the item holds a full account login other
    than the file's (_same_login), holding Claude Code's save lock, a fresh read of the item (_follow_once): the file
    gets the item's exact text when it is the same account's login, and goes beside another account's. With the
    standing copy on (mirror_on), any other text than the item's is brought up to it (its other parts too: a process
    whose Keychain read finds nothing saves the file's whole object back into the item), a change of the other parts
    alone only once the file is MIRROR_PARTS_S old and never on a busy Mac (keychain.busy_mac), never to a login whose
    refresh token the saved accounts have struck, and never through a folder or file that is a link or not this
    user's (_mirror_safe). An item that is absent, holds no login or can't be read leaves the hold's other rules to
    stand. The save lock is never waited for: out['again'] asks for another look."""
    if lr is None or not (lr.usable and lr.read.provenance == 'keychain') or lr.login is None or \
            not lr.login.full_pair or not hold_on(ctx):
        return
    path = _cred_path(ctx)
    ours = _ours_now(ctx, path)
    standing = mirror_on(ctx)
    same = ours is not None and _same_login(live_mod.parse(ours[2]) if ours[2] is not None else None, lr.login)
    if ours is None or (standing and ours[2] == lr.read.value) or (not standing and same):
        return
    if standing and (not _mirror_safe(path) or (same and (keychain.busy_mac() or _mirror_young(ctx, ours[0])))):
        return                                  # only the item's other parts changed: a later look brings them
    block = live_mod.config_identity(ctx)[1]            # read before Claude Code's save lock is taken, never under it
    try:
        doc = vault.load(ctx)
    except vault.VaultError:
        doc = None
    try:
        with live_mod.storage_lock(ctx, budget_s=0.0):
            cur = live_mod.read_live(ctx, fresh=True, interactive=True)
            did, why = _follow_once(ctx, cur, path, block, doc, standing)
    except ccfiles.LockBusy:
        out['again'] = SPARE_AGAIN_S
        return
    # a copy of the item's own login is never a reason to take a login out of the item later (_maybe_take_out is for
    # bytes the hold put back from the file): Claude Code put this one there itself
    if did == 'followed' and why == 'parts':
        return                                  # only the item's other parts changed: nothing said, nothing recorded
    if did == 'followed':
        ctx.log(FOLLOW_LINE)
    elif did == 'removed' and why == 'another':
        ctx.log(FOLLOW_GONE_LINE)
    if did is not None:
        _spare_said(ctx, did, why)


def _follow_once(ctx, cur, path, block, doc, standing=False):
    """_follow's step under Claude Code's save lock, cur its fresh read of the item, block and doc the settings'
    identity block and the saved accounts (None when they can't be read), read before that lock was taken: (did,
    why), did 'followed', 'removed' or None; why 'parts' for a standing copy whose login was the item's already (only
    the item's other parts changed). A file of ours whose note is lost (known to this process only) can't be
    replaced and known: it goes. standing: the standing copy is on (_follow)."""
    if not (cur.usable and cur.read.provenance == 'keychain' and cur.login is not None and cur.login.full_pair):
        return None, None
    if standing and _struck(cur.login, doc, block):
        return None, None                       # never a copy of a login Anthropic refused
    with _HOLD_FILES:
        ours = _ours_now(ctx, path)
        if ours is None:
            return None, None
        stamp, note, text = ours
        filed = live_mod.parse(text) if text is not None else None
        if (standing and text == cur.read.value) or (not standing and _same_login(filed, cur.login)):
            return None, None
        same = filed is not None and filed.kind == 'oauth' and _one_account(cur.login, filed, doc, block)
        if same and note is not None:
            why = 'parts' if standing and _same_login(filed, cur.login) else None
            return ('followed', why) if _hold_swap(ctx, path, stamp, note, cur.read.value) is not None else \
                (None, None)
        removed = _hold_remove(ctx, path, stamp)
        _hold_drop_note(ctx, note)
        if not same:
            _hold_empty(ctx)                    # bytes kept for that file's account are not put back beside it either
        return ('removed', None if same else 'another') if removed else (None, None)


def _hold_swap(ctx, path, old, note, text):
    """Replace the file of ours at path (stamp old, described by note) with text in one rename, so that there is never
    a moment without a file, and only while it is still that file. The note first names the temporary file, before it
    is made (pending, its stamp still the file in place: a process stopped while the temporary file holds the login
    leaves a name the next tidy, the sign-in watch's start and the uninstaller take away, as _hold_make does); then the
    new file, with the one it replaces as 'prev' (whichever of the two is there is known as ours if this process stops
    part-way, and a young pending note is left alone by another process's tidy); hold_own is told the new stamp before
    the rename (the sign-in watch's call for that change knows it is ours); then the rename; then the note settled, its
    'wrote' kept. The new stamp, or None (nothing changed; quiet, and counted unless Claude Code had written the file
    since). The caller holds _HOLD_FILES."""
    tmp = _hold_tmp_name(path)
    first = dict(note, tmp=tmp, stamp=old, prev=None, state='pending', at=ctx.wall())
    try:
        _hold_write_note(ctx, first, sync=False)
    except (OSError, ccfiles.CCFileError):
        _hold_fail(ctx)
        return None
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            view = memoryview(text.encode('utf-8'))
            while view:
                view = view[os.write(fd, view):]
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        st = os.stat(tmp)
        new = (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)
        _hold_write_note(ctx, dict(first, stamp=new, prev=old), sync=False)
    except (OSError, ccfiles.CCFileError):
        _hold_unlink(tmp)
        try:
            _hold_write_note(ctx, note)                 # the note as it was before
        except (OSError, ccfiles.CCFileError):
            pass
        _hold_fail(ctx)
        return None
    hold_own(ctx, new)
    try:
        if _stamp(path) != old:
            raise FileExistsError(path)                 # Claude Code wrote the file since: its own, left as it is
        os.replace(tmp, path)
    except OSError as e:
        _hold_unlink(tmp)
        try:
            _hold_write_note(ctx, note)                 # the note as it was before
        except (OSError, ccfiles.CCFileError):
            pass
        if not isinstance(e, FileExistsError):
            _hold_fail(ctx)
        return None
    with _HOLD_LOCK:
        h = _HOLDER.get(ctx.data_dir)
        if h is not None and h.get('ours') == old:
            h['ours'] = new
    try:
        _hold_write_note(ctx, dict(note, tmp=None, stamp=new, prev=None, state='settled'))
    except (OSError, ccfiles.CCFileError):
        _hold_fail(ctx)                                 # the pending note still knows the file; a look settles it
    return new


# ---------- a standing copy of the sign-in in Claude Code's plain login file ----------
#
# A Claude Code process reads its login from its Keychain item, with a limit of 2 s and no second try; when that read
# gives nothing (it timed out, "no such item", empty, locked with nothing cached) the process reads Claude Code's plain
# login file instead (2.1.329 to 2.1.332, read in its program text). The process that just started has nothing cached,
# so on a loaded Mac its first request can find no login and end that chat's turn with "Not logged in", and
# `claude auth status`, which the VS Code extension asks once per panel when the account in its settings changes,
# answers "signed out" the same way. (Its read for a save or a renewal does not fall through on a timeout.) So, while
# the setting keep_signin_file is on (mirror_on), the plain login file is kept as an exact copy of the item:
#   - made from the item's exact text at a look that finds the item holding a full login (both tokens) of the account
#     Claude Code's settings name (proven as a promote proves a plain login file's login, and not older than its
#     saved copy), with no switch part-way, no sign-in under way on the page, and no file there (_mirror); holding
#     Claude Code's save lock for a fresh read, create-only and owner-only, as the hold makes its files (_hold_make);
#   - brought up to the item's exact text at the look after every change of its login (_follow), in one rename; a
#     change of the item's other parts alone at most once in MIRROR_PARTS_S, never on a busy Mac;
#   - made again when a Claude Code process removes it while the item is there (a process whose Keychain read found
#     nothing removes the file once its own save into the item landed);
#   - written by a switch right after its write of the item and before Claude Code's settings name the new account
#     (mirror_put), so the extension's checks after the settings change find the new login in both places;
#   - a plain login file Claude Code wrote itself and left for HOLD_TIDY_S beside such an item is brought up to the
#     item too, and kept as ours (_mirror_adopt), when its login is the item's or an older one of the same account, or
#     it holds none (never another account's: that may be the only copy of its newest refresh token). A younger
#     one is Claude Code's fallback (its save into the item was refused: it deletes the item next), left to the
#     hold's rules.
# It is never written from a login that is blank, or whose refresh token the saved accounts have struck, and never
# through a folder or file that is a link or not this user's (_mirror_safe). It is a file of ours like any other
# (signin-hold.json knows its stamp), so it goes by the hold's rules once it serves nobody: at once when the settings
# name nobody (a chosen sign-out clears them), beside another account's login, and, once the item holds no usable
# login of the account named (or the setting is off), at the first look that finds the item, when the item was last
# known absent HOLD_TIDY_S ago or more. While the item is missing it is Claude Code's sign-in, put back in the item by
# the promote's rule after SIGN_OUT_S. The uninstaller removes it unless it is the only sign-in Claude Code has.
# A running Claude Code compares this file's modification time before each request and, when it moved, drops its
# cached read of the item and reads the Keychain again, twice (2.1.329, read in its program text). So each new copy
# costs every running Claude Code process a `security` call or two at its next request: a change of the login is
# brought into the copy at once (a stale refresh token in it is the danger), a change of the item's other parts
# alone (a connector's login, a mark Claude Code keeps there) at most once in MIRROR_PARTS_S, and never on a busy Mac.
MIRROR_PARTS_S = 300.0
# data folder -> (stamp of a plain login file of Claude Code's, the item's login it was judged beside: _login_key)
# that _mirror_adopt declined to take: not judged again (no save lock, no fresh read) until either changes
_NOT_ADOPTED = {}
# Claude Code removes the plain login file after a save by a process whose Keychain read had found nothing (on a
# loaded Mac, a fresh process whose first read ran out). Each new copy then makes every running Claude Code read its
# item again: removed and made again in a loop, that would feed the load. So beside one and the same login, at most
# REMAKE_BURST copies are made in any REMAKE_GAP_S (the copy and its first remake at once; a third waits, and the look
# asks for the look that makes it): 2 in any 20 s, so up to 6 a minute, each costing every running chat a `security`
# call or two (against up to 20 a minute at the watch's own busy pace). A process that asks in that gap and whose own
# Keychain read runs out finds no file, as before the copy existed. A new login in the item is copied at once.
REMAKE_GAP_S = 20.0
REMAKE_BURST = 2
_REMADE = {}                    # data folder -> [(wall time, _login_key)] of the copies _mirror made, the last minute
MIRROR_LINE = ("Claude Code's sign-in is also kept in its plain login file (readable only by you), so a chat that "
               "finds the Keychain slow reads it from there")


def _mirror_young(ctx, stamp):
    """The plain login file with this stamp was written less than MIRROR_PARTS_S ago, by its modification time (a
    time a little past the clock counts as now)."""
    age = ctx.wall() - stamp[2] / 1e9
    return -MIRROR_PARTS_S < age < MIRROR_PARTS_S


def _mirror_safe(path):
    """The folder of Claude Code's plain login file is a real folder of this user's (not a link), and the file, when
    there, is a regular file of this user's (not a link): the standing copy is never written through a link or into a
    folder or file another user owns."""
    try:
        st = os.lstat(os.path.dirname(path))
    except OSError:
        return False
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        return False
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid()


def _owner_only(path, stamp):
    """A file only noted as ours (it already holds the text it should) is made readable by its owner alone, as every
    file of ours is from its first byte: through a descriptor opened without following a link, only while it is still
    the file with this stamp and this user's (fchmod moves neither its time nor its size, so the stamp stays). True
    when it is owner-only now."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    except OSError:
        return False
    try:
        st = os.fstat(fd)
        if (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size) != tuple(stamp) or st.st_uid != os.getuid() or \
                not stat.S_ISREG(st.st_mode):
            return False
        if stat.S_IMODE(st.st_mode) != 0o600:
            os.fchmod(fd, 0o600)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def _struck(login, doc, block=None):
    """The saved accounts have struck this login's refresh token (Anthropic refused it: one strike, bound to its
    fingerprint). False when they can't be read."""
    if doc is None or login is None or not login.fingerprint:
        return False
    return any((a.get('strike') or {}).get('fp') == login.fingerprint for a in doc['accounts'].values())


def _mirror_owner(login, doc, block):
    """The account a login in Claude Code's item is kept a standing copy for: the one the settings name (block), when
    the login is a full one of that account (_file_is_theirs: its own, never older than its saved copy) and not
    struck; else None. doc: the saved accounts (None when they can't be read)."""
    aid = identity.label_id(doc, block) if doc is not None and block else None
    if aid is None or login is None or not login.full_pair or _struck(login, doc):
        return None
    if _file_is_theirs(login, doc, block, aid, _recorded(doc['accounts'][aid]), PRESENT) != 'theirs':
        return None
    return aid


def _mirror_keeps(ctx, lr, cfg=None):
    """What the standing copy is there for, by a look's live read lr: a switch part-way (it writes the copy itself:
    mirror_put), or an item that holds a login the copy is kept for (_mirror_owner). An item with a full login beside
    Claude Code's settings or the saved accounts that can't be read this moment keeps the copy too: Claude Code
    rewrites its settings all the time, and taking the copy away now would only have the next look make it again.
    Only reads that succeeded and say another account's, struck or blank let the copy go. cfg: (Read, identity block)
    of Claude Code's settings when the caller has them."""
    if os.path.exists(switcher.journal_path(ctx)):
        return True
    if lr is None or not (lr.usable and lr.read.provenance == 'keychain') or lr.login is None or \
            not lr.login.full_pair:
        return False
    cfg_r, block = cfg if cfg is not None else live_mod.config_identity(ctx)[:2]
    if cfg_r.kind != PRESENT:
        return True
    try:
        doc = vault.load(ctx)
    except vault.VaultError:
        return True
    return _mirror_owner(lr.login, doc, block) is not None


def _mirror(ctx, lr, out):
    """At every look while the standing copy is on (mirror_on), lr its live read: with no plain login file there and
    the item holding a login the copy is kept for (_mirror_owner), holding Claude Code's save lock, a fresh read of
    the item and the copy made from its exact text (_spare_from); with a plain login file of Claude Code's own there
    for HOLD_TIDY_S or more, that file brought up to the item and kept as ours (_mirror_adopt). The settings and the
    saved accounts are read before the save lock, which is never waited for: out['again'] asks for another look."""
    if lr is None or not (lr.usable and lr.read.provenance == 'keychain') or lr.login is None or \
            not lr.login.full_pair or not mirror_on(ctx):
        return
    path = _cred_path(ctx)
    stamp = _stamp(path)
    now = ctx.wall()
    if stamp is not None and stamp in _hold_known(ctx, _hold_note(ctx), path):
        _mirror_said(ctx, now)                          # one a switch made, or a put-back left, is the copy too
        return                                          # ours: _follow keeps it in step
    if stamp is not None and now - stamp[2] / 1e9 < HOLD_TIDY_S:
        return                                          # Claude Code's own, young: its fallback, left to the hold
    if stamp is not None:
        with _HOLD_LOCK:
            judged = _NOT_ADOPTED.get(ctx.data_dir)
        if judged == (stamp, _login_key(lr.login)):
            return                                      # already judged not to be taken, beside this very login
    else:
        key = _login_key(lr.login)
        with _HOLD_LOCK:
            made_at = [t for t, k in _REMADE.get(ctx.data_dir) or [] if k == key and 0 <= now - t < REMAKE_GAP_S]
        if len(made_at) >= REMAKE_BURST:
            out['again'] = max(SPARE_AGAIN_S, REMAKE_GAP_S - (now - min(made_at)))
            return                                      # made again too often beside this login: a later look
    if not _mirror_safe(path) or os.path.exists(switcher.journal_path(ctx)) or _signing_in(ctx, now):
        return
    # parsed only when the settings file changed (the copy is checked against them again right after it is made,
    # fresh: _spare_from), so a look that finds a login no copy is kept for costs no parse of a large file
    cfg_r, block, _ = live_mod.config_identity(ctx, cached=True)
    block = block if cfg_r.kind == PRESENT else None
    try:
        doc = vault.load(ctx)
    except vault.VaultError:
        return
    if _mirror_owner(lr.login, doc, block) is None:
        return                                          # decided on the look's own read; again under the lock
    absent = _absent_at(ctx) or 0.0                     # when the item was last known absent; 0: not known to be
    try:
        with live_mod.storage_lock(ctx, budget_s=0.0):
            cur = live_mod.read_live(ctx, fresh=True, interactive=True)
            made = None
            if cur.usable and cur.read.provenance == 'keychain' and _mirror_owner(cur.login, doc, block):
                made = _spare_from(ctx, cur, now, absent, block, doc) if stamp is None else \
                    _mirror_adopt(ctx, cur, path, stamp, absent, doc, block)
    except ccfiles.LockBusy:
        out['again'] = SPARE_AGAIN_S
        return
    if made is not None:
        if stamp is None:
            with _HOLD_LOCK:
                _REMADE[ctx.data_dir] = [x for x in _REMADE.get(ctx.data_dir) or [] if 0 <= now - x[0] < 60.0] + \
                    [(now, _login_key(cur.login))]
        _spare_said(ctx, 'mirrored', 'adopted' if stamp is not None else None)
        _mirror_said(ctx, now)


def _login_key(login):
    """What says a login changed (its tokens' fingerprints and its access end), or None."""
    return None if login is None else (login.fingerprint, login.access_fp, login.expires_ms)


def _mirror_adopt(ctx, cur, path, stamp, wrote, doc, block):
    """Holding Claude Code's save lock, cur a fresh read of an item that holds a login the standing copy is kept for:
    Claude Code's own plain login file (stamp: its stamp, HOLD_TIDY_S old or more) becomes the standing copy. Holding
    the item's exact text already, it is only noted as ours; else it is replaced with that text in one rename while it
    is still that file (_hold_swap). Only one whose login is the item's very login (the same tokens and access end:
    its other parts are a save Claude Code made into the file instead of the item) or older than it (live.compare),
    or that holds no usable login: never one whose login is newer or can't be told apart by age (it may hold the only
    refresh token Anthropic still takes), never one whose login is not the same account's (_one_account: a process
    that read another account's login before a switch can renew it and write it here, the only copy of its newest
    refresh token; live.compare tells nothing across accounts), nor one that is not a JSON object Claude Code would
    read. A temporary name an earlier note named goes, as hold_adopt does. wrote: when the item was last known absent,
    for the note; doc and block: the saved accounts and the settings' identity block, read before the save lock. Its
    stamp now, or None."""
    with _HOLD_FILES:
        if _stamp(path) != stamp:
            return None                                 # written since: Claude Code's, left to it
        text = _hold_read(path)
        filed = live_mod.parse(text) if text is not None else None
        same = text == cur.read.value
        if filed is None or filed.kind not in ('oauth', 'wiped', 'no_login', 'empty') or \
                (not same and filed.kind == 'oauth' and (not _one_account(cur.login, filed, doc, block) or
                                                         live_mod.compare(filed, cur.login) not in ('older', 'same'))):
            # not JSON, another account's, newer, or can't be told: it may hold the one good token. Remembered, so
            # the next looks do not take the save lock and read the item again for it
            with _HOLD_LOCK:
                _NOT_ADOPTED[ctx.data_dir] = (stamp, _login_key(cur.login))
            return None
        old = _hold_note(ctx)
        note = {'path': path, 'tmp': None, 'stamp': stamp, 'prev': None, 'at': ctx.wall(), 'wrote': wrote,
                'state': 'settled', 'said': False}
        if not same:
            new = _hold_swap(ctx, path, stamp, note, cur.read.value)
        else:
            if not _owner_only(path, stamp):
                return None
            try:
                _hold_write_note(ctx, note)
            except (OSError, ccfiles.CCFileError):
                _hold_fail(ctx)
                return None
            new = stamp
        if new is not None and old is not None:
            _hold_unlink(old['tmp'])                    # a temporary name the note before this one still named
        return new


def mirror_put(ctx, text):
    """For a switch (switcher._critical) and its roll back, right after its write of Claude Code's item landed, while
    it still holds Claude Code's save lock, before Claude Code's settings change: with the standing copy on
    (mirror_on), the plain login file holds exactly text, so the VS Code extension's checks after the settings change
    find that login in the item and in the file. A file of ours is replaced in one rename (_hold_swap; live.write_live
    left it alone: hold_drop); with no file, one is made (_hold_make); a file of Claude Code's own, which the write
    itself rewrote with text (ccfiles.bump_credentials_file), is noted as ours. text None (the roll back deleted the
    item), not a full login, or a folder or file that is a link or not this user's: a file of ours goes, so it never
    holds another login than the item. Its stamp, or None (no file of ours is there)."""
    if not mirror_on(ctx):
        return None
    path = _cred_path(ctx)
    login = live_mod.parse(text) if text is not None else None
    wrote = _absent_at(ctx) or 0.0
    with _HOLD_FILES:
        note = _hold_note(ctx)
        stamp = _stamp(path)
        ours = stamp is not None and stamp in _hold_known(ctx, note, path)
        if login is None or not login.full_pair or not _mirror_safe(path):
            if ours and _hold_remove(ctx, path, stamp):
                _hold_drop_note(ctx, note)
            return None
        if ours:
            new = _hold_swap(ctx, path, stamp, note, text) if note is not None else None
            if new is None and _hold_remove(ctx, path, stamp):
                _hold_drop_note(ctx, note)              # it could not be brought up to the item: it goes
            return new
        if stamp is None:
            return _hold_make(ctx, path, text, wrote)
        if _hold_read(path) != text or _stamp(path) != stamp or not _owner_only(path, stamp):
            return None
        try:
            _hold_write_note(ctx, {'path': path, 'tmp': None, 'stamp': stamp, 'prev': None, 'at': ctx.wall(),
                                   'wrote': wrote, 'state': 'settled', 'said': False})
        except (OSError, ccfiles.CCFileError):
            _hold_fail(ctx)
            return None
        if note is not None:
            _hold_unlink(note['tmp'])                   # a temporary name the note before this one still named
        return stamp


def mirror_drop(ctx):
    """For a switch whose mirror_put failed (switcher._mirror_put): a file of ours (by its stamp) is taken away with its
    note, under the hold's file lock; a file Claude Code wrote since stays. True when one was removed."""
    path = _cred_path(ctx)
    with _HOLD_FILES:
        note = _hold_note(ctx)
        stamp = _stamp(path)
        if stamp is None or stamp not in _hold_known(ctx, note, path) or not _hold_remove(ctx, path, stamp):
            return False
        _hold_drop_note(ctx, note)
    return True


def _mirror_said(ctx, now):
    """The History line the first time the standing copy is there after an install, whoever made it (a look, a
    switch, a put-back's spare that stays): the put-back record keeps that it was said, and the uninstaller's tidy
    forgets it. Once, never for a copy made again or brought up to the item."""
    if load_record(ctx).get('mirror_said'):
        return
    if _change_record(ctx, lambda r: r.update(mirror_said=True), now):
        ctx.say(MIRROR_LINE, 'info', h='setting', by='auto')
