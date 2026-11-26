"""Claude as a provider: Claude Code, on the accounts Switchboard already keeps for it.

Seats and numbers come from the account code's read functions only (switchboard.accounts.api: accounts(), readings(),
auto_settings()), which read Switchboard's own files and never the Keychain or the network; the account in use is
the one Switchboard's state file names. This module never opens a login, the account list or Claude Code's settings.

Which program runs: every claude on PATH, each file in Claude Code's own versions folder, and each editor extension's
private copy; the newest one that only the person (or the system) can change. Its version comes from the folder name
where it has one, else from one --version run, kept by the file's size and time.

A start is `claude "<first words>"` in the folder; opening a chat again is `claude --resume ID "<words>"` in the
chat's own folder, refused while a window holds the chat or it was written in the last minute, and refused for a
program older than the one that last wrote it. Never a flag that loosens the person's permission checks, never -p.
The environment is the person's own (Reach.env) without the names that move a run onto a key or a cloud account. This
module only says how to start; the command starts it.

Reading chats is claude_chats.py's (reached through `chatfiles`); the session hooks and the account code's own chat
check are reached through bridge.py only.
"""
import grp
import json
import os
import stat
from typing import List, Optional, Tuple

from switchboard import core

from . import claude_facts, contract, run, words
from .contract import (CHAT_ID_RE, IN_APP, IN_TERMINAL, PRINT, ChatRef, Found, Handoff, Launch, Limits, Program,
                       Provider, ProviderError, Seat, Window)

NAME = 'Claude Code'                    # the app's name in a sentence (words.TOO_OLD)
SIDECAR_MAX = 64 << 10                  # bytes of a note's own record read for the name of the side it came from
REFUSED = 'its folder can be changed by other users'
GROUPS_OK = ('admin', 'wheel')          # macOS's own /Applications is root:admin, group-writable
NOT_FOUND = 'Claude Code is not on this Mac (no claude on your PATH, in its own install or in an editor extension).'
NONE_FIT = ('Every copy of Claude Code here is in a folder other users can change, so Switchboard runs none of them. '
            'Fix the folder\'s permissions, or install Claude Code again.')
NO_ACCOUNTS = 'Switchboard could not read its list of Claude accounts just now. Run this again in a minute.'
NO_NUMBERS = 'No numbers have been read for this account yet.'
NOT_READ = 'Switchboard could not read the saved numbers just now.'
CANNOT_CARRY = 'This account cannot carry work now ({status}).'
BAD_CHAT = 'That is not a Claude Code chat id.'
BUSY = ('That chat is open in a Claude Code window, or was written in the last minute. Two windows writing one chat '
        'can lose messages: close it there first, or paste the line into it.')
NO_READER = 'Reading Claude Code chats is not in this build.'
NO_ASK = ('Switchboard reads Claude\'s numbers itself, as it always has; it does not ask Claude Code for them. '
          'The saved numbers are the current ones.')
# The name of the side a note came from, for each provider id the registry knows (the Codex adapter keeps the same).
PROVIDER_NAMES = {'claude': 'Claude', 'codex': 'Codex'}
ANOTHER = 'another assistant'           # no sidecar, one that cannot be read, or an id the registry does not know


class ClaudeProvider(Provider):
    """Claude Code. Attributes a test may replace: facts (claude_facts.FACTS), chatfiles (claude_chats), bridge
    (bridge.py) and accounts (switchboard.accounts.api); the last three are imported on first use."""
    id = 'claude'
    name = 'Claude'
    vendor = 'Anthropic'
    can = frozenset((contract.DETECT, contract.SEATS, contract.LIMITS, contract.CHATS, contract.READ,
                     contract.START, contract.RESUME))
    app_name = NAME
    sign_in = 'run /login in Claude Code'
    billing_prefixes = ('ANTHROPIC_', 'CLAUDE_CODE_')
    cannot = {contract.JOB: words.NO_CLAUDE_JOB, contract.START_APP: words.NO_CLAUDE_LINK}
    own_rules = '~/.claude/CLAUDE.md, memory, skills, hooks'
    account_list = True                 # its seats are the accounts Switchboard keeps (the rows of the page's list)
    session_env = 'CLAUDE_CODE_SESSION_ID'
    rule_files = ('CLAUDE.md', 'CLAUDE.local.md', os.path.join('.claude', 'CLAUDE.md'))
    rule_dirs = (os.path.join('.claude', 'rules'),)

    def __init__(self, reach):
        super().__init__(reach)
        self.facts = claude_facts.FACTS
        self._chatfiles = self._bridge = self._accounts = None

    # ---------- what it reaches, each imported only when used ----------

    @property
    def chatfiles(self):
        if self._chatfiles is None:
            from . import claude_chats
            self._chatfiles = claude_chats
        return self._chatfiles

    @chatfiles.setter
    def chatfiles(self, value):
        self._chatfiles = value

    @property
    def bridge(self):
        if self._bridge is None:
            from . import bridge
            self._bridge = bridge
        return self._bridge

    @bridge.setter
    def bridge(self, value):
        self._bridge = value

    @property
    def accounts(self):
        if self._accounts is None:
            from switchboard.accounts import api
            self._accounts = api
        return self._accounts

    @accounts.setter
    def accounts(self, value):
        self._accounts = value

    @accounts.deleter
    def accounts(self):
        self._accounts = None

    def _claude_dir(self) -> str:
        """Claude's folder: CLAUDE_CONFIG_DIR when the person set it, else ~/.claude (as core.claude_dir does)."""
        return self.reach.env.get('CLAUDE_CONFIG_DIR') or os.path.join(self.reach.home, '.claude')

    # ---------- which program ----------

    def detect(self) -> Found:
        programs = self._programs()
        chosen = next((p for p in programs if p.refused is None), None)
        lo, hi = (run.version_key(v) for v in self.facts['checked'])
        tested = chosen is not None and bool(chosen.version) and lo <= run.version_key(chosen.version) <= hi
        problems = () if chosen is not None else ((NONE_FIT,) if programs else (NOT_FOUND,))
        return Found(provider=self.id, installed=bool(programs), program=chosen, programs=tuple(programs),
                     signed_in=None, newest_seen=None, tested=tested, problems=problems)

    def _candidates(self) -> List[Tuple[str, str, Optional[str]]]:
        """(path, source, version from its folder or None), in the order a tie is won: PATH, the versions folder,
        the editor extensions."""
        out = []
        for folder in self.reach.path_dirs:
            for name in self.facts['program_names']:
                out.append((os.path.join(folder, name), 'path', None))
        versions = os.path.join(self.reach.home, self.facts['versions_dir'])
        for name in _names(versions):
            if claude_facts.VERSION_NAME_RE.match(name):
                out.append((os.path.join(versions, name), 'versions', name))
        pattern = _compile(self.facts['extension_re'])
        for rel in self.facts['extension_dirs']:
            folder = os.path.join(self.reach.home, rel)
            for name in _names(folder):
                m = pattern.match(name)
                if m:
                    out.append((os.path.join(folder, name, self.facts['extension_program']), 'extension', m.group(1)))
        return out

    def _programs(self) -> List[Program]:
        """Every copy found, newest first (a tie keeps the candidates' order); each one's realpath once."""
        never = tuple(os.path.join(os.path.realpath(os.path.join(self.reach.home, rel)), '')
                      for rel in self.facts['not_candidates'])
        seen, found, probe = set(), [], []
        for path, source, version in self._candidates():
            if not _runnable(path):
                continue
            real = os.path.realpath(path)
            if real in seen or real.startswith(never):
                continue
            seen.add(real)
            version = version or _version_from_place(real, self.reach.home, self.facts)
            refused = None if _trusted(real) else REFUSED
            if refused is None and version is None:
                probe.append(real)
            found.append([real, version, source, refused])
        if probe:
            versions = self._probe(probe)
            for item in found:
                if item[1] is None and item[0] in versions:
                    item[1] = versions[item[0]]
        programs = [Program(path=p, version=v, source=s, where=claude_facts.WHERE.get(s, s), refused=r)
                    for p, v, s, r in found]
        order = {id(p): i for i, p in enumerate(programs)}
        return sorted(programs, key=lambda p: (_neg(run.version_key(p.version)), order[id(p)]))

    def _probe(self, paths: List[str]) -> dict:
        """Each program's own --version, once per file size and time (PROGRAMS_FILE keeps the answers under
        'claude'). No lock is held while a program runs."""
        cache_path = os.path.join(self.reach.data_dir, contract.PROGRAMS_FILE)
        kept = core.read_json(cache_path).get('claude')
        kept = kept if isinstance(kept, dict) else {}
        out, fresh = {}, {}
        for path in paths:
            try:
                st = os.stat(path)
            except OSError:
                continue
            mark = [st.st_size, st.st_mtime_ns]
            entry = kept.get(path)
            if isinstance(entry, list) and len(entry) == 3 and entry[:2] == mark and isinstance(entry[2], str):
                out[path] = entry[2]
                continue
            ran = self.reach.run([path, '--version'], stdin=None, timeout=claude_facts.VERSION_PROBE_S,
                                 env=run.child_env(self.reach.env), cwd=self.reach.home)
            first = (ran.out or '').strip().splitlines()[:1] if ran.code == 0 else []
            m = claude_facts.VERSION_RE.match(first[0]) if first else None
            if m:
                out[path] = fresh[path] = m.group(1)
        if fresh:
            self._keep_versions(cache_path, fresh)
        return out

    def _keep_versions(self, cache_path: str, fresh: dict) -> None:
        """The file is shared with the other adapters (the Codex one writes it too): under the shared lock it is read,
        only the key 'claude' is changed, and it is written back with every other key as it was. A file that is there
        but is not a JSON object is left as it is (it is not Claude's to drop); the version is asked again next
        time."""
        try:
            with core.file_lock(contract.LOCK_NAME):
                doc = _json_object(cache_path)
                if doc is None:
                    return
                mine = doc.get('claude') if isinstance(doc.get('claude'), dict) else {}
                for path, version in fresh.items():
                    try:
                        st = os.stat(path)
                    except OSError:
                        continue
                    mine[path] = [st.st_size, st.st_mtime_ns, version]
                doc['claude'] = dict(sorted(mine.items())[-64:])
                _private_folder(os.path.dirname(cache_path))
                core.write_json(cache_path, doc)
        except OSError:
            pass                        # a version not kept is asked again next time

    def _chosen(self) -> Program:
        found = self.detect()
        if found.program is None:
            raise ProviderError('not_installed', found.problems[0] if found.problems else NOT_FOUND)
        return found.program

    # ---------- accounts and numbers ----------

    def _account_list(self) -> list:
        try:
            return list(self.accounts.accounts())
        except Exception:               # the account code's own failure, whatever it is, is one sentence here
            raise ProviderError('unreadable', NO_ACCOUNTS)

    def seats(self) -> Tuple[Seat, ...]:
        active = core.read_json(os.path.join(self.reach.data_dir, 'state.json')).get('active')
        return tuple(Seat(provider=self.id, key=a.id, label=a.alias or a.email, plan=a.plan,
                          in_use=bool(active) and a.email == active) for a in self._account_list())

    def limits(self, ask: bool = False):
        """Each account's numbers as the account code last read them (it reads them itself, every 5 minutes for the
        account in use). ask=True is the contract's ask method, which Claude does not have (ASK is not in `can`): it
        answers Unsupported before anything is read, as every method outside `can` does; the saved numbers are
        limits()."""
        if ask and contract.ASK not in self.can:
            return self._unsupported(contract.ASK, NO_ASK)
        accounts = self._account_list()
        try:
            readings, error = dict(self.accounts.readings()), None
        except Exception:
            readings, error = {}, NOT_READ
        try:
            settings = dict(self.accounts.auto_settings())
        except Exception:
            settings = {}
        out = []
        for a in accounts:
            r = readings.get(a.id)
            windows = tuple(self._window(w, settings) for w in (getattr(r, 'windows', None) or ()))
            read_at = getattr(r, 'read_at', None)
            if read_at is None:
                read_at = getattr(r, 'checked_at', None)         # older lines have no checked_at
            why = error or (getattr(r, 'error', None) if r is not None else NO_NUMBERS)
            reached = None
            if a.status not in claude_facts.OK_STATUSES:
                reached, why = a.status, CANNOT_CARRY.format(status=a.status)
            out.append(Limits(provider=self.id, seat=a.id, windows=windows, read_at=read_at,
                              source='engine' if r is not None else 'none', plan=a.plan, reached=reached,
                              error=why or None, ask=None))
        return tuple(out)

    def _window(self, w, settings) -> Window:
        name = getattr(w, 'name', '') or ''
        length, line_name = claude_facts.WINDOWS.get(name, (None, None))
        label = name
        if name.startswith(claude_facts.MODEL_PREFIX):
            label, line_name = name[len(claude_facts.MODEL_PREFIX):] or name, claude_facts.MODEL_LINE
        line = None
        if line_name is not None:
            line = _number(settings.get(line_name))
            if line is None:
                line = claude_facts.DEFAULT_LINES[line_name]
        return Window(label=label, used_pct=_number(getattr(w, 'pct', None)),
                      resets_at=_number(getattr(w, 'resets_at', None)), length_min=length, line=line)

    # ---------- chats (claude_chats.py's) ----------

    def chats(self, folder: str):
        try:
            reader = self.chatfiles
        except ImportError:
            return self._unsupported(contract.CHATS, NO_READER)
        return reader.chats(self.reach, folder, self.reach.wall())

    def read(self, chat: ChatRef, limits: contract.ReadLimits = contract.ReadLimits()):
        try:
            reader = self.chatfiles
        except ImportError:
            return self._unsupported(contract.READ, NO_READER)
        return reader.read(self.reach, chat, limits)

    # ---------- starting it ----------

    def start(self, folder: str, handoff: Handoff, how: str):
        if how == IN_APP:
            return self._unsupported(contract.START_APP, words.NO_CLAUDE_LINK)
        program = self._chosen()
        first = words.FIRST_WORDS.format(hop=handoff.hop, path=handoff.path, from_name=self._other_name(handoff))
        return self._launch(program, how, [program.path, first], folder, first)

    def resume(self, chat: ChatRef, handoff: Handoff, how: str):
        if how == IN_APP:
            return self._unsupported(contract.RESUME, words.NO_CLAUDE_LINK)
        if not isinstance(chat.id, str) or not CHAT_ID_RE.match(chat.id):
            raise ProviderError('refused', BAD_CHAT)
        program = self._chosen()
        if chat.version and run.version_key(program.version) < run.version_key(chat.version):
            raise ProviderError('too_old', words.TOO_OLD.format(name=NAME, version=program.version or '?',
                                                                seen=chat.version))
        if self._in_use(chat):
            raise ProviderError('busy', BUSY)
        back = words.BACK_WORDS.format(hop=handoff.hop, path=handoff.path, other_name=self._other_name(handoff))
        return self._launch(program, how, [program.path, claude_facts.RESUME_FLAG, chat.id, back], chat.folder, back)

    def _launch(self, program: Program, how: str, argv: list, cwd: str, first: str) -> Launch:
        how = how if how in (IN_TERMINAL, PRINT) else PRINT
        return Launch(provider=self.id, how=how, program=program, argv=tuple(argv), env=run.child_env(self.reach.env),
                      cwd=cwd, line=run.shell_line(argv, cwd, self.reach.env), first_words=first)

    def _other_name(self, handoff: Handoff) -> str:
        """The name of the side the note came from, as the note's own record says it (the sidecar `<hop>.json`
        beside the note, `"from": {"provider": id}`; it holds no chat text). Only an id the registry knows is used,
        named as its own adapter names it (else from PROVIDER_NAMES, else that id with a capital); anything else is
        "another assistant"."""
        try:
            with open(handoff.meta_path, 'rb') as f:
                doc = json.loads(f.read(SIDECAR_MAX + 1).decode('utf-8'))
        except (OSError, ValueError, TypeError, UnicodeDecodeError, RecursionError):
            return ANOTHER
        frm = doc.get('from') if isinstance(doc, dict) else None
        pid = frm.get('provider') if isinstance(frm, dict) else None
        from . import adapter_class, known  # only ids from the registry are ever used
        if not isinstance(pid, str) or not known(pid):
            return ANOTHER
        return getattr(adapter_class(pid), 'name', '') or PROVIDER_NAMES.get(pid) or pid.capitalize()

    def _in_use(self, chat: ChatRef) -> bool:
        """A chat is open when a live window holds it, or it was written in the last minute; and when the account
        code's own check (through the bridge, where this build has it) says so."""
        if self._held_by_window(chat.id):
            return True
        try:
            written = os.stat(chat.path).st_mtime
        except (OSError, TypeError, ValueError):
            written = None
        if written is not None and self.reach.wall() - written < claude_facts.WRITING_S:
            return True
        try:
            return self.bridge.chat_in_use(chat.id, chat.path) is True
        except Exception:               # a check that fails says nothing either way
            return False

    def _held_by_window(self, cid: str) -> bool:
        """Claude Code's window files (<Claude's folder>/sessions/<pid>.json): one whose process is alive and whose
        sessionId is this chat. Only a live window's file is opened, only that one key is kept, and nothing named
        like a key file is ever opened."""
        folder = os.path.join(self._claude_dir(), claude_facts.SESSIONS_DIR)
        for name in _names(folder):
            if not claude_facts.WINDOW_RE.match(name) or contract.LOGIN_NAME_RE.match(name):
                continue
            if not self.reach.alive(int(name[:-5])):
                continue
            path = os.path.join(folder, name)
            try:
                if not stat.S_ISREG(os.lstat(path).st_mode):
                    continue
                with open(path, 'rb') as f:
                    raw = f.read(claude_facts.WINDOW_MAX + 1)
                doc = json.loads(raw.decode('utf-8')) if len(raw) <= claude_facts.WINDOW_MAX else None
            except (OSError, ValueError, UnicodeDecodeError, RecursionError):
                continue
            session = doc.get('sessionId') if isinstance(doc, dict) else None
            del doc
            if session == cid:
                return True
        return False

    def job(self, folder, prompt, out_path, timeout=900.0):
        return self._unsupported(contract.JOB, words.NO_CLAUDE_JOB)


# ---------- small helpers ----------

def _json_object(path: str) -> Optional[dict]:
    """The file's JSON object; {} when there is no file; None when a file is there that is not one (or cannot be
    read)."""
    try:
        with open(path, 'rb') as f:
            data = json.loads(f.read().decode('utf-8'))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, UnicodeDecodeError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _names(folder: str) -> List[str]:
    try:
        return sorted(os.listdir(folder))
    except OSError:
        return []


_PATTERNS = {}


def _compile(pattern: str):
    import re
    if pattern not in _PATTERNS:
        _PATTERNS[pattern] = re.compile(pattern)
    return _PATTERNS[pattern]


def _runnable(path: str) -> bool:
    try:
        return os.path.isfile(path) and os.access(path, os.X_OK)
    except (OSError, ValueError):
        return False


def _version_from_place(real: str, home: str, facts: dict) -> Optional[str]:
    """A PATH copy that is a link into the versions folder or an extension is named by that place, unrun."""
    name = os.path.basename(real)
    versions = os.path.realpath(os.path.join(home, facts['versions_dir']))
    if os.path.dirname(real) == versions and claude_facts.VERSION_NAME_RE.match(name):
        return name
    pattern = _compile(facts['extension_re'])
    for rel in facts['extension_dirs']:
        root = os.path.join(os.path.realpath(os.path.join(home, rel)), '')
        if real.startswith(root):
            m = pattern.match(real[len(root):].split(os.sep, 1)[0])
            if m and real.endswith(os.sep + facts['extension_program'].replace('/', os.sep)):
                return m.group(1)
    return None


def _trusted(real: str) -> bool:
    """The file and every folder above it, to /, can be changed only by the person or root: none is
    world-writable, none group-writable by a group other than admin or wheel, none owned by anyone else. os.stat
    only (a signature check reads a whole app, seconds each time)."""
    me = os.getuid()
    path = real
    while True:
        try:
            st = os.stat(path)
        except OSError:
            return False
        if st.st_uid not in (me, 0) or st.st_mode & stat.S_IWOTH:
            return False
        if st.st_mode & stat.S_IWGRP and _group(st.st_gid) not in GROUPS_OK:
            return False
        parent = os.path.dirname(path)
        if parent == path:
            return True
        path = parent


def _group(gid: int) -> Optional[str]:
    try:
        return grp.getgrgid(gid).gr_name
    except (KeyError, OverflowError):
        return None


def _neg(key: Tuple[int, ...]) -> Tuple[int, ...]:
    """A sort key that puts the newest version first and an unknown one last."""
    return tuple(-x for x in key) if key else (1,)


def _number(x) -> Optional[float]:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or x != x or x in (float('inf'), float('-inf')):
        return None
    return float(x)


def _private_folder(folder: str) -> None:
    os.makedirs(folder, mode=0o700, exist_ok=True)
    if stat.S_IMODE(os.stat(folder).st_mode) & 0o077:
        os.chmod(folder, 0o700)
