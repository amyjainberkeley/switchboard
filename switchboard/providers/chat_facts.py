"""Both chat formats in one table: which lines and fields the readers take, each with the app version it was checked
on. claude_chats.py and codex_chats.py read nothing that is not named here.

Where a fact comes from:
- Claude Code 2.1.309 to 2.1.330: the chat files' shapes (line kinds, keys, value types, counts; never their words)
  over twenty chats written by the VS Code extension (entrypoint claude-vscode) and four headless runs (sdk-cli). No
  chat written by the terminal program was there to look at, so its lines are [not seen]: they are read by the legacy
  rule below.
- Codex 0.153.4, 0.169.0-alpha.11.1 and 0.169.1: the session files' shapes, the same way, over threads the desktop
  app and the editor extension started (source 'vscode') and their helpers. No file written by the terminal program
  (`codex`, `codex exec`) was there: those shapes are [not seen].
[inferred] marks a rule reasoned from those shapes, not seen in a file.
"""
import re
from typing import Optional

from . import run, words

# A reader's version, kept in a note's sidecar so an old note is never reused by a newer reader.
CLAUDE_READER = 'claude-chats/1'
CODEX_READER = 'codex-chats/1'

# The versions each table was checked on: a chat written outside this range is read, and the note says so.
CLAUDE_CHECKED = ('2.1.309', '2.1.330')
CODEX_CHECKED = ('0.153.4', '0.169.1')

# ---------- both ----------

# Thresholds of section "When the file is not what the table knows": a reader degrades past them.
UNKNOWN_SHARE = 0.2         # U: more than one in five of the main lines read are of an unknown kind or shape
NEW_KIND_MIN = 20           # I3: a kind absent from the table seen this often is named in ReadInfo.new_kinds
SUMMARY_WITHIN = 16         # Claude: the summary line is the first isCompactSummary line within this many after its
                            # compact_boundary (4 lines after in 79 of 83, 5 in 3, 6 in 1; 2.1.309-2.1.330)
LISTING_TAIL = 1 << 20      # a chat's last bytes read to list it (its folder, name, version, whether it stopped)
WORKING_S = 60              # a chat written this recently is being worked on now
HEAD_MAX = 1 << 20          # a first line longer than this is not read
# A Claude chat's folder is the cwd of the first of its lines that names one (33 of 33 files of 2.1.309-2.1.330: it is
# the folder its project is kept under); its newest cwd is often another (19 of 33: the assistant's shell moved).
START_LINES = 40
START_BYTES = 8 << 20

# Text that begins with one of these was written by the app or a tool, never typed by the person (Claude Code
# 2.1.309-2.1.330: 2,890 task notices, 244 other sessions' messages, 140 helpers' messages, 18 local commands).
MACHINE_TAGS = ('<task-notification', '<system-reminder', '<cross-session-message', '<agent-message',
                '<command-name', '<command-message', '<local-command-', '<bash-', '<user-prompt-submit-hook')

# Who wrote a message in the person's place, as the short word a NOTICE carries. A value from a file outside this
# list is 'other', so a NOTICE's origin never carries words of the chat.
ORIGIN_WORDS = ('task-notification', 'task_notification', 'peer', 'scheduled', 'system', 'sdk', 'human', 'unknown',
                'switchboard-note', 'app-message', 'heartbeat', 'compact-summary',
                'system-reminder', 'cross-session-message', 'agent-message', 'command-name', 'command-message',
                'local-command', 'bash', 'user-prompt-submit-hook')


def origin_word(value) -> Optional[str]:
    """A file's origin value as one of ORIGIN_WORDS ('other' for any other str), None for none."""
    if not isinstance(value, str) or not value:
        return None
    return value if value in ORIGIN_WORDS else 'other'


def tag_of(text: str) -> Optional[str]:
    """The machine tag a text begins with, as an origin word ('task-notification', 'local-command', 'bash'), or
    None when it begins plain."""
    t = text.lstrip()
    for tag in MACHINE_TAGS:
        if t.startswith(tag):
            return tag[1:].rstrip('-')
    return None


def begins_with_tag(text: str) -> bool:
    """Any '<word' or '<word-word' start: a block the app wrapped, never plain typing [inferred: every app-written
    message on both apps begins so]."""
    t = text.lstrip()
    return len(t) > 1 and t[0] == '<' and (t[1].isalpha() or t[1] == '/')


def note_hop(text: str) -> Optional[str]:
    """The hop id when text is Switchboard's own first message (words.NOTE_MARK_RE), else None."""
    m = words.NOTE_MARK_RE.match(text.lstrip())
    return m.group(2) if m else None


def version_side(version: Optional[str], checked) -> Optional[str]:
    """'newer' or 'older' when version is outside checked (min, max), else None (a version that cannot be read says
    nothing)."""
    key = run.version_key(version)
    if not key:
        return None
    if key > run.version_key(checked[1]):
        return 'newer'
    if key < run.version_key(checked[0]):
        return 'older'
    return None


def version_word(app: str, version: Optional[str], checked) -> Optional[str]:
    """The note's sentence for a chat written outside the versions checked (words.NEWER_VERSION or OLDER_VERSION),
    else None. A reader also puts a census row 'version/newer' or 'version/older' in ReadInfo.census."""
    side = version_side(version, checked)
    if side is None:
        return None
    return (words.NEWER_VERSION if side == 'newer' else words.OLDER_VERSION).format(app=app, version=version)


# Web tools: their result's text is never kept, only the address (ref), so a page's words never reach the next
# assistant as if the person had said them. Claude Code's own (2.1.309-2.1.330); every tool of the browser connectors
# below, whatever it does (page text, a script's answer, a screenshot); and any connector tool whose last part (after
# its last '__' or '.') holds one of WEB_TOOL_WORDS, in any case [inferred from the names: what a tool returns is not
# in the chat file's shape].
WEB_TOOLS = ('WebFetch', 'WebSearch')
WEB_TOOL_PREFIXES = ('mcp__claude-in-chrome__', 'mcp__Claude_Browser__', 'mcp__playwright__', 'mcp__puppeteer__')
WEB_TOOL_WORDS = ('fetch', 'search', 'browse', 'navigate', 'page_text', 'read_page', 'screenshot')
WEB_REF_KEYS = ('url', 'query')


def is_web_tool(name) -> bool:
    if not isinstance(name, str) or not name:
        return False
    if name in WEB_TOOLS or name.startswith(WEB_TOOL_PREFIXES):
        return True
    cut = max(name.rfind('__') + 2 if '__' in name else 0, name.rfind('.') + 1)
    last = name[cut:].lower() if cut > 0 else ''
    return any(w in last for w in WEB_TOOL_WORDS)


def web_ref(name: str, inp) -> str:
    """What a web tool's result is shown by: its input's url or query, else the tool's own name."""
    if isinstance(inp, dict):
        for k in WEB_REF_KEYS:
            if isinstance(inp.get(k), str) and inp[k]:
                return inp[k]
    return name


# The app's own id that ties a tool's result to its call (Claude Code's tool_use id, Codex's call_id or item id): kept
# only when it is a short plain word, so an Event never carries text of the chat in it.
_CALL_ID_RE = re.compile(r'^[A-Za-z0-9_.:-]{1,80}\Z')


def call_id(value) -> Optional[str]:
    return value if isinstance(value, str) and _CALL_ID_RE.match(value) else None


# ---------- Claude Code (2.1.309 to 2.1.330) ----------

# The value of origin.kind on a line the person typed (139 of 139 such lines; also on the 24 messages typed while the
# assistant worked, in queued_command attachments). origin.kind 'task-notification' 500, 'peer' 220 are not.
HUMAN_ORIGINS = ('human',)
# Legacy mode (a file with no origin dict at all: sdk-cli files, and perhaps the terminal program [not seen]): a line
# is the person's only when none of these is true ...
LEGACY_NOT_PERSON_FLAGS = ('isMeta', 'isCompactSummary', 'isSidechain', 'turnCompanion', 'queueTranscriptOnly',
                           'isVisibleInTranscriptOnly')
# ... turnOrigin is absent or one of these, and promptSource is not 'system'.
LEGACY_PERSON_TURN_ORIGINS = ('human', 'sdk')
# ... and its text does not begin with one of these, which the app writes itself with no mark on older versions
# (a NOTICE 'app-message') [not seen on 2.1.309-2.1.330, which mark it; the shape is the older versions'].
LEGACY_APP_TEXTS = ('[Request interrupted by user',)
# The legacy rule also judges a line older than the first line of its file that carries an origin dict: a chat begun
# on a version that wrote none and carried on in one that does [not seen: inferred from the two shapes].

# Blocks the app writes inside a line marked human (2.1.309-2.1.330: in 30 of 139 such lines the first text block was
# the app's, always before the person's own block). A text block that is one whole <TAG ...>...</TAG> element and is
# not the line's last text block is the app's: these tags are left out, these are a NOTICE beside the person's words
# (they often point at it: "fix this"), and any other tag is a NOTICE too, counted so a new one is seen. The last text
# block is always the person's, and a block of PERSON_BLOCKS is always theirs (a paste the app wrapped).
APP_BLOCKS_SKIPPED = ('browser_instruction', 'system-reminder')
APP_BLOCKS_CONTEXT = ('ide_opened_file', 'ide_selection', 'ide_diagnostics')
PERSON_BLOCKS = ('pasted_content',)
_ELEMENT_RE = re.compile(r'^<([A-Za-z_][A-Za-z0-9_-]{0,63})(?:\s[^<>]*)?>[\s\S]*</\1\s*>\Z')
# An assistant line the app wrote itself (21 lines: 20 API errors, 1 filler with no error flag).
SYNTHETIC_MODEL = '<synthetic>'


def app_block(text: str) -> Optional[str]:
    """The tag when text, stripped, is one whole element <TAG ...>...</TAG>, else None."""
    m = _ELEMENT_RE.match(text.strip())
    return m.group(1) if m else None


# Window files: <claude dir>/sessions/<pid>.json (2.1.329). Only these keys are taken; peerToken never is.
WINDOW_RE_TEXT = r'^[1-9][0-9]{0,9}\.json\Z'
WINDOW_MAX = 256 << 10
WINDOW_KEYS = ('sessionId', 'cwd', 'status', 'updatedAt', 'name', 'version', 'entrypoint')
WINDOW_BUSY = ('busy',)     # [inferred: the status word of a window that is answering; 'idle' otherwise]

# Line kinds the table takes.
CLAUDE_TAKEN = ('user', 'assistant', 'system', 'attachment', 'last-prompt', 'custom-title', 'ai-title')
# Line kinds the table leaves out on purpose (counted as skipped, never unknown). queue-operation lines never hold the
# person's words: their content is the app's own (task notices, other sessions' messages, scheduled prompts) and the
# person's own prompts are queued with no content (2.1.309-2.1.330: 3,280 enqueues with content, all machine-written
# or scheduled; 163 without, which are the 139 human lines and the 24 human queued_commands).
CLAUDE_SKIPPED = ('api-request', 'api-request-blob', 'api-request-shape', 'atis-latch', 'agent-name', 'mode',
                  'data-flow-records', 'data-flow-incomplete', 'frame-link', 'bridge-session', 'pr-link',
                  'tools-used', 'derived-from', 'artifact-autoreact-ledger', 'artifact-comment-monitor',
                  'file-history-delta', 'file-history-snapshot', 'cost-state', 'session-owner', 'queue-operation')
# system subtypes: compact_boundary is BOUNDARY; every other one (stop_hook_summary, api_error, informational, ...) is
# skipped by the table.
COMPACT_BOUNDARY = 'compact_boundary'
# attachment types: queued_command is read; every other one (environment, file, hook output, skill lists, ...) is
# skipped by the table.
QUEUED_COMMAND = 'queued_command'
# Blocks in a user line's content.
USER_TEXT_BLOCKS = ('text',)
USER_PICTURE_BLOCKS = ('image', 'document')         # counted in ReadInfo.images, never kept
TOOL_RESULT_BLOCK = 'tool_result'
RESULT_PICTURE_BLOCKS = ('image', 'document', 'tool_reference')     # inside a tool_result: counted, never kept
# Blocks in an assistant line's content.
ASSISTANT_REASONING_BLOCKS = ('thinking', 'redacted_thinking')      # counted in ReadInfo.reasoning
TOOL_USE_BLOCK = 'tool_use'
# A tool call's input keys that name a file it reads or changes.
FILE_KEYS = ('file_path', 'notebook_path', 'path')
# Tools that change the files they name. When the result says error the change was not made, and the files are left
# off the call and its result, except one that usually holds a secret (the note then still leaves that call out).
CHANGING_TOOLS = ('Edit', 'Write', 'MultiEdit', 'NotebookEdit')
SHELL_TOOLS = ('Bash',)     # input 'command': split (never run) for the files a secret may be in
PLAN_TOOLS = ('TodoWrite',)  # input 'todos': [{content, status}] [not seen on 2.1.309-2.1.330: no chat used it]
HEADLESS_ENTRYPOINTS = ('sdk', 'exec')     # an entrypoint (Claude) or source (Codex) that starts so is a run nobody
                                           # watched: offered only when named ('exec' for Codex is [inferred])
# A queued_command and a user line of the same words are one message only when they lie this close in the file
# (the queued copy's text was in no user line at all over 24 queued messages of 2.1.309-2.1.330, so a copy, when an
# app writes one, is beside it [inferred]).
COPY_WITHIN = 8
# last-prompt.lastPrompt is the app's own copy of a prompt, cut to 200 characters (201 with its ellipsis), and it also
# records prompts the app wrote: used only when no PERSON event was found, and never when it begins with a tag.
LAST_PROMPT_CUT = 200


def headless(entrypoint) -> bool:
    return isinstance(entrypoint, str) and entrypoint.startswith(HEADLESS_ENTRYPOINTS)


# ---------- Codex (0.153.4 to 0.169.1) ----------

# The first line of a session file is its own session_meta; a helper's file holds its parent's too, later (20 lines in
# 19 files). Only threads a person started are chats: thread_source 'user' and source a word ('vscode' for the
# desktop app and the editor; a dict for a helper or a reviewer).
CODEX_META_KEYS = ('id', 'cwd', 'cli_version', 'thread_source', 'source', 'timestamp')
CODEX_PERSON_THREAD = 'user'
# A UserMessage item is the person's when it has client_id (46 typed, 12 answers to the app's questions), or, in a
# file where no UserMessage has client_id (a client that does not write it [not seen]), when its first text part
# begins plain or with one of these tags. Every other one is the app's own message (11 heartbeats, no client_id).
CODEX_PERSON_TAGS = ('send_user_message_question_reply',)
CODEX_APP_TAGS = ('heartbeat',)
# The same rule judges a UserMessage older than the first one of its file with client_id (a thread begun on a
# version that wrote none and carried on in one that does [not seen: inferred]).
# A user-role message that is the project's instructions, never the person's: Codex before 0.169 wrote AGENTS.md so,
# a heading then a whole <INSTRUCTIONS> element [inferred: no file of a version that did was in the census].
CODEX_INSTRUCTIONS_HEAD = '# AGENTS.md instructions for'
_INSTRUCTIONS_RE = re.compile(r'<INSTRUCTIONS>[\s\S]*</INSTRUCTIONS>')


def codex_instructions(text: str) -> bool:
    return text.lstrip().startswith(CODEX_INSTRUCTIONS_HEAD) or bool(_INSTRUCTIONS_RE.search(text))


# item_completed item types (0.169): the three that are tools, the two that are counted, and the rest left out.
CODEX_TOOL_ITEMS = ('CommandExecution', 'FileChange', 'McpToolCall')
CODEX_SKIPPED_ITEMS = ('AgentMessage', 'SubAgentActivity', 'Extension', 'ContextCompaction', 'FunctionCallOutput',
                       'CollabAgentToolCall')
CODEX_SHELL_FLAGS = ('-c', '-lc')       # the word after one of these is a shell line: split for its files
# Top-level line kinds left out (counted as skipped).
CODEX_SKIPPED_LINES = ('world_state', 'token_usage_record', 'inter_agent_communication_metadata')
# event_msg payload types left out (token_count is read by saved_limits only).
CODEX_SKIPPED_EVENTS = ('token_count', 'task_started', 'turn_aborted', 'thread_settings_applied', 'agent_message')
# response_item payload types left out; role 'developer' messages are the app's own instructions [inferred].
# configuration_update: the app's own record of a setting changed in the chat (0.169.1, seen Nov 26: a reasoning
# effort and nothing else; no chat text).
CODEX_SKIPPED_RESPONSES = ('compaction', 'agent_message', 'additional_tools', 'configuration_update')
CODEX_SKIPPED_ROLES = ('developer', 'system')
CODEX_PLAN_TOOLS = ('update_plan',)     # [not seen on 0.169: every custom_tool_call is named exec]
CODEX_PATCH_TOOLS = ('apply_patch',)    # [not seen on 0.169]
CODEX_SHELL_CALLS = ('shell', 'exec_command')   # function_call names whose arguments hold a command [not seen]
CODEX_PATCH_MARKS = ('*** Update File: ', '*** Add File: ', '*** Delete File: ')
# Saved numbers (event_msg/token_count, rate_limits; 1,900 of 1,901 lines carry one).
CODEX_LIMIT_ID = 'codex'
SAVED_LIMITS_FILES = 5
SAVED_LIMITS_DAYS = 7
SAVED_LIMITS_BYTES = 4 << 20
SAVED_LIMITS_SECONDS = 1.0
NEWEST_SEEN_FILES = 50
INDEX_BYTES = 4 << 20       # session_index.jsonl is read from its end up to this


def window_label(minutes) -> Optional[str]:
    """A window's name from its real length: 300 minutes is '5-hour', 10080 'weekly', else '<n>-day' or
    '<n>-hour'."""
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or minutes <= 0:
        return None
    minutes = int(minutes)
    if minutes == 300:
        return '5-hour'
    if minutes == 10080:
        return 'weekly'
    if minutes % 1440 == 0:
        return '%d-day' % (minutes // 1440)
    return '%d-hour' % max(1, round(minutes / 60.0))
