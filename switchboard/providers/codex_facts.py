"""Codex's program interface in one table: names, places, flags, the four questions the ask sends, and the lines.

Every fact names the Codex version it was checked on. "Checked" means read from that version's own --help output or
its generated JSON schema (`codex app-server generate-json-schema`), never by running a model turn. The tests point
FACTS at stand-in names (fake-codex) and shorter time limits; nothing else here changes in a test.
"""
import re

# The versions whose help and schema this table was checked against. A chosen program outside them still runs; the
# board says it was not checked (Found.tested).
CHECKED_ON = ('0.155.1', '0.169.1')

# `codex --version` prints `codex-cli 0.169.1` (0.155.1, 0.164.0-alpha.11.1, 0.167.0-alpha.4.1 and 0.169.1 all do).
VERSION_RE = re.compile(r'codex-cli (\S+)')

FACTS = {
    # The program's own name on $PATH (the npm package installs `codex`; 0.155.1).
    'program_names': ('codex',),
    # Both desktop apps that carry Codex (ChatGPT.app with 0.169.1, the older Codex.app with 0.164.0-alpha.11.1) have
    # this bundle id and keep the program here, a small shell wrapper that runs the app's own copy.
    'bundle_id': 'com.openai.codex',
    'app_program': 'Contents/Resources/codex-cli/bin/codex',
    # A copy the desktop app downloads for itself under the Codex folder (0.167.0-alpha.4.1 seen): listed, never run.
    'plugin_program': 'plugins/.plugin-appserver/codex-cli/bin/codex',
    # Groups that may write a folder above the program: macOS's own /Applications is drwxrwxr-x root admin.
    'trusted_groups': ('admin', 'wheel'),
    'probe_seconds': 10.0,          # one `--version`
    'ask_seconds': 20.0,            # the whole ask, from start to the last answer
    'ask_grace': 3.0,               # then its input is closed and it has this long to end
}

# The person's Codex folder: $CODEX_HOME when set, else ~/.codex (0.155.1 and 0.169.1 help: "`~/.codex/config.toml`";
# `--ignore-user-config`: "auth still uses `CODEX_HOME`").
CODEX_HOME_ENV = 'CODEX_HOME'
LOGIN_FILE = 'auth.json'            # its existence is checked with os.stat; it is never opened

# Where a copy is kept, said to the person.
WHERE_PATH = 'your PATH'
WHERE_PLUGIN = "Codex's plugin folder"
REFUSED_PLUGIN = 'a copy the app keeps for itself'
REFUSED_TRUST = 'its folder can be changed by other users'
REFUSED_NO_VERSION = 'its version could not be read'

# The line at which a window counts as full, by its label (the same lines Claude uses by default [inferred]).
LINES = {'5-hour': 90.0, 'weekly': 95.0}
LINE_OTHER = 95.0


def label_of(minutes):
    """A window's name from its length in minutes: 300 is '5-hour', 10080 is 'weekly', else '<n>-day' or '<n>-hour'
    (or '<n>-minute' for a length that is neither). None when the length is not known."""
    if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes <= 0:
        return None
    if minutes == 300:
        return '5-hour'
    if minutes == 10080:
        return 'weekly'
    if minutes % 1440 == 0:
        return '%d-day' % (minutes // 1440)
    if minutes % 60 == 0:
        return '%d-hour' % (minutes // 60)
    return '%d-minute' % minutes


def line_of(label):
    return LINES.get(label, LINE_OTHER)


# ---------- the ask (`codex app-server`, stdio, one JSON object per line, no "jsonrpc" field) ----------

# The only methods the ask may send (0.155.1 and 0.169.1 stable schema). The sender refuses any other name.
ASK_METHODS = ('initialize', 'initialized', 'account/read', 'account/rateLimits/read')
ASK_ARGS = ('app-server',)          # stdio is the default transport; never --listen, proxy, daemon or analytics
OPT_OUT = ('item/agentMessage/delta', 'item/reasoning/summaryTextDelta', 'item/reasoning/summaryPartAdded',
           'item/reasoning/textDelta')
# Every request's params, as sent. account/read never asks for a token refresh; the limits read skips the reset
# credits' detail (and never sends supportsLunaReserve).
ASK_PARAMS = {'account/read': {'refreshToken': False},
              'account/rateLimits/read': {'excludeResetCreditDetails': True}}
# Keys taken out of every answer while it is parsed, so they are never held: an email, ids of the person's account.
# The one exception is account/read's email: it names Codex's row in the list, as a Claude account's email names its
# row, and it is kept in READINGS_FILE beside the numbers. Never a token, never an id of the account.
DROP_KEYS = frozenset(('email', 'userId', 'accountId', 'chatgptAccountId', 'workspaceName', 'previousAccountId'))
EMAIL_RE = re.compile(r'^[^@\s"]+@[^@\s"]+\.[A-Za-z]{2,}\Z')
EMAIL_MAX = 254
SERVER_REQUEST_ANSWER = {'code': -32601, 'message': 'Unsupported server request'}
OVERLOADED = -32001                 # "Server overloaded; retry later." (app-server docs, read with 0.155.1)
BUCKET = 'codex'                    # rateLimitsByLimitId's key for Codex's own limits (0.169.1)
ASK_PACE_S = 300.0                  # no ask within this long of the last one
ASK_BACKOFF_FIRST_S = 600.0         # after a refusal or a time-out; doubled each time
ASK_BACKOFF_MAX_S = 3600.0
KEPT_DAYS = 7                       # an asked reading kept in READINGS_FILE is shown for at most this long

# ---------- starting it ----------

# The interactive terminal app in a folder on a first message (0.155.1 and 0.169.1 top-level help: `-C, --cd <DIR>`,
# `[PROMPT]`). Never -s, -a, --dangerously-*, --no-daemon: the person's own sandbox and approvals stay as they are.
START_ARGS = ('-C',)
# One of its own chats again (0.155.1 and 0.169.1 `resume --help`: `-C`, `[SESSION_ID] [PROMPT]`).
RESUME_ARGS = ('resume', '-C')
# On a new task (switchboard do), a start may also carry these, each as a pair and nothing else: the model option
# (0.155.1 and 0.169.1 top-level help: `-m, --model <MODEL>`) and one configuration value, the reasoning effort (0.169.1
# help: `-c, --config <key=value>`; the key is model_reasoning_effort in OpenAI's configuration reference).
TASK_MODEL_FLAGS = ('-m', '--model')
TASK_CONFIG_FLAG = '-c'
TASK_CONFIG_RE = re.compile(r'^model_reasoning_effort="?[a-z][a-z0-9]{0,15}"?\Z')
TASK_VALUE_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._\[\]-]{0,63}\Z')
# The desktop app's link: it fills the message box and does not send [notes 05 s4.3, Codex docs read with 0.169.1].
APP_LINK = 'codex://new?prompt={prompt}&path={path}'
# One read-only job, its prompt on standard input (0.155.1 and 0.169.1 `exec --help`: "if `-` is used, instructions
# are read from stdin"; `-s read-only`; `-o, --output-last-message <FILE>`; `--ephemeral` "Run without persisting
# session files to disk").
JOB_ARGS = ('exec', '-C', '{folder}', '-s', 'read-only', '-c', 'approval_policy="never"', '--skip-git-repo-check',
            '--ephemeral', '--color', 'never', '-o', '{out}', '-')
ANSWER_MAX = 200000                 # characters of a job's answer kept (contract.Answer)

# Words no start or resume ever passes (each loosens the app's sandbox or approvals, or joins a shared server), and
# no word that begins NEVER_PREFIX (0.169.1 has two such flags). A job passes `-s read-only` and nothing else of these.
NEVER_FLAGS = ('-s', '--sandbox', '-a', '--ask-for-approval', '--no-daemon', '--approve-for-me', '--add-dir',
               '--trust')
NEVER_PREFIX = '--dangerously-'

# The name of the provider a note came from, by its id (the note's sidecar keeps the id), for the first message.
PROVIDER_NAMES = {'claude': 'Claude', 'codex': 'Codex'}
SOMEONE = 'another assistant'

# ---------- the adapter's own sentences (one each, for the person) ----------

NOT_INSTALLED = 'Codex is not on this Mac. Install OpenAI\'s Codex (its app or its command line), then try again.'
NO_FIT_PROGRAM = 'No copy of Codex here can be used: {why}.'
BAD_CHAT_ID = "That chat's id is not one Codex writes, so it was not opened."
CHAT_OPEN = 'That Codex chat was written in the last minute; it may still be open. Close it, then try again.'
NO_APP_RESUME = 'Codex has no link that opens one of its chats again; run it in a terminal.'
NO_APP_TASK = 'Codex has no link that starts it on a new task with a model; run it in a terminal.'
ASK_API_KEY = 'Codex is on an API key here, not a plan.'
ASK_SIGNED_OUT = 'Codex is not signed in here.'
ASK_WRONG_HOME = 'Codex answered from another Codex folder than the one expected.'
ASK_TIMED_OUT = 'Codex did not answer in time.'
ASK_OVERLOADED = 'Codex said it was busy.'
ASK_CLOSED = 'Codex stopped before it answered.'
ASK_NO_BUCKET = "Codex's answer held no numbers for its own limits."
JOB_TIMED_OUT = "Codex did not answer within {span}. It was started in Codex's read-only mode."
JOB_NO_ANSWER = 'Codex stopped without an answer (exit {code}).'
JOB_LAST_WORDS = ' Its last words: {words}'
JOB_MISSING = 'Codex could not be started.'
