"""The frozen contract of Switchboard's other assistants: the types every adapter and every module beside it speaks.

A provider is one assistant app the person already has on this Mac: Claude Code (Anthropic) and Codex (OpenAI) today.
Each provider is one adapter, a subclass of Provider below, in its own module of this package. An adapter answers for
its own app only: is it installed, which program is the right one to run, what its limits are, which of its chats ran
in a folder, what one of those chats holds (read from the chat's own file on this Mac), how to start its app on a
note, and how to run one read-only job.

Rules every adapter keeps (docs/how-it-works.md, "Other assistants", says why for each):
- The vendor's own app keeps the vendor's own login. No module of this package opens, copies, keeps, renews or sends a
  login: no file in LOGIN_FILES, under LOGIN_DATA_FOLDERS or named in LOGIN_BASENAMES is ever opened, and no Keychain
  item is ever read. The account list (ENGINE_DATA_FILES) holds no secret and is read only by the account code, which
  this package reaches through switchboard.accounts.api alone.
- Everything outside the process goes through one Reach: the environment, the folders, programs, clocks, and whether a
  process is alive. Tests hand it stand-ins, so no test ever reaches a real app, a real chat or the network.
- An adapter never starts its app itself. start() and resume() return a Launch (the exact argument list, environment
  and folder); the command runs it, in this terminal, or opens the app's own link, or prints the one line to run.
  Only job() runs a program and waits for it, under a time limit, and it writes its answer only to the file it is
  given.
- What an adapter cannot do is answered, never raised: every method a provider lacks returns Unsupported, and the
  flags in `can` say ahead which ones work. A failure the person can act on raises ProviderError with one plain
  sentence. Nothing else is raised to the command.
- A chat is read with an allow-list: only the fields this file's Event can carry, per line kind, as each adapter's
  facts table names them with the app version it was checked on. A line of a shape not in the table is counted and
  skipped. A file whose shape is not known yields a smaller Transcript (ReadInfo.degraded says why), never a guess.
- Nothing of a chat goes into an argument list, a log line, the History, a notification or a hook's text. A chat's
  words are written only into the note file the person asked for (mode 0600), and to the terminal of the person who
  asked, with control characters taken out.

Types are frozen dataclasses so a value handed between modules cannot change under its reader. Fields that can hold an
environment, a chat's words or a program's output are left out of repr(), so a traceback or a test failure never
prints them. Python 3.9 and the standard library only. Nothing here does anything but name things: the behaviour is in
the modules beside it.
"""
import re
from dataclasses import dataclass, field
from typing import Callable, FrozenSet, Mapping, Optional, Tuple, Union

CONTRACT_VERSION = 2

# ---------- names ----------

# A provider's id: the word the person types (`switchboard add codex`). Lowercase, short, never a command's name.
ID_RE = re.compile(r'^[a-z][a-z0-9]{1,15}\Z')

# A chat's id as both apps write it (Claude Code 2.1.329 sessionId; Codex 0.169.1 thread id): a lowercase UUID. Checked
# by every adapter before an id goes into a path or an argument list.
CHAT_ID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z')

# What an adapter can do. `can` holds the ones it does; every other method answers Unsupported.
DETECT = 'detect'           # is the app here, which program, which version
SEATS = 'seats'             # the accounts it is signed in with (Claude: every saved account; Codex: the one login)
LIMITS = 'limits'           # its numbers, from what is saved on this Mac (no process, no network)
ASK = 'ask'                 # its numbers asked of the app now (one paced read through the app's own program)
CHATS = 'chats'             # which of its chats ran in a folder
READ = 'read'               # one chat as a Transcript, from the chat's own file
START = 'start'             # its terminal app, in a folder, on a note
START_APP = 'start_app'     # its desktop app, in a folder, on a note (the person presses send)
RESUME = 'resume'           # one of its own chats opened again, on a note
JOB = 'job'                 # one read-only job in a folder, its answer as text
NOTE = 'note'               # the app writes its own short note of a chat (needs room; not in the first build)
CAPABILITIES = frozenset((DETECT, SEATS, LIMITS, ASK, CHATS, READ, START, START_APP, RESUME, JOB, NOTE))

# How much room a provider has, in the words the person was shown ("has room", "is out until").
ROOM = 'room'               # at least one of its accounts is under its lines
OUT = 'out'                 # every account is at or over a line; back_at says when the first refills, when known
UNKNOWN = 'unknown'         # its numbers are not known (never read, unreadable, or too old to trust)
NOT_ADDED = 'not_added'     # the person has not added it (`switchboard add PROVIDER`)
NOT_INSTALLED = 'not_installed'
SIGNED_OUT = 'signed_out'   # its app is here but says it is not signed in
ROOM_STATES = (ROOM, OUT, UNKNOWN, NOT_ADDED, NOT_INSTALLED, SIGNED_OUT)

# How an ask of the app for its numbers ended (Limits.ask). None: no ask was made.
ASK_DONE = 'done'           # asked now; these numbers are its answer
ASK_PACED = 'paced'         # asked too recently, or backing off after a refusal: the saved numbers are shown
ASK_BUSY = 'busy'           # another Switchboard command is asking now (the lock is held)
ASK_REFUSED = 'refused'     # it did not answer, timed out, or said it was overloaded
ASK_API_KEY = 'api_key'     # the app is on an API key here, not a plan: its plan limits do not apply
ASK_OFF = 'off'             # the person has not yet said yes to starting the app's helper for this (asked once)
ASK_OUTCOMES = (ASK_DONE, ASK_PACED, ASK_BUSY, ASK_REFUSED, ASK_API_KEY, ASK_OFF)

# Where a Launch runs.
IN_TERMINAL = 'terminal'    # this terminal: the command replaces itself with the program (exec)
IN_APP = 'app'              # the vendor's desktop app, through its documented link; the person presses send
PRINT = 'print'             # not in a terminal: the note is written and the one line to run is printed
HOW = (IN_TERMINAL, IN_APP, PRINT)

# What one event of a chat is. Text is the chat's own words, already cut to the bounds the reader keeps.
PERSON = 'person'           # what the person typed (their own message, or one typed while the assistant worked)
ASSISTANT = 'assistant'     # what the assistant said (text only; private reasoning is never kept)
TOOL_CALL = 'tool_call'     # the assistant called a tool: name, and a short form of its input
TOOL_RESULT = 'tool_result' # what the tool gave back: ok or not when the app says, and a short excerpt
SUMMARY = 'summary'         # the app's own summary of what came before (Claude Code's compaction, Codex's compacted)
BOUNDARY = 'boundary'       # where that summary took over: everything before it is in the summary
PLAN = 'plan'               # the assistant's latest plan or to-do list, as text, one item per line
ERROR = 'error'             # the app said it could not answer (a limit, a server error): its kind only
NOTICE = 'notice'           # a message in the person's place that the app or a tool wrote (a task's notice, a
                            # command's output, a scheduled prompt, a note Switchboard started the chat on): quoted
                            # in the record, never treated as the person's words. Event.origin says which.
EVENT_KINDS = (PERSON, ASSISTANT, TOOL_CALL, TOOL_RESULT, SUMMARY, BOUNDARY, PLAN, ERROR, NOTICE)

# How much of a chat a reader keeps of one event, so memory stays bounded whatever the file holds (characters).
KEEP_TOOL_INPUT = 2000
# The longest command that is split the shell's way, in characters (lines.shell_words()): as much of a command as a
# note keeps. A longer one is a script or a file's text given in one command, and shlex reads it a character at a
# time, slower still inside one long quoted word (2 s for 200,000 characters). Counted on one real 36 MB chat: 880
# commands up to this length took 0.4 s to split, the 339 longer ones 20 s, of a read that has 5.
SPLIT_MAX = KEEP_TOOL_INPUT
KEEP_TOOL_RESULT = 4000
KEEP_PERSON = 24000         # the person's newest message is kept to this (the head and the tail, the cut said)

# Which chats a provider lists for a folder: live ones, and ones written in the last CHATS_DAYS days.
CHATS_DAYS = 7

# ---------- what is never opened, never passed on ----------

# Login files, relative to the home folder. No module of this package opens one of these, for reading or writing,
# and the guard test proves it for every command. Their existence may be checked (os.stat).
LOGIN_FILES = ('.codex/auth.json', '.claude/.credentials.json', '.claude.json')
# Login folders, relative to Switchboard's data folder: the saved logins and their recovery copies.
LOGIN_DATA_FOLDERS = ('logins', 'recovery')
# File names that hold a login wherever they are under the home or the data folder (a run's private Claude folder
# under <data>/run/ has its own): never opened at any depth.
LOGIN_BASENAMES = ('auth.json', '.credentials.json', '.claude.json')
# Claude Code keeps a <pid>.key beside each running window's <pid>.json (2.1.329): never opened.
LOGIN_NAME_RE = re.compile(r'^[0-9]+\.[0-9a-fA-F-]+\.key\Z')
# The account list (vault.py: "no secrets"), relative to the data folder: opened only by code under
# switchboard/accounts/ and switchboard/core.py, reached from here through switchboard.accounts.api.
ENGINE_DATA_FILES = ('accounts.json',)
# Settings files that can hold a key in their env block, relative to the home folder: this package never opens them
# itself (the session hooks read Claude Code's settings for their own status, reached through bridge.py).
NO_READ_FILES = ('.claude/settings.json', '.codex/config.toml')

# Names that move a run from the person's plan to a key someone pays for by the token, to a cloud account, or hand a
# program a login. Taken out of the environment of every program a provider starts, so the work runs on the plan the
# person chose; the printed one-line form says `env -u NAME` for each one that is set.
BILLING_ENV = ('OPENAI_API_KEY', 'CODEX_API_KEY', 'CODEX_ACCESS_TOKEN', 'OPENAI_BASE_URL', 'OPENAI_ORG_ID',
               'OPENAI_PROJECT_ID', 'OPENAI_IDENTITY_TOKEN_FILE', 'OPENAI_FEDERATION_RULE_ID',
               'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL', 'CLAUDE_CODE_OAUTH_TOKEN',
               'CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR', 'CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR', 'ANTHROPIC_PROFILE',
               'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY',
               'CLAUDE_CODE_USE_MANTLE', 'CLAUDE_CODE_USE_ANTHROPIC_AWS',
               'CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD')
# The marks a Claude Code chat gives the programs it starts. A terminal opened from such a chat carries them into
# every window, and Claude Code started with them takes itself for that chat's helper: it saves no chat (so nothing
# could carry it on later), answers to that chat's message channel and runs at that chat's effort. They are kept
# from what Switchboard starts, and only when one of NESTED_MARKS is there: without one, a name a person set by hand
# stays. A person's own settings (CLAUDE_CONFIG_DIR and the like) are never in this list. Read from the environment
# of Claude Code 2.1.332's own helpers; a later version may add a name.
NESTED_MARKS = ('CLAUDECODE', 'CLAUDE_CODE_CHILD_SESSION')
SESSION_ENV = NESTED_MARKS + (
    'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_SESSION_ID', 'CLAUDE_CODE_SESSION_ATTENDED', 'CLAUDE_CODE_CHILD_NOTES_DIR',
    'CLAUDE_CODE_MESSAGING_SOCKET', 'CLAUDE_CODE_MESSAGING_TOKEN', 'CLAUDE_CODE_EXECPATH', 'CLAUDE_PID',
    'CLAUDE_EFFORT', 'CLAUDE_AGENT_SDK_VERSION', 'CLAUDE_CODE_STARTUP_FAILURE_RESULTS',
    'CLAUDE_CODE_EMIT_STARTUP_TIMING', 'CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING', 'CLAUDE_CODE_ENABLE_TASKS',
    'CLAUDE_CODE_QUESTION_PREVIEW_FORMAT')
# A read-only job (job()) gets only these names from the person's environment (and never one in BILLING_ENV): a
# review needs no secret, and a job can print its own environment back.
JOB_ENV_NAMES = ('PATH', 'HOME', 'USER', 'LOGNAME', 'SHELL', 'TMPDIR', 'LANG', 'TERM', 'CODEX_HOME',
                 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'no_proxy',
                 'all_proxy')
JOB_ENV_PREFIXES = ('LC_', 'XDG_')
# Names whose values look like secrets: their values are taken out of every note wherever they appear.
SECRET_ENV_RE = re.compile(r'(?i)(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|COOKIE|SESSION|AUTH(?!OR)|DSN|PRIVATE'
                           r'|(^|_)PAT(_|$)|(^|_)CONN|(DATABASE|DB|REDIS|MONGO\w*|POSTGRES\w*)_URL)')

# ---------- files this package keeps ----------

# Under Switchboard's data folder:
DATA_DIR = 'providers'                      # <data>/providers/, mode 0700
SETTINGS_FILE = 'providers/settings.json'   # order, added providers, consent per folder, how to open each app
LEDGER_FILE = 'providers/ledger.json'       # tasks and hops
READINGS_FILE = 'providers/readings.json'   # each provider's last Limits, for the board
PROGRAMS_FILE = 'providers/programs.json'   # each program found and its version, by path, size and time
ASK_FILE = 'providers/ask.json'             # when the app was last asked for its numbers, and the back-off
PROBE_HOME = 'providers/codex-probe'        # an empty CODEX_HOME for --version probes, mode 0700
LOCK_NAME = 'providers.lock'                # core.file_lock name for the settings and the ledger
ASK_LOCK_NAME = 'providers-ask.lock'        # one ask at a time across processes
HAND_LOCK_NAME = 'providers-hand.lock'      # one job at a time across processes

# Beside the data folder, never inside it: the notes. <dirname(data)>/.switchboard-notes/<task id>/<hop id>.md and
# .json, and a job's <hop id>.answer.txt; folders 0700, files 0600. A folder that holds nothing but notes, so an
# assistant that asks to read one never needs a rule that also opens Switchboard's logins.
NOTES_DIR_NAME = '.switchboard-notes'
NOTE_KEEP_DAYS = 30                         # a note and its sidecar are removed after this
ANSWER_KEEP_DAYS = 7                        # a job's answer is removed after this (it may hold what the job read)

# A short pointer for a Claude chat, delivered by the session hooks with the person's next message there. It holds
# fixed words, times, the hop id and the note's path: never another assistant's words. session_hooks.py reads this
# without importing this package, so these four names are repeated there with a pointer back here, and a test ties
# the two.
NOTE_DIR = 'hooks/notes'                    # <data>/hooks/notes/<session id>.json, mode 0600
NOTE_V = 1                                  # {"v": 1, "text": str, "path": str, "hop": str, "made_at": float}
NOTE_TEXT_MAX = 2000                        # characters of "text"; the hook cuts the note, never its other notice
NOTE_KEEP = 7 * 86400                       # a note nobody's next message picked up is dropped after this

# ---------- values ----------


@dataclass(frozen=True)
class Unsupported:
    """What a provider answers for something it does not do. what: the capability asked for. why: one plain
    sentence for the person ("Claude jobs are not in this version.")."""
    provider: str
    what: str
    why: str


class ProviderError(Exception):
    """A failure the person can act on. str() is one plain sentence that says what to do next."""
    KINDS = ('not_installed', 'too_old', 'not_signed_in', 'not_found', 'ambiguous', 'refused', 'timeout', 'busy',
             'unreadable', 'disk', 'other')

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind if kind in self.KINDS else 'other'


@dataclass(frozen=True)
class Ran:
    """One program run to its end (or its time limit). code: its exit code, None when it timed out or never started.
    missing: the program was not there. out and err are text, each cut to the first 1 MiB, read after the program
    ends (a run shows no progress while it runs)."""
    code: Optional[int]
    out: str = field(default='', repr=False)
    err: str = field(default='', repr=False)
    took_s: float = 0.0
    timed_out: bool = False
    missing: bool = False


class Pipe:
    """A program started with its input and output joined to this process, one JSON object per line each way (a
    vendor's app server). Made by Reach.pipe. Its standard error goes to /dev/null, so a program that writes a lot
    there can never fill a pipe nobody reads. Every method has a deadline and never blocks past it."""

    def send(self, line: str) -> None:
        """Write one line (a newline is added). Raises ProviderError('refused') when the program has gone."""
        raise NotImplementedError

    def receive(self, timeout: float) -> Optional[str]:
        """The next line the program wrote, without its newline, or None when none came within timeout seconds or
        the program ended. A line longer than 16 MiB is read past and None is returned for it."""
        raise NotImplementedError

    def close(self, grace: float = 3.0) -> Optional[int]:
        """Close its input, wait up to grace seconds, then end it (terminate, then kill). Returns its exit code."""
        raise NotImplementedError


@dataclass(frozen=True)
class Reach:
    """Everything an adapter reaches outside this process. run.default_reach() builds the real one; tests build one
    of stand-ins. home and data_dir are absolute. env is the environment the command was started with: an adapter
    reads names from it (never from os.environ) and never changes it. path_dirs: the folders of $PATH, in order.
    app_dirs: where desktop apps are looked for (/Applications, ~/Applications).

    run(argv, stdin, timeout, env, cwd) -> Ran: one program to its end. argv is a list, never a shell line.
    pipe(argv, env, cwd) -> Pipe: one program kept talking.
    execve(path, argv, env, cwd): this process becomes the program (never returns when it works; raises OSError when
    it cannot start).
    open_url(url, app=None) -> Ran: hand a link to macOS (the vendor's documented app link only), to app (the path of
    the app bundle that should answer it) when given.
    wall(): seconds since the epoch, for times shown and saved. mono(): a clock for this process's own waits.
    tty(): True when the person is at a terminal: standard input and output are both one.
    alive(pid) -> bool: is that process running (a process of another user counts as running)."""
    home: str
    data_dir: str
    env: Mapping[str, str] = field(repr=False)
    path_dirs: Tuple[str, ...]
    app_dirs: Tuple[str, ...]
    run: Callable[..., Ran]
    pipe: Callable[..., Pipe]
    execve: Callable[..., None]
    open_url: Callable[..., Ran]
    wall: Callable[[], float]
    mono: Callable[[], float]
    tty: Callable[[], bool]
    alive: Callable[[int], bool]


@dataclass(frozen=True)
class Program:
    """One copy of a provider's program found on this Mac. version: as the program says it ('0.169.1',
    '0.164.0-alpha.11.1'), None when it could not be told. source: 'path' ($PATH), 'app' (inside a desktop app),
    'plugin' (a copy an app keeps for itself), 'extension' (an editor extension's private copy) or 'versions' (a
    folder of installed versions). where: a short plain name for the person ('ChatGPT.app', 'your PATH'). refused:
    None when the copy may be chosen, else a plain reason it never is ('a copy the app keeps for itself', 'its folder
    can be changed by other users')."""
    path: str
    version: Optional[str]
    source: str
    where: str
    refused: Optional[str] = None


@dataclass(frozen=True)
class Found:
    """What detect() saw. program: the one this provider will run (the newest copy not refused), None when none is
    fit. programs: every copy found, newest first. signed_in: True, False, or None when it cannot be told without
    opening a login file (it never is). newest_seen: the newest app version that wrote this provider's own files
    (its chats), so an older program is never run on them. tested: the chosen program's version is in the range this
    adapter's facts table was checked on. problems: plain sentences, each one thing the person may fix."""
    provider: str
    installed: bool
    program: Optional[Program] = None
    programs: Tuple[Program, ...] = ()
    signed_in: Optional[bool] = None
    newest_seen: Optional[str] = None
    tested: bool = False
    problems: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Window:
    """One limit, in the provider's own words. label: named from its real length ('5-hour', 'weekly', '3-day'), or
    a model's ('Opus'). used_pct: 0..100, None when not known (a window whose reset has passed since it was read is
    None: it has refilled and nothing has been read since). resets_at: epoch seconds or None. length_min: the
    window's length in minutes when the app says it. line: the percent at which Switchboard counts it as full."""
    label: str
    used_pct: Optional[float]
    resets_at: Optional[float]
    length_min: Optional[int] = None
    line: Optional[float] = None


@dataclass(frozen=True)
class Limits:
    """One account's numbers. seat: Seat.key. read_at: when the oldest number was read. source: 'file' (the app's
    own saved files), 'ask' (asked of the app now), 'engine' (Claude's account code) or 'none'. plan: the plan's
    name as the app says it ('pro'), None when not known. reached: the app's own word when it says a limit is
    reached ('rate_limit_reached'), else None. error: a plain reason when the numbers are not current. ask: how an
    ask ended (one of ASK_OUTCOMES), None when none was made."""
    provider: str
    seat: str
    windows: Tuple[Window, ...]
    read_at: Optional[float]
    source: str
    plan: Optional[str] = None
    reached: Optional[str] = None
    error: Optional[str] = None
    ask: Optional[str] = None


@dataclass(frozen=True)
class Seat:
    """One account of a provider. key: the provider's own id for it (Claude: the Anthropic account id; Codex: its own
    id, since only its one login is used). label: what the person sees (an email or short name for Claude; 'ChatGPT
    Pro' for Codex). in_use: the account the app is on now."""
    provider: str
    key: str
    label: str
    plan: Optional[str] = None
    in_use: bool = False


@dataclass(frozen=True)
class Room:
    """How much room a provider has, now. state: one of ROOM_STATES. back_at: when the first of its accounts refills,
    for OUT, when known. read_at: how old the numbers behind it are. why: left empty by place.room_of; the command
    fills it from words.py, which also says how old the numbers are ("had room when last read 2 days ago")."""
    provider: str
    state: str
    back_at: Optional[float] = None
    why: str = ''
    read_at: Optional[float] = None


@dataclass(frozen=True)
class ChatRef:
    """One chat of a provider that ran in a folder. id: the app's own id for it (CHAT_ID_RE). folder: absolute, the
    folder the chat was started in, as its own first lines say (their cwd), never only from where its file is kept:
    the app files the chat under this folder and opens it again from there. name: the name the person or the app gave
    it, None when it has none. path: the chat's file on this Mac (never printed by a command except with --show).
    live: a window of the app has it open now. working: it was written in the last minute. version: the app version
    that last wrote it. entrypoint: how the app was started for it, as the app says ('cli', 'claude-vscode',
    'sdk-cli'); a headless run is listed only when named. stopped: the app's own word when its newest reply was an
    error ('rate_limit'), else None: such a chat is listed first and marked. work_folder: the folder its newest lines
    ran in, when that is another folder than the one it was started in (the assistant's shell moved there, which is
    the usual case in a long chat) and it is still a folder; None when it is the same. Where things stand is read
    there, and another assistant is started there; the chat itself is opened again from folder."""
    provider: str
    id: str
    folder: str
    path: str
    name: Optional[str] = None
    started_at: Optional[float] = None
    last_at: Optional[float] = None
    live: bool = False
    working: bool = False
    version: Optional[str] = None
    entrypoint: Optional[str] = None
    stopped: Optional[str] = None
    work_folder: Optional[str] = None


@dataclass(frozen=True)
class ReadLimits:
    """How much of a chat's file a reader may read, from its end: at most max_bytes and max_seconds; a line longer
    than line_max is skipped unread (and counted). since_boundary: stop at the newest summary boundary, since the
    summary stands for everything before it; but first keep reading back past it, up to keep_person_before_boundary
    of the person's own messages and before_boundary_bytes more, so a request made just before the summary is kept
    (each such event has before_boundary set)."""
    max_bytes: int = 64 << 20
    max_seconds: float = 5.0
    line_max: int = 4 << 20
    since_boundary: bool = True
    keep_person_before_boundary: int = 5
    before_boundary_bytes: int = 1 << 20


@dataclass(frozen=True)
class Event:
    """One thing that happened in a chat, oldest first in a Transcript. kind: one of EVENT_KINDS. text: the words, cut
    to KEEP_TOOL_INPUT for a tool call, KEEP_TOOL_RESULT for a result and KEEP_PERSON for the person's (full_len
    says how long it was). at: epoch seconds when the line says. name: a tool's name for TOOL_CALL and TOOL_RESULT,
    else ''. ok: for TOOL_RESULT, False when the tool reported an error, True when it reported success, None when the
    app gives no flag (Codex 0.169.1 gives none). files: paths the event names (a tool's file argument, or the files a
    shell command names). ref: the address of web content a tool fetched (its text is never kept). queued: the person
    typed it while the assistant worked. before_boundary: it came before the newest summary (ReadLimits). origin: for
    NOTICE, the app's own short word for who wrote it ('task-notification', 'switchboard-note'), else None. call: for
    TOOL_CALL and TOOL_RESULT, the app's own id that ties a result to its call, when the app gives one (a short
    plain word, never text); None when it gives none, and a result is then paired by its tool's name."""
    kind: str
    text: str = field(default='', repr=False)
    at: Optional[float] = None
    name: str = ''
    ok: Optional[bool] = None
    files: Tuple[str, ...] = ()
    ref: Optional[str] = None
    full_len: int = 0
    queued: bool = False
    before_boundary: bool = False
    origin: Optional[str] = None
    call: Optional[str] = None


@dataclass(frozen=True)
class ReadInfo:
    """What a reader saw while it read. version: the app version the file's newest lines name. bytes_read and lines:
    how much it read. from_end: True when it stopped before the file's start (a cap or a boundary). stopped: why it
    stopped ('start', 'boundary', 'bytes', 'time'). unknown_lines: lines of a kind or shape its facts table does not
    know (a new block type inside a known line counts). torn_tail: a last line cut mid-write (never counted unknown).
    skipped_large: lines over line_max. images: pictures left out. reasoning: private reasoning blocks left out.
    title: the chat's latest name. degraded: None, or one plain sentence on why the transcript holds less than usual
    ("This chat's file has a shape this Switchboard does not know (Codex 0.180.0)."). compressed: the file was a
    compressed one. census: (kind, seen, taken, skipped by the table, unknown) per line kind and block kind, so drift
    is visible later. new_kinds: kinds seen often and absent from the table, by name. reader: the facts table's own
    version string, kept in the note's sidecar so an old note is not reused by a newer reader."""
    version: Optional[str] = None
    bytes_read: int = 0
    lines: int = 0
    from_end: bool = False
    stopped: str = 'start'
    unknown_lines: int = 0
    torn_tail: int = 0
    skipped_large: int = 0
    images: int = 0
    reasoning: int = 0
    title: Optional[str] = None
    degraded: Optional[str] = None
    compressed: bool = False
    census: Tuple[Tuple[str, int, int, int, int], ...] = ()
    new_kinds: Tuple[str, ...] = ()
    reader: str = ''


@dataclass(frozen=True)
class Transcript:
    """One chat as neutral events, oldest first, with what the reader saw."""
    chat: ChatRef
    events: Tuple[Event, ...]
    info: ReadInfo


@dataclass(frozen=True)
class Handoff:
    """A note written for the next assistant (handoff.py). path: the Markdown file. meta_path: its .json beside it.
    bytes and sha256: of the Markdown file as written. removed: (kind, count) pairs of what looked like a secret and
    was taken out. cut: how many pieces were shortened. nonce: the random word that marks the note's own headings.
    degraded: copied from the transcript. reused: an unchanged chat's earlier note was used again."""
    hop: str
    path: str
    meta_path: str
    bytes: int
    sha256: str
    removed: Tuple[Tuple[str, int], ...] = ()
    cut: int = 0
    nonce: str = ''
    degraded: Optional[str] = None
    reused: bool = False


@dataclass(frozen=True)
class Launch:
    """How to start a provider's app. how: one of HOW. program: the copy that runs. argv: the full argument list,
    argv[0] the program's path. env: the whole environment it gets. cwd: the folder it starts in. line: the one line
    the person could type for the same thing, quoted for a shell (with `env -u NAME` for each BILLING_ENV name the
    person's environment has), shown when how is PRINT; '' when it could not be printed safely. url: for IN_APP, the
    app's own link; app: the app bundle that should answer it (the one holding the chosen program), or None.
    first_words: the fixed first message (fixed words and the note's path, never chat text)."""
    provider: str
    how: str
    program: Optional[Program]
    argv: Tuple[str, ...]
    env: Mapping[str, str] = field(default_factory=dict, repr=False)
    cwd: str = ''
    line: str = ''
    url: Optional[str] = None
    app: Optional[str] = None
    first_words: str = ''


@dataclass(frozen=True)
class Answer:
    """What a job gave back. ok: it ran and wrote an answer. text: the answer, cut to 200,000 characters. path: the
    file it is kept in (the out_path the job was given, 0600). took_s: how long it ran. code: the program's exit code.
    why: a plain reason when not ok ("Codex did not answer within 15 minutes.")."""
    provider: str
    ok: bool
    text: str = field(default='', repr=False)
    path: Optional[str] = None
    took_s: float = 0.0
    code: Optional[int] = None
    why: str = ''


@dataclass(frozen=True)
class Hop:
    """One move of a task between providers, as the ledger keeps it (tasks.py). kind: 'continue', 'back' or 'hand'.
    state: 'made' (the note is written), 'started' (the app is being started in a terminal), 'in_app' (the app's link
    was opened), 'printed' (the line to run was printed), 'answered' (a job answered), 'failed' (a job, or a start that
    could not run), 'returned' (a later back closed it). to_chat: the chat the move led to, once found. No chat text
    is ever kept here."""
    id: str
    task: str
    kind: str
    at: float
    folder: str
    from_provider: str
    from_chat: Optional[str]
    to_provider: str
    to_chat: Optional[str] = None
    handoff: Optional[str] = None
    sha256: Optional[str] = None
    bytes: int = 0
    removed: int = 0
    state: str = 'made'
    program_version: Optional[str] = None


# ---------- the adapter ----------


class Provider:
    """One provider's adapter. A subclass sets id, name, vendor and can, and overrides the methods it can do; every
    other method answers Unsupported. Each method keeps to the rules in this module's docstring, and every program
    call it makes has a time limit. An adapter is made with a Reach and keeps no other state between calls."""
    id = ''                                 # matches ID_RE: the word the person types
    name = ''                               # what the person reads: 'Claude', 'Codex'
    vendor = ''                             # who makes it: 'Anthropic', 'OpenAI'
    can: FrozenSet[str] = frozenset()       # a subset of CAPABILITIES
    # What the shared modules ask an adapter instead of testing its id. Each default names no provider.
    app_name: Optional[str] = None          # its program's name in a sentence ('Claude Code'); None: its name
    sign_in = ''                            # how a person signs in once in its own app; '' for the plain words
    billing_prefixes: Tuple[str, ...] = ()  # the starts of the BILLING_ENV names its own app would use
    cannot: Mapping[str, str] = {}          # its own sentence for something it cannot do, by capability
    job_question = ''                       # asked once on this Mac before its first job; '' asks nothing
    own_rules = ''                          # where its own memory and global rules live, for the note
    account_list = False                    # its seats are the accounts Switchboard's own account list keeps
    session_env = ''                        # the name its app sets in its own shell to the chat that shell runs in
    rule_files: Tuple[str, ...] = ()        # a project's rule files its app reads, relative to a project folder
    rule_dirs: Tuple[str, ...] = ()         # folders of more rule files (*.md) its app reads, relative to one

    def __init__(self, reach: Reach):
        self.reach = reach

    def _unsupported(self, what: str, why: str = '') -> Unsupported:
        return Unsupported(self.id, what, why or '%s cannot do that in this version of Switchboard.' % self.name)

    def detect(self) -> Union[Found, Unsupported]:
        """Is the app here, which copies of its program, which one is used. Runs a program's --version at most once
        per copy whose size or time changed (the answer is kept in PROGRAMS_FILE)."""
        return self._unsupported(DETECT)

    def seats(self) -> Union[Tuple[Seat, ...], Unsupported]:
        """Its accounts, in the person's order. Never reads a login."""
        return self._unsupported(SEATS)

    def limits(self, ask: bool = False) -> Union[Tuple[Limits, ...], Unsupported]:
        """Each seat's numbers. Saved numbers first: no process and no network. ask=True asks the app now, once,
        paced and single flight, only after the person's one yes to it; whatever happens, Limits.ask says how the ask
        ended and the saved numbers come back when it did not run. Every Window carries its line, so how much room a
        provider has is worked out in one place for all of them (place.room_of), never by an adapter."""
        return self._unsupported(ASK if ask else LIMITS)

    def chats(self, folder: str) -> Union[Tuple[ChatRef, ...], Unsupported]:
        """Its chats whose own lines say they ran in folder (an absolute, resolved path), most recent first: live
        ones, then ones written in the last CHATS_DAYS days. Reads each chat's first or last lines only, never the
        whole file."""
        return self._unsupported(CHATS)

    def read(self, chat: ChatRef, limits: ReadLimits = ReadLimits()) -> Union[Transcript, Unsupported]:
        """One chat as a Transcript, read from the file's end within limits, with the allow-list of its facts table."""
        return self._unsupported(READ)

    def start(self, folder: str, handoff: Handoff, how: str) -> Union[Launch, Unsupported]:
        """How to start its app in folder on the note: the terminal app (IN_TERMINAL), the desktop app's link
        (IN_APP) or the line to print (PRINT). Raises ProviderError('too_old') rather than run a program older than
        Found.newest_seen on the person's own files."""
        return self._unsupported(START_APP if how == IN_APP else START)

    def resume(self, chat: ChatRef, handoff: Handoff, how: str) -> Union[Launch, Unsupported]:
        """How to open one of its own chats again, in the chat's folder, with a first message that points at the
        note. Raises ProviderError('busy') when the chat is open in a window (two writers can lose messages)."""
        return self._unsupported(RESUME)

    def job(self, folder: str, prompt: str, out_path: str, timeout: float = 900.0) -> Union[Answer, Unsupported]:
        """One read-only job in folder: the prompt goes in on standard input (never in the argument list), the app
        runs in its read-only mode with no approvals asked and an environment of JOB_ENV_NAMES only, and its last
        message is written to out_path (made by the command, empty and 0600, under the notes folder) and comes back
        as the answer. Nothing is written anywhere else."""
        return self._unsupported(JOB)

    def note(self, chat: ChatRef, timeout: float = 300.0) -> Union[str, Unsupported]:
        """The app's own short note of one of its chats. A seam for later: no adapter does it in the first build."""
        return self._unsupported(NOTE)
