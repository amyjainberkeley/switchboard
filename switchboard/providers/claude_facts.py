"""Claude Code's program facts, in one table: where its program is kept, how it says its version, which flags a start
uses, and where a running window names its chat. Each fact says the version it was checked on.

Read from Claude Code's own --help and --version (2.1.309) and from the folders it keeps (2.1.309 to 2.1.330), never
by running a chat. The adapter (claude.py) reads this table through
its `facts` attribute, so a test can point it at stand-in names: a stand-in is never called claude.
"""
import re

FACTS = {
    # A program called this on $PATH is a candidate (2.1.309: the native installer links ~/.local/bin/claude to a
    # file in the versions folder below; an npm install puts a script there instead).
    'program_names': ('claude',),
    # Under the home folder: one file per installed version, named by the version itself (2.1.309 to 2.1.330).
    'versions_dir': '.local/share/claude/versions',
    # Under the home folder: the editor extension's folders, each anthropic.claude-code-<version>-<platform>, holding
    # its own private program at extension_program (2.1.322 to 2.1.330).
    'extension_dirs': ('.vscode/extensions',),
    'extension_re': r'^anthropic\.claude-code-(\d+\.\d+\.\d+)(?:-[A-Za-z0-9._-]+)?\Z',
    'extension_program': 'resources/native-binary/claude',
    # The desktop app keeps a Linux program for its own virtual machine here: never a candidate, never looked in.
    'not_candidates': ('Library/Application Support/Claude/claude-code-vm',),
    # The versions this table was checked on (Found.tested).
    'checked': ('2.1.309', '2.1.330'),
}

# `claude --version` prints one line, '2.1.309 (Claude Code)' (2.1.309 and 2.1.330).
VERSION_RE = re.compile(r'^(\d+\.\d+\.\d+) \(Claude Code\)')
# A version as a file or folder name says it (the versions folder).
VERSION_NAME_RE = re.compile(r'^\d+\.\d+\.\d+\Z')
VERSION_PROBE_S = 10.0          # seconds a --version probe may take

# A start: `claude "<first message>"` opens an interactive session with that first message; `claude --resume <id>
# "<words>"` opens that chat again with them (2.1.309 help: "-r, --resume [value]", "prompt  Your prompt").
RESUME_FLAG = '--resume'
# On a new task (switchboard do), a start may also carry these options, each with its value and nothing else
# (2.1.309 help: "--model <model>  Model for the current session", "--effort <level>  Effort level for the current
# session").
TASK_FLAGS = ('--model', '--effort')
TASK_VALUE_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._\[\]-]{0,63}\Z')
# Flags a start never carries: they loosen the person's own permission checks, or run without a person (-p, whose
# runs use an API key from the settings file whenever one is there).
NEVER_FLAGS = ('--dangerously-skip-permissions', '--allow-dangerously-skip-permissions', '--permission-mode',
               '--permission-prompt-tool', '-p', '--print', '--bg', '--background')

# A running window keeps <Claude's folder>/sessions/<pid>.json (2.1.309 to 2.1.329) with the chat it is on
# (sessionId) and removes it when it ends; a crash leaves one whose process is gone. A <pid>.<hex>.key beside it holds
# a secret and is never opened (contract.LOGIN_NAME_RE).
SESSIONS_DIR = 'sessions'
WINDOW_RE = re.compile(r'^[1-9][0-9]{0,9}\.json\Z')
WINDOW_MAX = 256 << 10          # Claude Code reads at most this much of one (2.1.309)
WRITING_S = 60.0                # a chat written this recently is still being written by a window

# The account code's own windows and how long each is (accounts/api.py Window.name), and its lines' names in
# api.auto_settings(). The lines' defaults are the account code's (accounts/policy.py Settings), used only when
# auto_settings() does not answer.
WINDOWS = {'5-hour': (300, 'session_at'), 'weekly': (10080, 'weekly_at')}
MODEL_PREFIX = 'model:'
MODEL_LINE = 'model_at'
DEFAULT_LINES = {'session_at': 90.0, 'weekly_at': 95.0, 'model_at': 95.0}
# Account statuses that can carry work (accounts/policy.py OK_STATUSES): any other counts as no room.
OK_STATUSES = ('ok', 'expiring')

# Where each kind of copy is, in words for the person (Program.where).
WHERE = {'path': 'your PATH', 'versions': "Claude Code's own install", 'extension': "the editor extension's copy"}
