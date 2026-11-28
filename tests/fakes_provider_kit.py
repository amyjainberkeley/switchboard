"""Stand-ins for everything switchboard.providers.cli reaches through its Kit, for the commands' tests.

The commands are tested before the modules beside them are merged: each stand-in here has exactly the functions and
arguments of the real modules (choices, tasks, handoff, place, run's lower part, the bridge), as the other lanes' branch
heads had them on Mon Nov 23 2026 (test_providers_kit_matches_real.py holds the list and fails when one drifts), and
keeps the rules DESIGN sections 4, 8.4, 8.5 and 13 give them, in memory. FakeProvider is an adapter (a Provider subclass) whose answers a test
sets; its start(), resume() and job() build what the real adapters build (the same argument lists), and job() runs a
stand-in program a test wrote (never one called claude or codex).

Made-up data only: a folder under the test's own temp root, ids like aaaaaaaa-0000-4000-8000-00000000000a, plans and
names that exist nowhere. No test here; the test files that use it hold the tests.
"""
import dataclasses
import hashlib
import io
import json
import os
import secrets
import signal
import subprocess
import time
import urllib.parse
from contextlib import contextmanager

from fakes_providers import make_reach, stand_in
from switchboard.providers import cli, codex_facts, contract, run as provider_run, words
from switchboard.providers.contract import (Answer, ChatRef, Event, Found, Handoff, Launch, Limits, Program, Provider,
                                            ProviderError, Ran, ReadInfo, Room, Seat, Transcript, Window)

NOW = 1795600000.0
CHAT_A = 'aaaaaaaa-0000-4000-8000-00000000000a'
CHAT_B = 'bbbbbbbb-0000-4000-8000-00000000000b'
CHAT_C = 'cccccccc-0000-4000-8000-00000000000c'
OTHER = 'another assistant'          # the from_name the stand-in adapters write into a first message


# ---------- the person's choices (choices.py, section 13) ----------


class FakeChoices:
    def __init__(self, added=None, order=None, noticed=None):
        """noticed: the providers whose notice was said (default: every one given in added, as an add says it)."""
        self._added = {'claude': 0}
        self._added.update(added or {})
        self._order = list(order) if order else None
        self.folders = {}
        self._ask_ok = {}
        self._hand_ack = None
        self._notice = dict(added or {}) if noticed is None else {p: NOW for p in noticed}
        self._open_with = {}
        self.budget = 96000

    def _entry(self, folder):
        key = os.path.realpath(folder)
        return self.folders.setdefault(key, {'consent': {}, 'stay': None, 'stay_set': []})

    def order(self):
        base = list(self._order) if self._order else sorted(self._added, key=lambda p: (p != 'claude',
                                                                                         self._added[p]))
        if 'claude' not in base:
            base.insert(0, 'claude')
        return base + [p for p in self._added if p not in base]

    def set_order(self, ids):
        self._order = list(ids)

    def added(self):
        return dict(self._added)

    def add(self, pid, now):
        self._added[pid] = now

    def remove(self, pid):
        if pid == 'claude':
            raise ValueError('the first provider is always added')
        was = self._added.pop(pid, None) is not None
        if self._order:
            self._order = [p for p in self._order if p != pid]
        self._notice.pop(pid, None)
        return was

    def consent(self, folder, pid):
        got = self._entry(folder)['consent'].get(pid)
        return got['state'] if got else None

    def set_consent(self, folder, pid, state, now):
        self._entry(folder)['consent'][pid] = {'state': state, 'at': now}

    def consent_at(self, folder, pid):
        got = self._entry(folder)['consent'].get(pid)
        return got.get('at') if got and got['state'] == 'yes' else None

    def stays(self, folder):
        return self._entry(folder)['stay']

    def set_stay(self, folder, pid, now):
        e = self._entry(folder)
        if pid is None:
            for q in e['stay_set']:
                if self.consent(folder, q) == 'never':
                    del e['consent'][q]
            e['stay'], e['stay_set'] = None, []
            return
        e['stay'] = pid
        for q in self._added:
            if q != pid and self.consent(folder, q) != 'never':
                self.set_consent(folder, q, 'never', now)
                e['stay_set'].append(q)

    def allow(self, folder, pid, now):
        self.set_consent(folder, pid, 'yes', now)
        e = self._entry(folder)
        if pid in e['stay_set']:
            e['stay_set'].remove(pid)

    def ask_ok(self, pid):
        return pid in self._ask_ok

    def set_ask_ok(self, now, pid):
        self._ask_ok[pid] = now

    def hand_ack(self, pid):
        return pid in (self._hand_ack or {})

    def set_hand_ack(self, now, pid):
        self._hand_ack = dict(self._hand_ack or {}, **{pid: now})

    def notice_said(self, pid):
        return pid in self._notice

    def set_notice_said(self, now, pid):
        self._notice[pid] = now

    def open_with(self, pid):
        return self._open_with.get(pid, contract.IN_TERMINAL)

    def set_open_with(self, pid, how):
        if how not in (contract.IN_TERMINAL, contract.IN_APP):
            raise ValueError('an app opens in a terminal or in its app')
        self._open_with[pid] = how

    def handoff_budget(self):
        return self.budget


# ---------- the ledger (tasks.py, section 14) ----------


class FakeTasks:
    def __init__(self):
        self.all = []           # Hops, oldest first
        self.made = 0
        self.pruned = []        # the now of each prune()
        self.rows = {}          # task rows begun by switchboard do, by id

    def new_hop_id(self):
        return 'h-' + secrets.token_hex(4)

    def new_task_id(self):
        return 't-' + secrets.token_hex(4)

    def prune(self, now):
        self.pruned.append(now)
        return 0

    def task_for(self, folder, provider, chat_id):
        for h in reversed(self.all):
            if chat_id and (h.to_chat == chat_id or h.from_chat == chat_id):
                return h.task
        self.made += 1
        return 't-%08x' % self.made

    def add_hop(self, hop):
        self.all.append(hop)
        return hop

    def update_hop(self, hop_id, **changes):
        for i, h in enumerate(self.all):
            if h.id == hop_id:
                self.all[i] = dataclasses.replace(h, **changes)
                return self.all[i]
        raise KeyError(hop_id)

    def get(self, hop_id):
        return next(h for h in self.all if h.id == hop_id)

    def open_continue(self, folder, to_chat=None):
        for h in reversed(self.all):
            if h.kind == 'continue' and h.folder == folder and h.state not in ('returned', 'failed') and \
                    (to_chat is None or h.to_chat == to_chat):
                return h
        return None

    def continued_from(self, folder, from_chat):
        for h in reversed(self.all):
            if h.kind == 'continue' and h.folder == folder and h.from_chat == from_chat and \
                    h.state not in ('returned', 'failed'):
                return h
        return None

    def hops(self, folder=None, limit=50):
        rows = [h for h in reversed(self.all) if folder is None or h.folder == folder]
        return rows[:limit]

    def add_task(self, task_id, folder, job, at):
        """tasks.add_task: a task begun by switchboard do, with how it was picked (kept in self.rows)."""
        from switchboard.providers import tasks as real            # the same check of the job's fields
        self.rows[task_id] = {'id': task_id, 'folder': folder, 'made_at': at, 'job': real._job(job)}
        return task_id

    def update_task(self, task_id, **changes):
        from switchboard.providers import tasks as real
        self.rows[task_id]['job'].update(real._job(changes))
        return dict(self.rows[task_id])


# ---------- the note (handoff.py, section 10) ----------


class FakeHandoff:
    """Writes a small real note under the notes folder (0600, folders 0700), from the transcript's events, so the
    commands' own handling of the file (show, remove) is real."""

    def __init__(self, removed=(('key', 2),)):
        self.removed = tuple(removed)
        self.made = []          # the keyword arguments of each make()
        self.notes = []         # each Handoff made
        self.fail = None        # a ProviderError kind to raise from make()
        self.stood = []         # each folder where_things_stand() was asked of
        self.pruned = []        # (reach, now) of each prune()
        self.tasks_written = [] # each task file write_task() made

    def fact(self, value, cap=200):
        return words.span(value, cap)

    def where_things_stand(self, folder, reach):
        self.stood.append(folder)
        return {'folder': folder}

    def make(self, transcript, *, hop, task, to_name, from_name, why, stand, earlier=(), budget=96000, reach):
        self.made.append(dict(transcript=transcript, hop=hop, task=task, to_name=to_name, from_name=from_name,
                              why=why, stand=stand, earlier=earlier, budget=budget))
        if self.fail:
            raise ProviderError(self.fail, 'The note could not be written.')
        root = notes_root(reach)
        folder = os.path.join(root, task)
        os.makedirs(folder, mode=0o700, exist_ok=True)
        body = '# Switchboard note %s\n\n' % hop + ''.join('> %s\n' % e.text for e in transcript.events)
        data = body.encode('utf-8')
        path = os.path.join(folder, hop + '.md')
        meta = os.path.join(folder, hop + '.json')
        for p, d in ((path, data), (meta, b'{"v": 1}')):
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(d)
        note = Handoff(hop=hop, path=path, meta_path=meta, bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                       removed=self.removed, nonce='0a1b2c3d')
        self.notes.append(note)
        return note

    def reusable(self, hop, chat, reader):
        return None

    def write_task(self, text, *, hop, task, to_name, kind_line, asked=(), folder, reach):
        """The real task file (handoff.write_task is pure but for its one file under the notes folder), so the
        commands' tests read what an app would read."""
        from switchboard.providers import handoff as real
        note = real.write_task(text, hop=hop, task=task, to_name=to_name, kind_line=kind_line, asked=asked,
                               folder=folder, reach=reach)
        self.tasks_written.append(note)
        return note

    def answer_path(self, task, hop, reach):
        folder = os.path.join(notes_root(reach), task)
        os.makedirs(folder, mode=0o700, exist_ok=True)
        path = os.path.join(folder, hop + '.answer.txt')
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600))
        return path

    def back_note(self, handoff, *, other_name, start, end):
        from switchboard.providers import handoff as real      # pure: fixed words, the times as numbers, the path
        return real.back_note(handoff, other_name=other_name, start=start, end=end)

    def prune(self, reach, now):
        self.pruned.append((reach, now))
        return 0


def notes_root(reach):
    root = os.path.join(os.path.dirname(reach.data_dir), contract.NOTES_DIR_NAME)
    os.makedirs(root, mode=0o700, exist_ok=True)
    return root


# ---------- how much room, which is next (place.py, sections 8.4 and 8.5) ----------


class FakePlace:
    def room_of(self, provider, name, limits, now, fresh_s):
        has_room, unknown, refills, read = [], False, [], []
        for lim in limits:
            full = [w for w in lim.windows if lim.reached or (w.used_pct is not None and w.line is not None
                                                              and w.used_pct >= w.line)]
            known = [w for w in lim.windows if w.used_pct is not None]
            fresh = lim.read_at is not None and now - lim.read_at <= fresh_s
            if lim.read_at is not None:
                read.append(lim.read_at)
            if full:
                stamps = [w.resets_at for w in full]
                refills.append(None if any(s is None for s in stamps) else max(stamps))
            elif fresh and known:
                has_room.append(lim.read_at)
            else:
                unknown = True
        if has_room:
            return Room(provider, contract.ROOM, read_at=max(has_room))
        if unknown or not refills:
            return Room(provider, contract.UNKNOWN, read_at=max(read) if read else None)
        back = None if any(r is None for r in refills) else min(refills)
        return Room(provider, contract.OUT, back_at=back, read_at=max(read) if read else None)

    CANNOT = {contract.NOT_ADDED: 'not_added', contract.NOT_INSTALLED: 'not_installed',
              contract.SIGNED_OUT: 'signed_out'}

    def pick(self, order, current, rooms, to=None):
        """place.pick's rule (lane A aac80f8): a named provider unless it is the current one or cannot run (picked
        even when out); else the first other with room, then the first not known, else all_out, or why the first
        other cannot run, or no_other."""
        by_id = dict(rooms) if isinstance(rooms, dict) else {r.provider: r for r in rooms}
        if to is not None:
            if to == current:
                return None, 'is_source'
            r = by_id.get(to)
            if r is None:
                return None, 'not_added'
            if r.state in self.CANNOT:
                return None, self.CANNOT[r.state]
            return to, {contract.ROOM: 'room', contract.OUT: 'out'}.get(r.state, 'unknown')
        listed = [by_id[p] for p in order if p != current and p in by_id]
        others = [r for r in listed if r.state not in self.CANNOT]
        for wanted, why in ((contract.ROOM, 'room'), (contract.UNKNOWN, 'unknown')):
            for r in others:
                if r.state == wanted:
                    return r.provider, why
        if others:
            return None, 'all_out'
        return None, (self.CANNOT[listed[0].state] if listed else 'no_other')


# ---------- run.py's lower part ----------


class FakeRun:
    shell_line = staticmethod(provider_run.shell_line)
    child_env = staticmethod(provider_run.child_env)

    def __init__(self):
        self.held_elsewhere = False
        self.locks = []

    @contextmanager
    def try_lock(self, name):
        self.locks.append(name)
        yield not self.held_elsewhere

    def notes_root(self, reach):
        return notes_root(reach)


def _end_group(proc, sig):
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def run_ending_group(argv, stdin=None, timeout=30.0, env=None, cwd=None):
    """run.run_program when this build has it (after the merge), else the same as DESIGN 4 says it is: a list, its
    own session, and on KeyboardInterrupt (which cli.hand's SIGTERM and SIGHUP handlers raise) the process group is
    ended (SIGTERM, 2 s, SIGKILL) before the exception goes on."""
    real = getattr(provider_run, 'run_program', None)
    if callable(real):
        return real(argv, stdin=stdin, timeout=timeout, env=env, cwd=cwd)
    start = time.monotonic()
    try:
        proc = subprocess.Popen([str(a) for a in argv], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=dict(env) if env is not None else None, cwd=cwd,
                                start_new_session=True)
    except OSError:
        return Ran(code=None, missing=True)
    try:
        out, err = proc.communicate((stdin or '').encode('utf-8'), timeout=timeout)
    except subprocess.TimeoutExpired:
        _end_group(proc, signal.SIGTERM)
        try:
            proc.communicate(timeout=2.0)
        except subprocess.TimeoutExpired:
            _end_group(proc, signal.SIGKILL)
            proc.communicate()
        return Ran(code=None, took_s=time.monotonic() - start, timed_out=True)
    except KeyboardInterrupt:
        _end_group(proc, signal.SIGTERM)
        try:
            proc.wait(2.0)
        except subprocess.TimeoutExpired:
            _end_group(proc, signal.SIGKILL)
            proc.wait()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                stream.close()
            except (OSError, ValueError):
                pass
        raise
    return Ran(code=proc.returncode, out=out.decode('utf-8', 'replace'), err=err.decode('utf-8', 'replace'),
               took_s=time.monotonic() - start)


# ---------- the bridge to the session hooks (lane E's bridge.py) ----------


class FakeBridge:
    def __init__(self, can=True, put_ok=True):
        self.can, self.put_ok, self.notes = can, put_ok, []

    def hooks_can_deliver(self):
        return self.can

    def put_note(self, session_id, text, path, hop, reach=None):
        self.notes.append((session_id, text, path, hop))
        return self.put_ok


# ---------- an adapter ----------


SPEC = {'claude': ('Claude', 'Anthropic', frozenset((contract.DETECT, contract.SEATS, contract.LIMITS, contract.CHATS,
                                                     contract.READ, contract.START, contract.RESUME))),
        'codex': ('Codex', 'OpenAI', frozenset((contract.DETECT, contract.SEATS, contract.LIMITS, contract.ASK,
                                                contract.CHATS, contract.READ, contract.START, contract.START_APP,
                                                contract.RESUME, contract.JOB)))}


# What the shared modules ask an adapter instead of testing its id (contract.Provider): a stand-in copies each from the
# real adapter of the same id, so the commands say what they say for the real one.
ADAPTER_FACTS = ('app_name', 'sign_in', 'billing_prefixes', 'cannot', 'job_question', 'own_rules', 'account_list',
                 'session_env', 'rule_files', 'rule_dirs', 'plan_phrase')


class FakeProvider(Provider):
    """An adapter whose answers the test sets. Its launches are the ones DESIGN 12.3 and 12.4 give each app."""

    def __init__(self, reach, pid, found=None, seats=(), limits=(), ask_limits=None, chats=(), transcripts=None,
                 job_program=None, job_answer='', busy=False, bridge=None, app=None, job_timeout=None):
        super().__init__(reach)
        self.id = pid
        self.name, self.vendor, self.can = SPEC[pid]
        from switchboard.providers import adapter_class
        real = adapter_class(pid)
        for fact in ADAPTER_FACTS:
            setattr(self, fact, getattr(real, fact))
        self.found_ = found
        self.seats_ = tuple(seats)
        self.limits_ = tuple(limits)
        self.ask_limits = ask_limits
        self.chats_ = list(chats)
        self.transcripts = dict(transcripts or {})
        self.job_program = job_program
        self.job_answer = job_answer
        self.job_timeout = job_timeout      # a test's shorter time limit, so a time-out takes a second
        self.busy = busy
        self.bridge = bridge
        self.app = app
        self.calls = []

    def detect(self):
        self.calls.append('detect')
        return self.found_ if self.found_ is not None else Found(self.id, installed=False)

    def seats(self):
        return self.seats_

    def started_in(self, path):
        """As the real adapter whose chats are carried over from tells it: the folder a chat file's first lines name
        (Claude Code's reader), None when the file cannot tell or this stand-in is not that adapter."""
        from switchboard.providers import claude_chats
        if not self.account_list or not isinstance(path, str) or not os.path.isfile(path):
            return None
        said = claude_chats._head_facts(path).get('cwd')
        return said if isinstance(said, str) and os.path.isabs(said) else None

    def limits(self, ask=False):
        if ask and contract.ASK not in self.can:
            return self._unsupported(contract.ASK)
        self.calls.append('limits(ask)' if ask else 'limits')
        if ask and self.ask_limits is not None:
            return self.ask_limits
        return self.limits_

    def chats(self, folder):
        """The chats started in folder, as a real reader keeps them (ChatRef.folder is where a chat began)."""
        here = os.path.realpath(folder)
        return tuple(c for c in self.chats_ if os.path.realpath(c.folder) == here)

    def read(self, chat, limits=contract.ReadLimits()):
        self.calls.append('read %s' % chat.id)
        if chat.id in self.transcripts:
            return self.transcripts[chat.id]
        return Transcript(chat, (Event(contract.PERSON, 'made-up request about the site'),), ReadInfo(reader='fake'))

    def _program(self):
        f = self.found_
        if f is None or f.program is None:
            raise ProviderError('not_installed', '%s is not on this Mac.' % self.name)
        return f.program

    def start(self, folder, handoff, how):
        prog = self._program()
        first = words.FIRST_WORDS.format(hop=handoff.hop, path=handoff.path, from_name=OTHER)
        if how == contract.IN_APP:
            if self.id == 'claude':
                return self._unsupported(contract.START_APP, words.NO_CLAUDE_LINK)
            url = 'codex://new?prompt=%s&path=%s' % (urllib.parse.quote(first, safe=''),
                                                     urllib.parse.quote(folder, safe=''))
            return Launch(self.id, how, prog, (prog.path,), url=url, app=self.app, first_words=first)
        argv = (prog.path, '-C', folder, first) if self.id == 'codex' else (prog.path, first)
        env = provider_run.child_env(self.reach.env)
        return Launch(self.id, how, prog, argv, env=env, cwd=folder,
                      line=provider_run.shell_line(argv, folder, self.reach.env), first_words=first)

    def open_here(self, folder, how):
        """As codex.py's open_here builds it: the terminal app in folder with no first message (Codex: -C folder)."""
        if how not in (contract.IN_TERMINAL, contract.PRINT) or not os.path.isabs(folder or ''):
            raise ValueError('a terminal start or the line to print, in an absolute folder')
        prog = self._program()
        argv = (prog.path, '-C', folder) if self.id == 'codex' else (prog.path,)
        env = provider_run.child_env(self.reach.env)
        return Launch(self.id, how, prog, argv, env=env, cwd=folder,
                      line=provider_run.shell_line(argv, folder, self.reach.env))

    def resume(self, chat, handoff, how):
        if self.busy:
            raise ProviderError('busy', 'That chat is open in a window; close it first.')
        prog = self._program()
        back = words.BACK_WORDS.format(hop=handoff.hop, path=handoff.path, other_name=OTHER)
        if self.id == 'codex':
            argv = (prog.path, 'resume', '-C', chat.folder, chat.id, back)
        else:
            argv = (prog.path, '--resume', chat.id, back)
        env = provider_run.child_env(self.reach.env)
        return Launch(self.id, how, prog, argv, env=env, cwd=chat.folder,
                      line=provider_run.shell_line(argv, chat.folder, self.reach.env), first_words=back)

    def start_task(self, folder, hop, path, how, extra=()):
        """As the real adapters build and check it (codex.py, claude.py start_task, with their own checks of the
        extra words): the program, -C folder for Codex, the model and effort words, then the fixed first message
        naming the task file. Any other extra word, a hop id of another shape or a relative path is refused as the
        adapters refuse it (ValueError); neither app's desktop link takes a new task."""
        import re
        from switchboard.providers.claude import ClaudeProvider
        from switchboard.providers.codex import CodexProvider
        if how == contract.IN_APP:
            return self._unsupported(contract.START_APP, codex_facts.NO_APP_TASK if self.id == 'codex'
                                     else words.NO_CLAUDE_LINK)
        if not os.path.isabs(folder or '') or not os.path.isabs(path or '') or \
                not re.match(r'^h-[0-9a-f]{8}\Z', str(hop)):
            raise ValueError('an absolute folder and task file, and a hop id')
        extra = (CodexProvider if self.id == 'codex' else ClaudeProvider)._task_words(extra)
        prog = self._program()
        first = words.TASK_WORDS.format(hop=hop, path=path)
        head = (prog.path, '-C', folder) if self.id == 'codex' else (prog.path,)
        argv = head + tuple(extra) + (first,)
        env = provider_run.child_env(self.reach.env)
        return Launch(self.id, how, prog, argv, env=env, cwd=folder,
                      line=provider_run.shell_line(argv, folder, self.reach.env), first_words=first)

    def job(self, folder, prompt, out_path, timeout=900.0):
        if contract.JOB not in self.can:
            return self._unsupported(contract.JOB)
        self.calls.append(('job', folder, prompt, out_path, timeout))
        if self.job_program is None:
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(self.job_answer)
            return Answer(self.id, True, text=self.job_answer, path=out_path, took_s=42.0, code=0)
        ran = self.reach.run([self.job_program, out_path], stdin=prompt, timeout=self.job_timeout or timeout,
                             env=provider_run.child_env(self.reach.env, job=True), cwd=folder)
        # The real adapter's answers (codex.py job, codex_facts' sentences): not started, the time limit (said for the
        # time asked), an exit with no answer and its last words.
        if ran.missing:
            return Answer(self.id, False, path=out_path, why=codex_facts.JOB_MISSING)
        if ran.timed_out:
            minutes = max(1, int(round(timeout / 60.0)))
            span = '%d minute%s' % (minutes, '' if minutes == 1 else 's')
            return Answer(self.id, False, path=out_path, took_s=ran.took_s,
                          why=codex_facts.JOB_TIMED_OUT.format(span=span))
        with open(out_path, encoding='utf-8') as f:
            text = f.read()
        if ran.code == 0 and text.strip():
            return Answer(self.id, True, text=text, path=out_path, took_s=ran.took_s, code=0)
        last = [ln for ln in (ran.err or '').splitlines() if ln.strip()]
        why = codex_facts.JOB_NO_ANSWER.format(code=ran.code)
        if last:
            why += codex_facts.JOB_LAST_WORDS.format(words=words.one_line(last[-1], 200))
        return Answer(self.id, False, path=out_path, took_s=ran.took_s, code=ran.code, why=why)


# ---------- made-up programs, limits and chats ----------


def program(root, pid, version, where='your PATH', source='path', refused=None, name=None):
    """A program the adapter reports. Its file is a stand-in (fake-codex, fake-claude), never run by these tests
    unless the test says so; exec is only recorded."""
    path = stand_in(os.path.join(root, 'bin-' + source), name or 'fake-' + pid, 'import sys\nsys.exit(0)\n')
    return Program(path=path, version=version, source=source, where=where, refused=refused)


def codex_found(root, version='0.169.1', seen='0.169.1', signed_in=None, programs=None):
    prog = program(root, 'codex', version, where='ChatGPT.app', source='app')
    progs = tuple(programs) if programs is not None else (prog,)
    return Found('codex', installed=True, program=prog, programs=progs, signed_in=signed_in, newest_seen=seen,
                 tested=True)


def claude_found(root, version='2.1.330'):
    prog = program(root, 'claude', version, where='your PATH')
    return Found('claude', installed=True, program=prog, programs=(prog,), signed_in=True, newest_seen=None,
                 tested=True)


def codex_limits(pct=42.0, read_ago=720.0, plan='pro', reached=None, resets_in=2 * 86400.0, source='file', ask=None,
                 error=None):
    w = Window('weekly', pct, NOW + resets_in if resets_in is not None else None, 10080, 95.0)
    return (Limits('codex', 'codex', (w,), NOW - read_ago, source, plan=plan, reached=reached, ask=ask,
                   error=error),)


def codex_seat(label='ChatGPT Pro', plan='pro'):
    return (Seat('codex', 'codex', label, plan=plan, in_use=True),)


def claude_seats(n=5):
    return tuple(Seat('claude', 'acct-%02d' % i, 'maya%d@northwind.example' % i, plan='max', in_use=i == 1)
                 for i in range(1, n + 1))


def claude_limits(n=5, under=2, read_ago=120.0, resets_in=3600.0):
    out = []
    for i in range(1, n + 1):
        pct = 30.0 if i <= under else 100.0
        w = Window('5-hour', pct, NOW + resets_in, 300, 90.0)
        out.append(Limits('claude', 'acct-%02d' % i, (w,), NOW - read_ago, 'engine', plan='max'))
    return tuple(out)


def chat(pid, folder, cid=CHAT_A, name='Fix the login page', ago=600.0, live=False, working=False, stopped=None,
         entrypoint=None, started_ago=None, version=None, work_folder=None):
    return ChatRef(pid, cid, folder, '/made-up/chat-%s.jsonl' % cid[:8], name=name,
                   started_at=NOW - (started_ago if started_ago is not None else ago + 60),
                   last_at=NOW - ago, live=live, working=working, version=version, entrypoint=entrypoint,
                   stopped=stopped, work_folder=work_folder)


# ---------- one made-up Mac and one command ----------


class Rig:
    """A Kit of stand-ins for one test: rig.kit, what was printed (rig.text()), the questions asked (rig.asked), the
    History lines (rig.said), and the Reach's own record (rig.calls: execs, links, runs)."""

    def __init__(self, root, tty=False, answers=(), env=None, added=None, order=None, execve=None, open_url=None,
                 run=None, noticed=None):
        self.root = root
        self.folder = os.path.realpath(os.path.join(root, 'site'))
        os.makedirs(self.folder, exist_ok=True)
        base_env = {'HOME': os.path.join(root, 'home'), 'PATH': '/usr/bin:/bin'}
        base_env.update(env or {})
        self.reach, self.calls = make_reach(root, env=base_env, tty=tty, wall=lambda: NOW, run=run)
        if execve is not None or open_url is not None:
            self.reach = dataclasses.replace(self.reach, **{k: v for k, v in (('execve', execve),
                                                                                ('open_url', open_url)) if v})
        self.out = io.StringIO()
        self.answers = list(answers)
        self.asked = []
        self.said = []
        self.choices = FakeChoices(added=added, order=order, noticed=noticed)
        self.tasks = FakeTasks()
        self.handoff = FakeHandoff()
        self.place = FakePlace()
        self.run = FakeRun()
        self.providers = {}
        self.load_errors = {}

    def add_provider(self, prov):
        self.providers[prov.id] = prov
        return prov

    def provider(self, pid, **kw):
        return self.add_provider(FakeProvider(self.reach, pid, **kw))

    def _ask(self, prompt):
        self.asked.append(prompt)
        if not self.answers:
            raise AssertionError('a question nobody expected: %r' % prompt)
        answer = self.answers.pop(0)
        return answer(prompt) if callable(answer) else answer

    def _say(self, text, kind, **facts):
        self.said.append((text, kind, facts))

    @property
    def kit(self):
        return cli.Kit(reach=self.reach, load=self.providers.get, load_errors=self.load_errors, handoff=self.handoff,
                       tasks=self.tasks, place=self.place, choices=self.choices, run=self.run, say=self._say,
                       ask=self._ask, out=self.out, cwd=lambda: self.folder)

    def __call__(self, *args):
        """Run one command: its exit code."""
        return cli.main(list(args), kit=self.kit)

    def text(self):
        return self.out.getvalue()

    def lines(self):
        return self.text().splitlines()


def read_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


# ---------- the usual made-up Mac: Claude with five accounts, Codex added a day ago ----------


class KitCase:
    """A mixin for unittest.TestCase: self.root (a fresh temp folder) and self.rig(...)."""

    def setUp(self):
        import shutil
        import tempfile
        super().setUp()
        self.root = os.path.realpath(tempfile.mkdtemp(prefix='sb-providers-cli-'))
        self.addCleanup(shutil.rmtree, self.root, True)

    def rig(self, codex=True, codex_added=True, codex_found_=None, claude_chats=(), codex_chats=(), **kw):
        """A Rig with Claude (five accounts, two under their lines) and, unless codex=False, Codex (ChatGPT Pro,
        weekly 42%, read 12 minutes ago), added a day ago unless codex_added=False, its notice said then (noticed=()
        for one added on an older build, which never said it). Chats are built against the rig's folder: pass
        functions folder -> ChatRef."""
        added = {'codex': NOW - 86400} if codex and codex_added else {}
        added.update(kw.pop('added', {}) or {})
        r = Rig(self.root, added=added, **kw)
        r.claude = r.provider('claude', found=claude_found(self.root), seats=claude_seats(),
                              limits=claude_limits(), chats=[c(r.folder) for c in claude_chats])
        if codex:
            r.codex = r.provider('codex', found=codex_found_ or codex_found(self.root), seats=codex_seat(),
                                 limits=codex_limits(), chats=[c(r.folder) for c in codex_chats])
        return r
