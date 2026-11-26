"""What one account's opened line offers (remove it, a terminal as that account, its place in the order) and the
"More" area's account settings (the engine's state in one line, remove everything).

Each one is the engine's own (engine.py, over accounts.api). A refusal comes back as one plain line. Every
destructive action is first described by the page; the server refuses it without the confirmation the page sends.
"""
import os
import re
import subprocess

from . import core, engine
from .core import SwitchError, data_path, log, write_text

PURGE_WORDS = 'remove everything'
OPEN = ['/usr/bin/open', '-a', 'Terminal']        # tests put a stand-in here


# ---------- one account ----------

def remove_line(name, active):
    """What removing an account changes, said before it is done."""
    return ('Remove %s from Switchboard? Its saved login is deleted here. Claude Code%s the browser are not touched.'
            % (name, ' stays on it until you switch, and' if active else ' and'))


def remove(target, confirmed):
    """Forget an account (engine.remove). The page asks first."""
    if confirmed is not True:
        raise SwitchError('Removing needs your yes on the page.')
    engine.remove(target)


def terminal(target, email):
    """Open Terminal running `switchboard run <account>`: Claude Code as that account, in that window only (the login
    Claude Code uses everywhere else stays as it is)."""
    command = data_path(os.path.join('bin', 'switchboard'))
    if not (os.path.isfile(command) and os.access(command, os.X_OK)):
        raise SwitchError('The switchboard command is missing. Run the installer again.')
    folder = data_path('terminal')
    os.makedirs(folder, mode=0o700, exist_ok=True)
    safe = re.sub(r'[^A-Za-z0-9._-]', '_', str(target))[:80] or 'account'
    path = os.path.join(folder, 'account-%s.command' % safe)
    write_text(path, '#!/bin/sh\n# Opened by Switchboard: Claude Code as %s, in this window only.\nexec %s run %s\n' % (
        re.sub(r'[^\w@.+-]', '', email or ''), _sh(command), _sh(str(target))))
    os.chmod(path, 0o700)
    subprocess.Popen(OPEN + [path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


CHAT_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z')


def chat_terminal(chat_id):
    """Open Terminal running `switchboard resume <chat id>`: the chat opened again as the account it belongs to, in
    that window only (resume.py says which account and why, or why not, in that window). Returns the file it wrote."""
    chat_id = chat_id.strip().lower() if isinstance(chat_id, str) else ''
    if not CHAT_RE.match(chat_id):
        raise SwitchError('That is not a chat id.')
    command = data_path(os.path.join('bin', 'switchboard'))
    if not (os.path.isfile(command) and os.access(command, os.X_OK)):
        raise SwitchError('The switchboard command is missing. Run the installer again.')
    folder = data_path('terminal')
    os.makedirs(folder, mode=0o700, exist_ok=True)
    path = os.path.join(folder, 'chat-%s.command' % chat_id)
    write_text(path, '#!/bin/sh\n# Opened by Switchboard: chat %s, on the account it belongs to, in this window only.\n'
                     'exec %s resume %s\n' % (chat_id, _sh(command), _sh(chat_id)))
    os.chmod(path, 0o700)
    subprocess.Popen(OPEN + [path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return path


def _sh(text):
    return "'" + text.replace("'", "'\\''") + "'"


# ---------- all accounts ----------

def move(target, to):
    """Put an account at a place in the order (1 first); the others keep their order around it."""
    if isinstance(to, bool) or not isinstance(to, int) or to < 1:
        raise SwitchError('That place is not in the list.')
    ids = [r['id'] for r in engine.held(engine.rows()) if r.get('id')]
    want = engine.find(target).id
    if want not in ids or to > len(ids):
        raise SwitchError('That place is not in the list.')
    ids.remove(want)
    ids.insert(to - 1, want)
    engine.reorder(ids)


def status():
    """The engine, in one line or two: how many accounts it keeps, and anything its health check says is wrong.
    `switchboard doctor` says the rest."""
    try:
        n = len(engine.saved_identities())
        h = engine.health()
    except SwitchError as e:
        return [str(e)]
    lines = ['Switchboard %s keeps %d account%s.' % (core.VERSION, n, '' if n == 1 else 's')]
    lines += [x for x in engine.health_lines(h) if x]
    return lines


def view():
    """GET /api/more: what the More area shows, read when it is opened."""
    return {'status': status()}


def purge(typed):
    """Remove every account (each one through engine.remove), after the person typed the words. Automatic switching
    is turned off first: with no accounts it has nothing to do. A refusal part-way stops there and says which."""
    if not isinstance(typed, str) or typed.strip().lower() != PURGE_WORDS:
        raise SwitchError('Type "%s" to confirm.' % PURGE_WORDS)
    try:
        engine.set_auto(enabled=False)
    except SwitchError as e:
        log('automatic switching was not turned off before removing everything: %s' % e)
    for r in engine.held(engine.rows()):
        try:
            engine.remove(r['id'] or r['email'])
        except SwitchError as e:
            raise SwitchError('%s was not removed: %s' % (r['email'], e))
    log('removed every account')
