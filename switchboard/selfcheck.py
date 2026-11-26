"""One place that checks what Switchboard keeps and repairs what it safely can, used three ways:

- the page, every ten minutes: tick(), its one call in server.loop(), runs every check with fix=True on a thread of
  its own (one at a time), so a slow look never holds up the page;
- `switchboard doctor --fix`: run(fix=True), and each check said in one plain line (Result.line);
- `switchboard doctor`: it only looks and writes nothing; doctor_lines() gives what it says NO (or worth knowing) for.

Each check is a small function that returns Results: what it looked at, what it found, and what it repaired or why
not. The checks, in order:
- accounts: Switchboard's list of accounts (vault.load restores it from its newest good copy; see vault.py);
- settings: settings.json (core restores it from its last good copy) and that copy kept current;
- state: the page's state.json, written back from its copy under the state lock;
- journal: a switch cut off and still not settled after UNSETTLED_AFTER (api.resolve_journal, as at the page's start);
- python: Switchboard's status line and hooks whose Python is gone (each piece's own repair(), as at the start);
- entries: what Switchboard put in Claude Code's settings, as its records name it (statusline-install.json and the
  hooks' records). An entry that is gone while the person did not turn it off in Switchboard is put back once, through
  the same code that put it there, under keep's CHOICE lock (the person's own switch waits, and this waits for it);
  apply() reads the file again right before it writes, so a save Claude Code made meanwhile is never written over.
  Gone again within REPUT_WITHIN of being put back, it is left out, and the page says once what is missing and what
  it costs. An entry Switchboard did not add is never touched, nor a settings file kept as a link (dotfiles);
- temporary: temporary files a killed write left behind (core.sweep_temporary);
- claude_code: after Claude Code updates itself, one read-only look at its program for the names the engine relies on
  (facts.shape_look), once per version and file; a name not found is a warning with the version, never a refusal.

What was repaired is kept in repairs.json: a day on the page and in doctor, a week in the file.
"""
import os
import sys
import threading
import time

from . import core, repairs

EVERY = 600.0               # seconds between two runs from the page: often enough that a removed entry or a damaged
                            # file is back within minutes, rarely enough to cost nothing
REPUT_WITHIN = 86400.0      # an entry gone again within a day of being put back is left out: something keeps taking it
                            # out, and Switchboard does not fight it
UNSETTLED_AFTER = 120.0     # a switch's record this old is cut off, not under way: a switch takes seconds, and its
                            # longest waits (the switch lock, Claude Code's locks) are under a minute
RUN = {'at': None, 'running': False}
RUN_LOCK = threading.Lock()

OK, REPAIRED, LEFT, FAILED, PROBLEM = 'ok', 'repaired', 'left', 'failed', 'problem'
MARKS = {OK: 'ok', REPAIRED: 'fixed', LEFT: 'NO', FAILED: 'NO', PROBLEM: 'NO'}

PIECES = ('statusline', 'notify', 'session_hooks')
PIECE_WORDS = {'statusline': "Switchboard's status line", 'notify': "Switchboard's done and needs-you hooks",
               'session_hooks': "Switchboard's session hooks"}
PUT_BACK = {'statusline': "Switchboard's status line was missing from Claude Code's settings. It was put back.",
            'notify': "Switchboard's done and needs-you hooks were missing from Claude Code's settings. They were put "
                      "back.",
            'session_hooks': "Switchboard's session hooks were missing from Claude Code's settings. They were put back."}
# What a piece left out costs (COST), then the one thing to do (FIX): how to put it back, with what really does (the
# page's Notifications switch stays on while only the hooks are out, and the session hooks have no switch on the
# page), and how to make the removal stay, with the setting the self-check reads (_turned_off). The status line has
# no such setting (only Switchboard's own record of it, which off drops only while its line is there), so none is named.
FIX = {'statusline': 'Turn it on again in Settings, or run: switchboard statusline on',
       'notify': "Put them back with: switchboard notify on. If you took them out on purpose, turn Notifications off "
                 "in Settings: Switchboard's own notifications stop too.",
       'session_hooks': 'Put them back with: switchboard hooks on. If you took them out on purpose: switchboard hooks '
                        'off.'}
COST = {'statusline': "Claude Code's status line no longer feeds Switchboard its limits; numbers for the account in use "
                      "may be a few minutes older. Turn it on again in Settings.",
        'notify': "Claude Code's done and needs-you hooks were taken out of its settings again, so those notifications "
                  "don't come. " + FIX['notify'],
        'session_hooks': "Claude Code's session hooks were taken out of its settings again, so an open session is not "
                         "told what a switch means for it. " + FIX['session_hooks']}
GIVEN_UP_SAID = 86400.0     # a piece left out is said on the page and in doctor for a day from when it was left out


class Result(object):
    """One check's answer. state: ok (nothing to do), repaired, left (found and left on purpose), failed (a repair was
    tried and did not work), problem (found; a look that may not repair)."""

    def __init__(self, name, looked, found, state=OK, did=None, fix=None):
        self.name, self.looked, self.found, self.state, self.did, self.fix = name, looked, found, state, did, fix

    def line(self):
        """'fixed  <what it looked at>: <found>; <did>' for doctor --fix."""
        text = '%s: %s' % (self.looked, self.found)
        if self.did:
            text += '; ' + self.did
        return '%5s  %s' % (MARKS.get(self.state, '--'), text)

    def __repr__(self):
        return 'Result(%r, %r, %r, %r)' % (self.name, self.state, self.found, self.did)


# ---------- the checks ----------

NEWER_LIST = 'was written by a newer Switchboard than this one'
NEWER_LIST_TODO = 'Update this one: switchboard update'


def check_accounts(fix, now):
    from .accounts import context, vault
    looked = "Switchboard's list of accounts"
    ctx = context.current()
    before = len(repairs.recent(ctx.data_dir, now=now, what='accounts'))
    try:
        if fix:
            vault.load(ctx)
        else:
            with vault.no_restore():
                vault.load(ctx)
    except vault.VaultError as e:
        if e.kind == 'newer':                       # never restored over (vault.load refuses it): no copy to promise
            return [Result('accounts', looked, NEWER_LIST, PROBLEM, fix=NEWER_LIST_TODO)]
        if fix:
            return [Result('accounts', looked, str(e), FAILED)]
        return [Result('accounts', looked, "can't be read", PROBLEM,
                       fix='Run: switchboard doctor --fix (it comes back from its newest good copy)')]
    except Exception as e:  # noqa: BLE001 - a look that fails says so
        return [Result('accounts', looked, 'could not be looked at (%s)' % e.__class__.__name__, FAILED)]
    got = repairs.recent(ctx.data_dir, now=time.time(), what='accounts')
    if fix and len(got) > before:
        return [Result('accounts', looked, 'was damaged', REPAIRED, got[0].get('text'))]
    return [Result('accounts', looked, 'reads fine')]


def _check_file(name, what, looked, fix, now):
    state = core.file_state(name)
    if state != 'damaged':
        return Result(what, looked, 'reads fine' if state == 'fine' else 'not there yet (a fresh start)')
    if not fix:
        return Result(what, looked, "can't be read", PROBLEM, fix='Run: switchboard doctor --fix')
    if name == 'settings.json':
        core.settings()                             # restores it (core._settings_restore)
    else:
        with core.locked():                         # load answers from the copy; save writes a good file back
            pass
    got = repairs.recent(core.data_dir(), now=time.time(), what=what)
    if core.file_state(name) == 'damaged':
        return Result(what, looked, "can't be read", FAILED, 'it could not be written back (see the log)')
    return Result(what, looked, 'was damaged', REPAIRED, got[0].get('text') if got else 'written back')


def check_settings(fix, now):
    out = [_check_file('settings.json', 'settings', "Switchboard's settings file", fix, now)]
    if fix and core.file_state('settings.json') == 'fine':
        try:
            if core.keep_settings_copy():
                out.append(Result('settings_copy', 'The last good copy of the settings', 'was not current',
                                  REPAIRED, 'kept again'))
        except OSError as e:
            out.append(Result('settings_copy', 'The last good copy of the settings', 'was not current', FAILED,
                              'not kept: %s' % (e.strerror or e)))
    return out


def check_state(fix, now):
    return [_check_file('state.json', 'state', "The page's own file", fix, now)]


def check_journal(fix, now):
    from .accounts import api, context, switcher
    path = context.current().path(switcher.JOURNAL)
    looked = 'A switch cut off half way'
    try:
        age = now - os.stat(path).st_mtime
    except OSError:
        return [Result('journal', looked, 'none')]
    if age < UNSETTLED_AFTER:
        return [Result('journal', looked, 'a switch is under way')]
    if not fix:
        return [Result('journal', looked, 'one is not settled yet', PROBLEM, fix='Run: switchboard doctor --fix')]
    try:
        said = api.resolve_journal()
    except Exception as e:  # noqa: BLE001
        return [Result('journal', looked, 'one is not settled yet', FAILED, 'not settled: %s' % e.__class__.__name__)]
    # The page's start and the loop settle the same record with the same function, one at a time under the switch
    # lock. Nothing said back: this look settled nothing (another settled it, or holds the lock now), so it reports no
    # repair of its own.
    if not said:
        return [Result('journal', looked, 'none' if not os.path.exists(path) else 'a switch is under way')]
    if os.path.exists(path):
        return [Result('journal', looked, 'one is not settled yet', FAILED, said)]
    return [Result('journal', looked, 'one was not settled', REPAIRED, said)]


def check_python(fix, now):
    """Switchboard's status line and hooks whose Python is gone: each piece's own repair(), as at the page's start."""
    if not fix:
        return []
    from . import notify_hooks, session_hooks, statusline
    out = []
    for name, mod in (('statusline', statusline), ('notify', notify_hooks), ('session_hooks', session_hooks)):
        try:
            said = mod.repair()
        except Exception as e:  # noqa: BLE001 - each repair never raises; this is in case
            said = ['%s: %r' % (name, e)]
        for line in said:
            core.log(line)
            gone = 'left as' in line
            out.append(Result('python:' + name, '%s in Claude Code' % PIECE_WORDS[name], 'named a Python that is gone',
                              FAILED if gone else REPAIRED, line))
    return out


# what Switchboard put in Claude Code's settings

def _module(name):
    from . import notify_hooks, session_hooks, statusline
    return {'statusline': statusline, 'notify': notify_hooks, 'session_hooks': session_hooks}[name]


def _turned_off(name):
    """The person turned the piece off in Switchboard (its record is dropped then too; these settings say it even when
    taking it out failed)."""
    if name == 'notify':
        return not _module('notify').notes_on()
    if name == 'session_hooks':
        return not _module('session_hooks').hooks_on()
    return False


def _present(name, path):
    """True when the piece is in the settings file, False when not, None when the file can't be read."""
    mod = _module(name)
    if name == 'statusline':
        f = mod._settings_file(path)
        if f.get('error'):
            return None
        return mod.is_ours(f['obj'].get('statusLine'))
    return mod.status(path)['in']


def _put_back(name, path, rec):
    """Put the piece back through the code that put it there. Returns 'put_back', 'theirs' (another status line is
    there now: never replaced) or 'present'. Raises PlanError (nothing changed) when it can't."""
    mod = _module(name)
    if name != 'statusline':
        r = mod.turn(True)
        return 'put_back' if r.get('changed') else 'present'
    inst = rec.get('installed')
    cmd = inst['command'] if mod.is_ours(inst) and not mod.python_gone(inst['command']) else None
    plan = mod.install_plan(path, replace=False, command_line=cmd)
    if plan.get('kept_theirs'):
        return 'theirs'
    if not plan.get('ok'):
        raise mod.PlanError(plan.get('message') or 'refused')
    if not plan.get('change'):
        return 'present'
    for k in ('had_previous', 'previous', 'previous_text', 'file_was', 'inner'):    # what off puts back stays
        if k in rec:
            plan['record'][k] = rec[k]
    mod.apply(plan)
    mod.precompile()
    return 'put_back'


def _entry(name, fix, now, folder, path):
    mod = _module(name)
    looked = '%s in Claude Code' % PIECE_WORDS[name]
    rec = mod._records().get(os.path.realpath(path))
    note = repairs.watch(folder).get('entry:' + name) or {}
    if not rec or _turned_off(name):
        if note and fix:
            repairs.set_watch(folder, 'entry:' + name, None, now=now)
        return Result('entry:' + name, looked, 'not turned on' if not rec else 'turned off in Switchboard')
    here = _present(name, path)
    if here is None:
        return Result('entry:' + name, looked, "Claude Code's settings file can't be read; left as it is")
    if here:
        if note.get('given_up_at') and fix:         # back (turned on again): a later removal is put back once again
            repairs.set_watch(folder, 'entry:' + name, None, now=now)
        return Result('entry:' + name, looked, 'in place')
    found = 'missing'
    if _num(note.get('given_up_at')) and not 0 <= now - note['given_up_at'] < GIVEN_UP_SAID:
        return Result('entry:' + name, looked, 'left out, as it has been for over a day')
    if not fix:
        return Result('entry:' + name, looked, found, PROBLEM, fix=FIX[name])
    why = None
    if os.path.islink(path):
        why = 'linked'
    elif note.get('given_up_at'):
        why = note.get('why') or 'again'
    elif note.get('put_back_at') and 0 <= now - note['put_back_at'] < REPUT_WITHIN:
        why = 'again'
    if why is None:
        try:
            with core.file_lock(_choice()):
                rec = mod._records().get(os.path.realpath(path))      # looked at again, inside the turn
                if not rec or _turned_off(name) or _present(name, path) is not False:
                    return Result('entry:' + name, looked, 'in place, or turned off meanwhile')
                done = _put_back(name, path, rec)
        except Exception as e:  # noqa: BLE001 - nothing was changed (plan, then apply); said, tried again next time
            core.log('%s was missing and could not be put back: %s' % (PIECE_WORDS[name], e))
            return Result('entry:' + name, looked, found, FAILED, 'not put back: %s' % e)
        if done == 'put_back':
            repairs.set_watch(folder, 'entry:' + name, {'put_back_at': now}, now=now)
            core.said_repair(name, 'put_back', PUT_BACK[name])
            return Result('entry:' + name, looked, found, REPAIRED, 'put back')
        if done == 'present':
            return Result('entry:' + name, looked, 'in place')
        why = done                                  # 'theirs'
    if not note.get('given_up_at'):
        repairs.set_watch(folder, 'entry:' + name, dict(note, given_up_at=now, why=why), now=now)
        core.log('%s is missing from Claude Code\'s settings %s; left as it is'
                 % (PIECE_WORDS[name], {'again': 'again, within a day of being put back', 'linked':
                                        '(the file is a link, kept elsewhere)', 'theirs':
                                        '(another status line is there)'}.get(why, '')))
        try:
            from . import history
            history.record('repair', COST[name])
        except Exception:  # noqa: BLE001
            pass
    return Result('entry:' + name, looked, found, LEFT, COST[name], fix=FIX[name])


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _choice():
    from . import keep
    return keep.CHOICE


def check_entries(fix, now):
    from . import statusline
    path = statusline.settings_path()
    folder = core.data_dir()
    out = []
    for name in PIECES:
        try:
            out.append(_entry(name, fix, now, folder, path))
        except Exception as e:  # noqa: BLE001 - one piece never stops the others
            out.append(Result('entry:' + name, '%s in Claude Code' % PIECE_WORDS[name], 'could not be looked at (%s)'
                              % e.__class__.__name__, FAILED))
    return out


def check_temporary(fix, now):
    if not fix:
        return []
    gone = core.sweep_temporary(now=now)
    return [Result('temporary', 'Temporary files a cut-off write left', '%d' % gone if gone else 'none',
                   REPAIRED if gone else OK, 'removed' if gone else None)]


def check_claude_code(fix, now, home=None):
    """After Claude Code updates itself: the look described above, at most once per version and file."""
    from .accounts import facts
    home = home or core.home()
    looked = "Claude Code's own files"
    versions = facts.installed_versions(home)
    newest = versions[-1] if versions else None
    if newest is None or facts._version_key(newest) <= facts._version_key(facts.CHECKED):
        if fix and repairs.watch(core.data_dir()).get('claude_code'):
            repairs.set_watch(core.data_dir(), 'claude_code', None, now=now)
        if newest is None:
            return [Result('claude_code', looked, 'no Claude Code was found on this Mac to look at')]
        if newest == facts.CHECKED:
            return [Result('claude_code', looked, 'version %s, the one Switchboard was checked against' % newest)]
        return [Result('claude_code', looked, 'version %s, older than %s, the one Switchboard was checked against'
                       % (newest, facts.CHECKED))]
    path = facts.program_of(home, newest)
    try:
        st = os.stat(path) if path else None
        sig = [path, st.st_size, st.st_mtime] if st else None
    except OSError:
        sig = None
    seen = repairs.watch(core.data_dir()).get('claude_code') or {}
    if sig is not None and seen.get('version') == newest and seen.get('sig') == sig:
        missing = seen.get('missing') or []
    elif sig is None:
        return [Result('claude_code', looked, 'Claude Code %s: its program was not found to look at' % newest)]
    else:
        got = facts.shape_look(path)
        if got is None:
            return [Result('claude_code', looked, 'Claude Code %s: its program could not be read' % newest)]
        missing = [words for _, words in got]
        if fix:
            repairs.set_watch(core.data_dir(), 'claude_code', {'version': newest, 'sig': sig, 'missing': missing,
                                                                'at': now}, now=now)
    if not missing:
        return [Result('claude_code', looked, 'Claude Code %s still names what Switchboard relies on' % newest)]
    return [Result('claude_code', looked, shape_words(newest, missing), LEFT, fix=SHAPE_TODO)]


# What to do when a newer Claude Code no longer holds a name Switchboard relies on: switching is never refused for it,
# and the one thing that can change the outcome is a Switchboard that knows that Claude Code. (facts.CHECKED is the
# Claude Code version these names were checked against.)
SHAPE_TODO = 'Switching goes on. If a switch fails, see whether a newer Switchboard is out: switchboard update --check.'


def shape_words(version, missing):
    from .accounts import facts
    missing = list(missing)
    return ("Claude Code %s is newer than %s, the version Switchboard was checked against, and Switchboard can't find "
            "%s in its program: %s" % (version, facts.CHECKED, 'this' if len(missing) == 1 else 'these', _and(missing)))


def _and(items):
    items = list(items)
    return items[0] if len(items) == 1 else ', '.join(items[:-1]) + ' and ' + items[-1]


CHECKS = (check_accounts, check_settings, check_state, check_journal, check_python, check_entries, check_temporary,
          check_claude_code)


def run(fix=True, now=None):
    """Every check, in order. fix=False only looks and writes nothing. Never raises."""
    now = time.time() if now is None else now
    out = []
    for check in CHECKS:
        try:
            out += check(fix, now)
        except Exception as e:  # noqa: BLE001 - one check never stops the others
            out.append(Result(check.__name__[len('check_'):], check.__name__[len('check_'):], 'could not be looked at '
                              '(%s)' % e.__class__.__name__, FAILED))
    return out


# ---------- the page ----------

def tick(now=None, start=None):
    """From the page's loop: start a run (fix=True) on its own thread when EVERY seconds passed since the last one and
    none is running. Returns the thread, or None. `start` is for tests (it runs the job instead of a thread)."""
    now = time.monotonic() if now is None else now
    with RUN_LOCK:
        if RUN['running'] or (RUN['at'] is not None and 0 <= now - RUN['at'] < EVERY):
            return None
        RUN.update(running=True, at=now)

    def job():
        try:
            for r in run(fix=True):
                if r.state in (REPAIRED, FAILED):
                    core.log('self-check: ' + r.line().strip())
        finally:
            with RUN_LOCK:
                RUN['running'] = False
    if start is not None:
        start(job)
        return None
    t = threading.Thread(target=job, name='switchboard-selfcheck', daemon=True)
    try:
        t.start()
    except RuntimeError:
        with RUN_LOCK:
            RUN['running'] = False
        return None
    return t


def notices(now=None):
    """What the page says, one line each: a file restored from a copy in the last day, an entry of Switchboard's left
    out of Claude Code's settings, and Claude Code's files no longer naming what Switchboard relies on. [{'what',
    'text', 'at'}], newest first. Reads one small file."""
    now = time.time() if now is None else now
    folder = core.data_dir()
    out, seen = [], set()
    for e in repairs.recent(folder, now=now):
        if e.get('kind') in ('restored', 'defaults') and e.get('what') not in seen and isinstance(e.get('text'), str):
            seen.add(e.get('what'))
            out.append({'what': e.get('what'), 'text': e['text'], 'at': e.get('at')})
    w = repairs.watch(folder)
    for name in PIECES:
        n = w.get('entry:' + name) or {}
        if _num(n.get('given_up_at')) and 0 <= now - n['given_up_at'] < GIVEN_UP_SAID:
            out.append({'what': name, 'text': COST[name], 'at': n['given_up_at']})
    cc = w.get('claude_code') or {}
    if cc.get('missing'):
        out.append({'what': 'claude_code', 'text': shape_words(cc.get('version'), cc['missing']) + '. ' + SHAPE_TODO,
                    'at': cc.get('at')})
    return out


# ---------- doctor ----------

def doctor_lines(now=None):
    """[(good: False for NO, None for worth knowing, text, the one thing to do)] for plain `switchboard doctor`. Only
    looks: nothing is written."""
    now = time.time() if now is None else now
    out = []
    for e in repairs.recent(core.data_dir(), now=now):
        if e.get('kind') in ('restored', 'defaults') and e.get('what') in ('accounts', 'settings', 'state'):
            aside = os.path.basename(e['aside']) if isinstance(e.get('aside'), str) else None
            out.append((False, '%s (%s)' % (e.get('text'), repairs.when_words(e['at'])),
                        'Look over your accounts and settings.%s' % (
                            ' The damaged file is kept as %s.' % aside if aside else '')))
    for r in run(fix=False, now=now):
        if r.state == PROBLEM and r.name in ('accounts', 'settings', 'state', 'journal'):
            out.append((False, '%s: %s' % (r.looked, r.found), r.fix))
        elif r.state == PROBLEM and r.name.startswith('entry:'):
            out.append((False, '%s: missing from Claude Code\'s settings' % PIECE_WORDS[r.name[len('entry:'):]],
                        r.fix))
        elif r.name == 'claude_code' and r.state == LEFT:
            out.append((None, r.found, SHAPE_TODO))
    return out


BUSY_FOR = 180.0            # seconds: the page makes its answer in a few seconds; three minutes is not "a moment"


def busy_words(state):
    """From the running page's state answer: what it has been busy with for BUSY_FOR or more, in plain words, or None.
    Its answer older than that while a newer one was wanted, or its own loop in one stage that long."""
    ans = (state or {}).get('answer') if isinstance(state, dict) else None
    if not isinstance(ans, dict):
        return None
    age, loop = ans.get('age'), ans.get('loop') if isinstance(ans.get('loop'), dict) else {}
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)    # noqa: E731
    if ans.get('stale') and num(age) and age >= BUSY_FOR:
        return 'The page has been busy for %d min: its last answer is that old' % (age // 60)
    if num(loop.get('for')) and loop['for'] >= BUSY_FOR and isinstance(loop.get('stage'), str):
        return "The page's own loop has been %s for %d min" % (loop['stage'], loop['for'] // 60)
    return None


def fix_lines(now=None):
    """`switchboard doctor --fix`: every repair run, one plain line each, and under one that still needs a look the
    one thing to do. (exit code, lines)."""
    results = run(fix=True, now=now)
    bad = any(r.state in (FAILED, LEFT, PROBLEM) for r in results)
    return (1 if bad else 0), [r.line() + ('\n       ' + r.fix if r.fix and r.state in (FAILED, LEFT, PROBLEM) else '')
                               for r in results]


if __name__ == '__main__':
    code, lines = fix_lines()
    print('\n'.join(lines))
    sys.exit(code)
