"""Helpers for the tests of one sign-in whose refresh-expiry stamp moves a little at a renewal.

Claude Code (checked in 2.1.329 and 2.1.331) stores, after each renewal, refreshTokenExpiresAt = min(the stamp it had,
now + the seconds the server says are left). "now + seconds left" lands a little earlier or later each time, so the
stored stamp of one sign-in can go down by milliseconds to seconds, and never goes up. These helpers sign accounts in
with chosen stamps and renew the live login under that rule, on the stand-ins of fakes_accounts.
"""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import time

from fakes_accounts import Rig, identity_block
from switchboard.accounts import roster

DAY_MS = 86400 * 1000


class Clock:
    """Real time plus a jump the test adds to both clocks at once (so no jump reads as the Mac waking)."""

    def __init__(self):
        self.jump = 0.0

    def wall(self):
        return time.time() + self.jump

    def mono(self):
        return time.monotonic() + self.jump

    def advance(self, seconds):
        self.jump += seconds


def new_rig():
    clock = Clock()
    return Rig(wall=clock.wall, mono=clock.mono), clock


def email_of(uuid):
    return '%s@mail.example' % uuid


def org_of(uuid):
    return 'org-' + uuid


def _set_stamp(text, rx_ms):
    blob = json.loads(text)
    blob['claudeAiOauth']['refreshTokenExpiresAt'] = int(rx_ms)
    return json.dumps(blob, separators=(',', ':'))


def sign_in_at(rig, uuid, rx_ms):
    """What /login does, with the sign-in's end stamp chosen: the live item and the settings name this account."""
    server = rig.server
    text = server.add(uuid, email_of(uuid), org_of(uuid))
    server.accounts[uuid]['lineages'][server.accounts[uuid]['latest']]['rx_ms'] = int(rx_ms)
    text = _set_stamp(text, rx_ms)
    rig.set_live(text)
    rig.set_config(identity_block(uuid, email_of(uuid), org_of(uuid)))
    return text


def keep_at(rig, uuid, rx_ms):
    """/login with a chosen end stamp, then the page's Add: the account is kept and is the live login."""
    text = sign_in_at(rig, uuid, rx_ms)
    roster.add_current(rig.ctx)
    return text


def true_stamp(rig, uuid):
    """The end of the account's latest sign-in as Anthropic knows it (what Claude Code's stamp moves around)."""
    a = rig.server.accounts[uuid]
    return a['lineages'][a['latest']]['rx_ms']


def renew(rig, move_ms, clock=None, step_s=60.0, uuid=None):
    """Claude Code renewing the live login by itself: new tokens, a later access expiry, and the stored stamp
    min(old stamp, the sign-in's end + move_ms), as Claude Code stores it. Returns (old text, new text)."""
    if clock is not None:
        clock.advance(step_s)
    old = rig.live_text()
    o = json.loads(old)['claudeAiOauth']
    owner = uuid
    if owner is None:
        for u, a in rig.server.accounts.items():
            if any(line.get('refresh') == o['refreshToken'] for line in a['lineages'].values()):
                owner = u
    end = None
    for line in rig.server.accounts[owner]['lineages'].values():
        if line.get('refresh') == o['refreshToken']:
            end = line['rx_ms']
    new = rig.server.refresh_for(old)
    stamp = min(int(o['refreshTokenExpiresAt']), int(end + move_ms))
    new = _set_stamp(new, stamp)
    rig.set_live(new)
    return old, new


def moved(text, move_ms, later_access_s=0):
    """The same login with its stamp moved by move_ms (and its access expiry later by later_access_s)."""
    blob = json.loads(text)
    o = blob['claudeAiOauth']
    o['refreshTokenExpiresAt'] = int(o['refreshTokenExpiresAt'] + move_ms)
    o['expiresAt'] = int(o['expiresAt'] + later_access_s * 1000)
    return json.dumps(blob, separators=(',', ':'))


def refresh_token(text):
    return json.loads(text)['claudeAiOauth']['refreshToken']


def count_profile_asks(server):
    """Wrap the stand-in's request so profile asks are counted: returns a list that grows by one per ask."""
    from switchboard.accounts import facts
    asks = []
    real = server.request

    def spy(method, url, headers=None, body=None, timeout_s=10.0):
        if url == facts.PROFILE_URL:
            asks.append(url)
        return real(method, url, headers, body, timeout_s)

    server.request = spy
    return asks
