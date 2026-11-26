"""Codex (OpenAI's app) as one provider: which program runs, its numbers, how its app starts on a note, one job.

What it reads and what it runs:
- Its program: every `codex` on $PATH, the copy inside each desktop app whose bundle id is Codex's, and the copy the
  app keeps for itself under the Codex folder (listed, never run). A copy is run only when no folder above it can be
  changed by other users, and only for `--version`, in an empty Codex folder of Switchboard's own (each copy once per
  size and time). The newest copy that may be chosen is the one used; one older than the newest Codex that wrote the
  person's chats is never run on them.
- Its numbers: from Codex's own session files (the chat reader, `chatfiles`), with no process and no network. Asked of
  Codex now only on the person's command, after their one yes, paced and one at a time: its own app server on stdio,
  four lines that only read, and an email that is dropped while the answer is parsed.
- Its login: never opened, never copied. Whether `auth.json` exists is all that is looked at, with os.stat.
- Starting it: start() and resume() only say how (a Launch); the command runs it. job() runs `codex exec` read-only,
  the prompt on standard input, an environment of a few plain names, and reads the answer from the one file it is
  given.
Everything outside this process goes through the Reach. The chat reader, the ask's lock, the person's yes and the
secret scrubber are attributes a test replaces (`chatfiles`, `locks`, `choices`, `redact`), each imported on first use.
"""
import dataclasses
import json
import os
import plistlib
import stat as stat_mod
from typing import Optional, Tuple
from urllib.parse import quote

from . import codex_facts, contract, words
from .contract import (ASK, CHAT_ID_RE, CHATS, DETECT, HOW, IN_APP, IN_TERMINAL, JOB, LIMITS, PRINT, READ, RESUME,
                       SEATS, START, START_APP, Answer, Found, Launch, Limits, Program, Provider, ProviderError, Seat,
                       Window)
from .run import child_env, shell_line, version_key

_JSON_MAX = 1 << 20             # a file of Switchboard's own, or an app's Info.plist, read at most this far
_ANSWER_BYTES = 4 * codex_facts.ANSWER_MAX


def _read_json(path, cap=_JSON_MAX):
    """A small JSON file of Switchboard's own, or {} when it is missing, too big or not JSON."""
    try:
        with open(path, 'rb') as f:
            raw = f.read(cap + 1)
        if len(raw) > cap:
            return {}
        data = json.loads(raw.decode('utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _json_object(path):
    """The file's JSON object; {} when there is no file; None when a file is there that is not one, is too big or
    cannot be read (it is then not Codex's to write over)."""
    try:
        with open(path, 'rb') as f:
            raw = f.read(_JSON_MAX + 1)
    except FileNotFoundError:
        return {}
    except OSError:
        return None
    try:
        data = json.loads(raw.decode('utf-8')) if len(raw) <= _JSON_MAX else None
    except (ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _number(x) -> Optional[float]:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or x != x or x in (float('inf'), float('-inf')):
        return None
    return float(x)


def _write_json(path, data):
    """Whole or not at all (core.write_json), in a folder only the person can open."""
    from switchboard import core
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    core.write_json(path, data)


def _drop_keys(pairs):
    """json object hook: an answer's email and account ids are left out as it is parsed, so they are never held."""
    return {k: v for k, v in pairs if k not in codex_facts.DROP_KEYS}


def _request_id(value):
    return isinstance(value, (int, str)) and not isinstance(value, bool)


class CodexProvider(Provider):
    id = 'codex'
    name = 'Codex'
    vendor = 'OpenAI'
    can = frozenset((DETECT, SEATS, LIMITS, ASK, CHATS, READ, START, START_APP, RESUME, JOB))
    sign_in = 'run codex login, or open the ChatGPT app'
    billing_prefixes = ('OPENAI_', 'CODEX_')
    job_question = words.HAND_TERMS     # OpenAI's terms on a plan sign-in for scripts: asked once on this Mac
    rule_files = ('AGENTS.md', 'AGENTS.override.md')

    def __init__(self, reach):
        super().__init__(reach)
        self.facts = codex_facts.FACTS
        self.stat = os.stat                 # the trust check's view of a file's owner, group and mode
        self._chatfiles = self._locks = self._choices = self._redact = None

    # ---------- what other modules are reached through (replaced whole in tests) ----------

    @property
    def chatfiles(self):
        if self._chatfiles is None:
            from . import codex_chats
            self._chatfiles = codex_chats
        return self._chatfiles

    @chatfiles.setter
    def chatfiles(self, value):
        self._chatfiles = value

    @property
    def locks(self):
        if self._locks is None:
            from . import run
            self._locks = run.try_lock
        return self._locks

    @locks.setter
    def locks(self, value):
        self._locks = value

    @property
    def choices(self):
        if self._choices is None:
            from . import choices
            self._choices = choices
        return self._choices

    @choices.setter
    def choices(self, value):
        self._choices = value

    @property
    def redact(self):
        if self._redact is None:
            from . import redact
            self._redact = redact
        return self._redact

    @redact.setter
    def redact(self, value):
        self._redact = value

    # ---------- folders ----------

    def codex_home(self) -> str:
        """The person's Codex folder: CODEX_HOME when set, else ~/.codex."""
        return self.reach.env.get(codex_facts.CODEX_HOME_ENV) or os.path.join(self.reach.home, '.codex')

    def _data(self, rel):
        return os.path.join(self.reach.data_dir, rel)

    # ---------- which program ----------

    def _candidates(self):
        """(real path, source, where, refused) for every copy, in the order 7.1 lists them, each file once."""
        facts, seen, out = self.facts, set(), []

        def add(path, source, where, refused=None):
            # R9: the file that is checked is the one that runs, so a link (npm puts one on PATH) is followed here
            # once and never again: a folder another user can change could re-point it between check and exec.
            real = os.path.realpath(path)
            if real not in seen:
                seen.add(real)
                out.append((real, source, where, refused))

        for folder in self.reach.path_dirs:
            for name in facts['program_names']:
                p = os.path.join(folder, name)
                if os.path.isfile(p) and os.access(p, os.X_OK):
                    add(p, 'path', codex_facts.WHERE_PATH)
        for folder in self.reach.app_dirs:
            try:
                names = sorted(os.listdir(folder))
            except OSError:
                continue
            for entry in names:
                if not entry.endswith('.app'):
                    continue
                app = os.path.join(folder, entry)
                if self._bundle_id(app) != facts['bundle_id']:
                    continue
                p = os.path.join(app, facts['app_program'])
                if os.path.isfile(p) and os.access(p, os.X_OK):
                    add(p, 'app', entry)
        p = os.path.join(self.codex_home(), facts['plugin_program'])
        if os.path.isfile(p):
            add(p, 'plugin', codex_facts.WHERE_PLUGIN, codex_facts.REFUSED_PLUGIN)
        return out

    @staticmethod
    def _bundle_id(app):
        path = os.path.join(app, 'Contents', 'Info.plist')
        try:
            if os.path.getsize(path) > _JSON_MAX:
                return None
            with open(path, 'rb') as f:
                info = plistlib.load(f)
        except Exception:               # a missing, unreadable or malformed plist: not Codex's app
            return None
        return info.get('CFBundleIdentifier') if isinstance(info, dict) else None

    def _trusted(self, path) -> bool:
        """R9: the program's real file and every folder above it, up to /, are owned by the person or root, are not
        world-writable, and are group-writable only by admin or wheel. os.stat only; nothing is opened or run."""
        import grp
        groups = set()
        for name in self.facts['trusted_groups']:
            try:
                groups.add(grp.getgrnam(name).gr_gid)
            except KeyError:
                pass
        me = os.getuid()
        p = os.path.realpath(path)
        while True:
            try:
                st = self.stat(p)
            except OSError:
                return False
            if st.st_uid not in (me, 0):
                return False
            if st.st_mode & stat_mod.S_IWOTH:
                return False
            if st.st_mode & stat_mod.S_IWGRP and st.st_gid not in groups:
                return False
            parent = os.path.dirname(p)
            if parent == p:
                return True
            p = parent

    def _probe_env(self):
        """(environment, folder) for a --version run in Switchboard's own empty Codex folder; None when that folder
        cannot be made (the version is then asked again next time)."""
        home = self._data(contract.PROBE_HOME)
        try:
            os.makedirs(home, mode=0o700, exist_ok=True)
            for folder in (os.path.dirname(home), home):
                os.chmod(folder, 0o700)
        except OSError:
            return None
        env = child_env(self.reach.env)
        env[codex_facts.CODEX_HOME_ENV] = home
        return env, home

    def _versions(self, paths):
        """Each path's version, from the cache when its size and time are unchanged, else from one `--version` run
        in Switchboard's own empty Codex folder. The cache file is shared with the other adapters: only the key
        'programs' is Codex's, and it is written back only when it changed (_keep_versions)."""
        cache_path = self._data(contract.PROGRAMS_FILE)
        cache = _read_json(cache_path).get('programs')
        cache = cache if isinstance(cache, dict) else {}
        out, fresh, probe = {}, {}, None
        for path in paths:
            try:
                st = self.stat(path)
            except OSError:
                out[path] = None
                continue
            mark = {'size': st.st_size, 'mtime_ns': st.st_mtime_ns}
            hit = cache.get(path)
            if isinstance(hit, dict) and hit.get('size') == mark['size'] and hit.get('mtime_ns') == mark['mtime_ns']:
                out[path] = hit.get('version') if isinstance(hit.get('version'), str) else None
                continue
            if probe is None:
                probe = self._probe_env() or False
            if not probe:
                out[path] = None
                continue
            env, home = probe
            ran = self.reach.run([path, '--version'], stdin=None, timeout=self.facts['probe_seconds'], env=env,
                                 cwd=home)
            version = None
            if ran.code == 0:
                m = codex_facts.VERSION_RE.search(ran.out or '')
                version = m.group(1) if m and version_key(m.group(1)) else None
                fresh[path] = dict(mark, version=version)
            out[path] = version
        if fresh:
            self._keep_versions(cache_path, fresh)
        return out

    @staticmethod
    def _keep_versions(cache_path, fresh):
        """Under the shared lock (never held while a program runs): the file is read again, only the key 'programs'
        changes, and every other key (the Claude adapter's 'claude') is written back as it was. A file that is there
        and is not a JSON object is left as it is; a write that fails is let go (the version is asked again next
        time)."""
        from switchboard import core
        try:
            with core.file_lock(contract.LOCK_NAME):
                doc = _json_object(cache_path)
                if doc is None:
                    return
                mine = doc.get('programs') if isinstance(doc.get('programs'), dict) else {}
                mine.update(fresh)
                doc['v'], doc['programs'] = 1, mine
                _write_json(cache_path, doc)
        except OSError:
            pass

    def _newest_seen(self):
        try:
            seen = self.chatfiles.newest_seen(self.reach)
        except Exception:               # the chat reader could not load or read: not known, said in problems
            return None, False
        return (seen if isinstance(seen, str) else None), True

    def detect(self) -> Found:
        cands = self._candidates()
        fit = [c for c in cands if c[3] is None and self._trusted(c[0])]
        versions = self._versions([c[0] for c in fit])
        fit_paths = {c[0] for c in fit}
        programs = []
        for path, source, where, refused in cands:
            version = versions.get(path)
            if refused is None and path not in fit_paths:
                refused = codex_facts.REFUSED_TRUST
            elif refused is None and version is None:
                refused = codex_facts.REFUSED_NO_VERSION
            programs.append(Program(path=path, version=version, source=source, where=where, refused=refused))
        programs.sort(key=lambda p: version_key(p.version), reverse=True)     # stable: a tie keeps list order
        chosen = next((p for p in programs if p.refused is None), None)
        seen, read_ok = self._newest_seen()
        problems = []
        if not read_ok:
            problems.append("Codex's chat files could not be read here, so the version that wrote them is not known.")
        if programs and chosen is None:
            problems.append(codex_facts.NO_FIT_PROGRAM.format(why=programs[0].refused))
        lo, hi = (version_key(v) for v in codex_facts.CHECKED_ON)
        tested = chosen is not None and lo <= version_key(chosen.version) <= hi
        login = os.path.join(self.codex_home(), codex_facts.LOGIN_FILE)
        signed_in = True if os.path.exists(login) else None        # os.stat only: the file is never opened
        return Found(provider=self.id, installed=bool(programs), program=chosen, programs=tuple(programs),
                     signed_in=signed_in, newest_seen=seen, tested=tested, problems=tuple(problems))

    def _runnable(self, chat_version=None) -> Program:
        """The program to run on the person's own Codex folder, or ProviderError: none fit, or older than the newest
        Codex that wrote their chats (or the chat about to be opened)."""
        found = self.detect()
        program = found.program
        if program is None:
            if found.problems and found.installed:
                raise ProviderError('not_installed', found.problems[-1])
            raise ProviderError('not_installed', codex_facts.NOT_INSTALLED)
        for seen in (found.newest_seen, chat_version):
            if seen and version_key(program.version) < version_key(seen):
                raise ProviderError('too_old', words.TOO_OLD.format(name=self.name, version=program.version,
                                                                    seen=seen))
        return program

    # ---------- seats and numbers ----------

    def _lined(self, limits: Limits, **changes) -> Limits:
        windows = tuple(w if w.line is not None else dataclasses.replace(w, line=codex_facts.line_of(w.label))
                        for w in limits.windows)
        return dataclasses.replace(limits, windows=windows, **changes)

    def _saved(self, ask=None, error=None) -> Limits:
        """The newer of the numbers Codex itself last wrote into its session files (the chat reader reads them) and
        the ones it gave when it was last asked (kept by _keep_reading), each window with its line; source says which
        ('file' or 'ask'). No process, no network."""
        now = self.reach.wall()
        try:
            saved = self.chatfiles.saved_limits(self.reach, now)
        except Exception:               # the reader could not load or read: no numbers, said in error
            saved, error = None, error or "Codex's saved numbers could not be read here."
        kept = self._kept(now)
        if kept is not None and (not isinstance(saved, Limits) or (saved.read_at or 0) < kept.read_at):
            return self._lined(kept, ask=ask, error=error)
        if not isinstance(saved, Limits):
            return Limits(provider=self.id, seat='codex', windows=(), read_at=None, source='none', ask=ask,
                          error=error)
        return self._lined(saved, ask=ask, error=error or saved.error)

    def _keep_reading(self, limits: Limits) -> None:
        """Codex's answer to an ask, kept in READINGS_FILE under the key 'codex' (numbers, labels, times and the plan's
        name only), so the next command shows it until a newer reading comes. The file is shared: under the shared lock
        only this key changes; a file that is not JSON is left as it is, and a write that fails is let go."""
        from switchboard import core
        entry = {'seat': limits.seat, 'read_at': limits.read_at, 'plan': limits.plan, 'reached': limits.reached,
                 'windows': [{'label': w.label, 'used_pct': w.used_pct, 'resets_at': w.resets_at,
                              'length_min': w.length_min} for w in limits.windows]}
        path = self._data(contract.READINGS_FILE)
        try:
            with core.file_lock(contract.LOCK_NAME):
                doc = _json_object(path)
                if doc is None:
                    return
                doc['v'], doc[self.id] = 1, entry
                _write_json(path, doc)
        except OSError:
            pass

    def _kept(self, now) -> Optional[Limits]:
        """The reading _keep_reading kept, when it is there, whole and no older than KEPT_DAYS; a window whose reset
        has passed since has refilled, and its use is not known (None)."""
        doc = _json_object(self._data(contract.READINGS_FILE)) or {}
        e = doc.get(self.id)
        if not isinstance(e, dict):
            return None
        read_at = _number(e.get('read_at'))
        if read_at is None or not 0 <= now - read_at <= codex_facts.KEPT_DAYS * 86400:
            return None
        windows = []
        for w in e.get('windows') if isinstance(e.get('windows'), list) else ():
            if not isinstance(w, dict) or not isinstance(w.get('label'), str):
                return None
            used, resets = _number(w.get('used_pct')), _number(w.get('resets_at'))
            length = w.get('length_min') if isinstance(w.get('length_min'), int) and \
                not isinstance(w.get('length_min'), bool) else None
            if resets is not None and resets <= now:
                used = None
            windows.append(Window(label=w['label'][:40], used_pct=used, resets_at=resets, length_min=length))
        plan, reached = e.get('plan'), e.get('reached')
        return Limits(provider=self.id, seat='codex', windows=tuple(windows), read_at=read_at, source='ask',
                      plan=plan if isinstance(plan, str) else None,
                      reached=reached if isinstance(reached, str) else None)

    def seats(self) -> Tuple[Seat, ...]:
        plan = self._saved().plan
        label = words.PLAN_NAMES.get(plan, words.PLAN_OTHER) if plan else self.name
        return (Seat(provider=self.id, key='codex', label=label, plan=plan, in_use=True),)

    def limits(self, ask: bool = False) -> Tuple[Limits, ...]:
        return (self._ask() if ask else self._saved(),)

    # ---------- the ask ----------

    def _ask_state(self):
        return _read_json(self._data(contract.ASK_FILE))

    @staticmethod
    def _paced(state, now):
        at, until = state.get('at'), state.get('backoff_until')
        if isinstance(at, (int, float)) and 0 <= now - at < codex_facts.ASK_PACE_S:
            return True
        return isinstance(until, (int, float)) and now < until

    def _ask_record(self, state, now, backoff):
        if backoff:
            last = state.get('backoff')
            last = last if isinstance(last, (int, float)) and last > 0 else 0
            wait = min(last * 2 if last else codex_facts.ASK_BACKOFF_FIRST_S, codex_facts.ASK_BACKOFF_MAX_S)
            data = {'v': 1, 'at': now, 'ok': False, 'backoff': wait, 'backoff_until': now + wait}
        else:
            data = {'v': 1, 'at': now, 'ok': True, 'backoff': 0, 'backoff_until': None}
        try:
            _write_json(self._data(contract.ASK_FILE), data)
        except OSError:
            pass                        # not kept: the next ask is not paced by this one, and nothing else changes

    def _ask(self) -> Limits:
        now = self.reach.wall()
        if not self.choices.ask_ok(self.id):
            return self._saved(ask=contract.ASK_OFF)
        if self._paced(self._ask_state(), now):
            return self._saved(ask=contract.ASK_PACED)
        program = self._runnable()
        with self.locks(contract.ASK_LOCK_NAME) as held:
            if not held:
                return self._saved(ask=contract.ASK_BUSY)
            state = self._ask_state()           # another command may have asked while this one looked
            if self._paced(state, now):
                return self._saved(ask=contract.ASK_PACED)
            outcome, result, why, backoff = self._exchange(program)
            self._ask_record(state, now, backoff)
        if outcome == contract.ASK_DONE:
            if result.error is None:
                self._keep_reading(result)
            return result
        if outcome == contract.ASK_API_KEY:
            return Limits(provider=self.id, seat='codex', windows=(), read_at=now, source='ask',
                          ask=contract.ASK_API_KEY, error=codex_facts.ASK_API_KEY)
        return self._saved(ask=contract.ASK_REFUSED, error=why)

    @staticmethod
    def message(method: str, id_: Optional[int] = None, params=None) -> str:
        """One line of the ask, exactly as sent. Refuses any method outside ASK_METHODS."""
        if method not in codex_facts.ASK_METHODS:
            raise ValueError('the ask never sends %r' % (method,))
        msg = {'method': method}
        if id_ is not None:
            msg['id'] = id_
        if params is not None:
            msg['params'] = params
        return json.dumps(msg, separators=(',', ':'))

    @staticmethod
    def init_params():
        from switchboard import core
        return {'clientInfo': {'name': 'switchboard', 'title': 'Switchboard', 'version': core.VERSION},
                'capabilities': {'experimentalApi': False, 'requestAttestation': False,
                                 'optOutNotificationMethods': list(codex_facts.OPT_OUT)}}

    def _await(self, pipe, want, deadline):
        """('ok', result) or ('error', code) for the answer to request `want`; ('timeout',) or ('closed',). A server
        request on the way is answered -32601; notifications are read past."""
        mono = self.reach.mono
        while True:
            left = deadline - mono()
            if left <= 0:
                return ('timeout',)
            line = pipe.receive(left)
            if line is None:
                return ('timeout',) if deadline - mono() <= 0.05 else ('closed',)
            try:
                msg = json.loads(line, object_pairs_hook=_drop_keys)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if 'method' in msg:
                if 'id' in msg and _request_id(msg['id']):
                    pipe.send(json.dumps({'id': msg['id'], 'error': codex_facts.SERVER_REQUEST_ANSWER},
                                         separators=(',', ':')))
                continue
            if msg.get('id') != want:
                continue
            if 'error' in msg:
                err = msg['error'] if isinstance(msg['error'], dict) else {}
                return ('error', err.get('code'))
            return ('ok', msg.get('result'))

    def _refusal(self, got):
        """(outcome, result, why, backoff) for an answer that was not 'ok'."""
        if got[0] == 'timeout':
            why = codex_facts.ASK_TIMED_OUT
        elif got[0] == 'closed':
            why = codex_facts.ASK_CLOSED
        elif got[1] == codex_facts.OVERLOADED:
            why = codex_facts.ASK_OVERLOADED
        else:
            why = codex_facts.ASK_CLOSED if got[1] is None else 'Codex refused the question (error %s).' % got[1]
        return contract.ASK_REFUSED, None, why, True

    def _exchange(self, program):
        """The four lines of 8.3 to Codex's own app server, and its answers. (outcome, Limits or None, why, backoff)."""
        facts = self.facts
        deadline = self.reach.mono() + facts['ask_seconds']
        try:
            pipe = self.reach.pipe([program.path] + list(codex_facts.ASK_ARGS), env=child_env(self.reach.env),
                                   cwd=self.reach.home)
        except (OSError, ProviderError):
            return contract.ASK_REFUSED, None, codex_facts.ASK_CLOSED, True
        try:
            return self._talk(pipe, deadline)
        except ProviderError:           # it went away while a line was being written
            return contract.ASK_REFUSED, None, codex_facts.ASK_CLOSED, True
        finally:
            pipe.close(facts['ask_grace'])

    def _talk(self, pipe, deadline):
        pipe.send(self.message('initialize', 1, self.init_params()))
        got = self._await(pipe, 1, deadline)
        if got[0] != 'ok':
            return self._refusal(got)
        home = got[1].get('codexHome') if isinstance(got[1], dict) else None
        if not isinstance(home, str) or os.path.realpath(home) != os.path.realpath(self.codex_home()):
            return contract.ASK_REFUSED, None, codex_facts.ASK_WRONG_HOME, True
        pipe.send(self.message('initialized'))
        pipe.send(self.message('account/read', 2, codex_facts.ASK_PARAMS['account/read']))
        got = self._await(pipe, 2, deadline)
        if got[0] != 'ok':
            return self._refusal(got)
        account = got[1].get('account') if isinstance(got[1], dict) else None
        if not isinstance(account, dict):
            return contract.ASK_REFUSED, None, codex_facts.ASK_SIGNED_OUT, False
        if account.get('type') != 'chatgpt':
            return contract.ASK_API_KEY, None, codex_facts.ASK_API_KEY, False
        plan = account.get('planType') if isinstance(account.get('planType'), str) else None
        pipe.send(self.message('account/rateLimits/read', 3, codex_facts.ASK_PARAMS['account/rateLimits/read']))
        got = self._await(pipe, 3, deadline)
        if got[0] != 'ok':
            return self._refusal(got)
        return contract.ASK_DONE, self._asked_limits(got[1], plan), None, False

    def _asked_limits(self, result, plan) -> Limits:
        """Limits from account/rateLimits/read: Codex's own bucket (rateLimitsByLimitId['codex']), else the single
        view when it names no other bucket. A bucket of another limit id is never used for room."""
        now = self.reach.wall()
        result = result if isinstance(result, dict) else {}
        by_id = result.get('rateLimitsByLimitId')
        snap = by_id.get(codex_facts.BUCKET) if isinstance(by_id, dict) else None
        if not isinstance(snap, dict):
            single = result.get('rateLimits')
            if isinstance(single, dict) and single.get('limitId') in (None, codex_facts.BUCKET):
                snap = single
        if not isinstance(snap, dict):
            return Limits(provider=self.id, seat='codex', windows=(), read_at=now, source='ask', plan=plan,
                          ask=contract.ASK_DONE, error=codex_facts.ASK_NO_BUCKET)
        windows = []
        for side in ('primary', 'secondary'):
            w = snap.get(side)
            if not isinstance(w, dict):
                continue
            mins = w.get('windowDurationMins')
            mins = mins if isinstance(mins, int) and not isinstance(mins, bool) else None
            label = codex_facts.label_of(mins) or side
            used = w.get('usedPercent')
            used = float(used) if isinstance(used, (int, float)) and not isinstance(used, bool) else None
            resets = w.get('resetsAt')
            resets = float(resets) if isinstance(resets, (int, float)) and not isinstance(resets, bool) else None
            windows.append(Window(label=label, used_pct=used, resets_at=resets, length_min=mins,
                                  line=codex_facts.line_of(label)))
        reached = snap.get('rateLimitReachedType')
        reached = reached if isinstance(reached, str) else None
        if reached is None and result.get('ordinaryUsageAllowed') is False:
            reached = 'usage_not_allowed'
        if plan is None and isinstance(snap.get('planType'), str):
            plan = snap['planType']
        return Limits(provider=self.id, seat='codex', windows=tuple(windows), read_at=now, source='ask', plan=plan,
                      reached=reached, ask=contract.ASK_DONE)

    # ---------- chats (the chat reader's) ----------

    def chats(self, folder):
        return self.chatfiles.chats(self.reach, folder, self.reach.wall())

    def read(self, chat, limits=contract.ReadLimits()):
        return self.chatfiles.read(self.reach, chat, limits)

    # ---------- starting it ----------

    def _from_name(self, handoff) -> str:
        """The name of the assistant the note came from, from the note's own sidecar ("from": {"provider"})."""
        data = _read_json(handoff.meta_path) if handoff.meta_path else {}
        frm = data.get('from')
        pid = frm.get('provider') if isinstance(frm, dict) else None
        from . import adapter_class, known  # only ids from the registry are ever used
        if isinstance(pid, str) and known(pid):
            return getattr(adapter_class(pid), 'name', '') or codex_facts.PROVIDER_NAMES.get(pid) or pid.capitalize()
        return codex_facts.SOMEONE

    @staticmethod
    def _app_of(program: Program) -> Optional[str]:
        """The app bundle holding the program, when it is inside one."""
        if program.source != 'app':
            return None
        p = program.path
        while p and p != os.path.dirname(p):
            if p.endswith('.app'):
                return p
            p = os.path.dirname(p)
        return None

    def start(self, folder, handoff, how):
        if how not in HOW:
            raise ValueError('how is one of %s' % (HOW,))
        if not os.path.isabs(folder):
            raise ValueError('the folder must be absolute')
        program = self._runnable()
        first = words.FIRST_WORDS.format(hop=handoff.hop, path=handoff.path, from_name=self._from_name(handoff))
        argv = (program.path,) + codex_facts.START_ARGS + (folder, first)
        env = child_env(self.reach.env)
        line = shell_line(argv, folder, self.reach.env)
        if how == IN_APP:
            url = codex_facts.APP_LINK.format(prompt=quote(first, safe=''), path=quote(folder, safe=''))
            app = self._app_of(program)
            opener = ('/usr/bin/open', '-a', app, url) if app else ('/usr/bin/open', url)
            return Launch(provider=self.id, how=IN_APP, program=program, argv=opener, env=env, cwd=folder,
                          line=line, url=url, app=app, first_words=first)
        return Launch(provider=self.id, how=how, program=program, argv=argv, env=env, cwd=folder, line=line,
                      first_words=first)

    def resume(self, chat, handoff, how):
        if how == IN_APP:
            return self._unsupported(RESUME, codex_facts.NO_APP_RESUME)
        if how not in (IN_TERMINAL, PRINT):
            raise ValueError('how is one of %s' % (HOW,))
        if not isinstance(chat.id, str) or not CHAT_ID_RE.match(chat.id):
            raise ProviderError('refused', codex_facts.BAD_CHAT_ID)
        if not os.path.isabs(chat.folder or ''):
            raise ProviderError('refused', codex_facts.BAD_CHAT_ID)
        now = self.reach.wall()
        if chat.live or chat.working or (chat.last_at is not None and 0 <= now - chat.last_at < 60):
            raise ProviderError('busy', codex_facts.CHAT_OPEN)
        program = self._runnable(chat.version)
        back = words.BACK_WORDS.format(hop=handoff.hop, path=handoff.path, other_name=self._from_name(handoff))
        argv = (program.path,) + codex_facts.RESUME_ARGS + (chat.folder, chat.id, back)
        return Launch(provider=self.id, how=how, program=program, argv=argv, env=child_env(self.reach.env),
                      cwd=chat.folder, line=shell_line(argv, chat.folder, self.reach.env), first_words=back)

    # ---------- one read-only job ----------

    def job(self, folder, prompt, out_path, timeout=900.0):
        if not os.path.isabs(folder) or not os.path.isabs(out_path):
            raise ValueError('the folder and the answer file must be absolute')
        program = self._runnable()
        argv = [program.path] + [a.format(folder=folder, out=out_path) for a in codex_facts.JOB_ARGS]
        ran = self.reach.run(argv, stdin=prompt, timeout=timeout, env=child_env(self.reach.env, job=True), cwd=folder)
        if ran.missing:
            return Answer(provider=self.id, ok=False, path=out_path, why=codex_facts.JOB_MISSING)
        if ran.timed_out:
            return Answer(provider=self.id, ok=False, path=out_path, took_s=ran.took_s,
                          why=codex_facts.JOB_TIMED_OUT.format(span=self._span(timeout)))
        text = self._answer_text(out_path)
        if ran.code == 0 and text.strip():
            return Answer(provider=self.id, ok=True, text=text, path=out_path, took_s=ran.took_s, code=0)
        why = codex_facts.JOB_NO_ANSWER.format(code=ran.code) + self._last_words(ran.err)
        return Answer(provider=self.id, ok=False, path=out_path, took_s=ran.took_s, code=ran.code, why=why)

    @staticmethod
    def _span(seconds) -> str:
        n = max(1, int(round(seconds / 60.0)))
        return '%d minute%s' % (n, '' if n == 1 else 's')

    @staticmethod
    def _answer_text(path) -> str:
        try:
            with open(path, 'rb') as f:
                raw = f.read(_ANSWER_BYTES)
        except OSError:
            return ''
        return raw.decode('utf-8', 'replace')[:codex_facts.ANSWER_MAX]

    def _last_words(self, err) -> str:
        """The last line Codex wrote to standard error, secrets taken out, on one line; '' when there is none or
        when the scrubber cannot load (then nothing of it is said at all)."""
        lines = [ln for ln in (err or '').splitlines() if ln.strip()]
        if not lines:
            return ''
        try:
            scrubber = self.redact
            text, _ = scrubber.scrub(lines[-1][-4000:], known=scrubber.env_secrets(self.reach.env))
        except Exception:               # no scrubber here: say nothing of what it printed
            return ''
        return codex_facts.JOB_LAST_WORDS.format(words=words.one_line(text, 200))
