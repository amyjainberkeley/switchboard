"""Models: the right model for each job, using only Claude Code's own settings.

Switchboard never sits between Claude Code and Anthropic. This module only reads and edits
three things Claude Code itself documents: the `env` block of the user settings file
(~/.claude/settings.json), the user agents folder (~/.claude/agents/) and the user rules folder
(~/.claude/rules/). It never changes the chat's own model, never overrides a helper that names its
model, and never edits a key or a file it did not set.

What decides a helper's model today (code.claude.com/docs, checked against Claude Code 2.1.331; the sentences
are in DOCS below, each with its URL):

  1. the model Claude passes when it starts the helper (aliases only),
  2. the `model` line in the helper's definition (`inherit` means the chat's model),
  3. CLAUDE_CODE_SUBAGENT_MODEL,
  4. the chat's own model.
  CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1 turns this upside down: one model for every helper.
  Workflow agents follow the same order. Forks always run on the chat's model. The built-in
  Explore and Plan helpers ignore CLAUDE_CODE_SUBAGENT_MODEL; a user agent named Explore
  replaces the built-in one and keeps its own model line.

Two switches, each on or off by itself (PARTS), and presets made of them (PRESETS):

  Models by the job   four user agent files, agents/switchboard-{lookup,reader,reviewer,verifier}.md
    ('helpers')       (lookup on Haiku; reader on Sonnet at medium effort; reviewer and verifier on
                      Sonnet at high effort), and one note, rules/switchboard-models.md, that says
                      which helper is for what. No setting.
  Sonnet for helpers  env CLAUDE_CODE_SUBAGENT_MODEL=sonnet in the user settings file, so helpers and
    ('sonnet')        workflow agents that name no model run on Sonnet instead of the chat's model,
                      and CLAUDE_CODE_SUBAGENT_EFFORT=high where Claude Code reads it (2.1.316).

  Presets: off, helpers, sonnet, helpers-and-sonnet; and by-the-job, the earlier preset (Sonnet for
  helpers plus agents/switchboard-explore.md, name: Explore, model: haiku), which records written
  before the two switches name. plan(preset, only=part) turns one switch and leaves the other as it is.
  Everything small is not offered: the only documented switch is the FORCE pair, which
  overrides helpers that name their own model (see LIMITS).

current() reads; plan() says exactly what would change and changes nothing; apply() writes
atomically, keeps a backup, and records what it did in Switchboard's own state file (one record for
both switches) so undo() can put every file back byte for byte. would_have_moved() and seen() read a
usage summary (switchboard.usage.summarize).

What this cannot do (limits() returns the same list with the doc sentence behind each):

  - Pick a model per message by reading the prompt.
  - Give each kind of helper its own default model.
  - Cap or size workflow steps by kind.
  - Move forks off the chat's model.
  - Move the built-in Plan helper off the chat's model.
  - Put Explore on Haiku without replacing the built-in Explore.
  - Offer "Everything small" without overriding helpers that name their own model.
  - Keep a change away from sessions that are already running.
  - Take a removal back from sessions that are already running.
  - Win over project or managed settings, or over the Desktop app's launch environment.
  - Override CLAUDE_CODE_EFFORT_LEVEL for one helper.
  - Use a hook or a mod to choose a helper's model.
  - Say how much of a plan's usage limit a model's tokens use.
  - Make Claude hand a job to a helper.

Manual check (reads only):  python3 -m switchboard.models
"""
import hashlib
import json
import os
import re
import sys
import tempfile
import time

STATE_VERSION = 1
SETTINGS_NAME = 'settings.json'
AGENTS_DIR = 'agents'
RULES_DIR = 'rules'
EXPLORE_FILE = 'switchboard-explore.md'     # identity comes from `name:`, not the file name
NOTE_FILE = 'switchboard-models.md'         # a user rule: loaded in every project (DOCS user_rules)
SUBAGENT_VAR = 'CLAUDE_CODE_SUBAGENT_MODEL'
SUBAGENT_EFFORT_VAR = 'CLAUDE_CODE_SUBAGENT_EFFORT'
FORCE_VAR = 'CLAUDE_CODE_SUBAGENT_MODEL_FORCE'
EFFORT_VAR = 'CLAUDE_CODE_EFFORT_LEVEL'
NO_EXPLORE_PLAN_VAR = 'CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS'
# The environment variables that decide models or effort. Only these are ever reported;
# other `env` entries (keys, tokens) are never read out.
MODEL_VARS = (SUBAGENT_VAR, SUBAGENT_EFFORT_VAR, FORCE_VAR, EFFORT_VAR, 'ANTHROPIC_MODEL', 'ANTHROPIC_DEFAULT_MODEL',
              'ANTHROPIC_DEFAULT_FABLE_MODEL', 'ANTHROPIC_DEFAULT_OPUS_MODEL',
              'ANTHROPIC_DEFAULT_SONNET_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL',
              NO_EXPLORE_PLAN_VAR, 'CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS')
MODEL_KEYS = ('model', 'effortLevel', 'modelSettings', 'availableModels', 'advisorModel', 'fallbackModel')
# Versions below these let the change do something other than what the preset says.
NEEDS_ORDER = (2, 1, 251)       # before it, CLAUDE_CODE_SUBAGENT_MODEL overrode named models
NEEDS_OMIT = (2, 1, 271)        # omitClaudeMd, which keeps our Explore as lean as the built-in
# Models by the job: two of its helper files use omitClaudeMd, which needs this version (DOCS omit_claude_md). It sets
# no CLAUDE_CODE_SUBAGENT_MODEL, so NEEDS_ORDER is not its business.
NEEDS_HELPERS = NEEDS_OMIT
# A settings file that can't be understood blocks Models by the job: it may deny one of the helpers. The row, the
# command and the plan say why; a press that was refused for it says that nothing was written, too.
HELPERS_UNREADABLE_WHY = ("Claude Code's settings file can't be read, so Switchboard can't tell whether you deny one "
                          'of these helpers.')
HELPERS_UNREADABLE = HELPERS_UNREADABLE_WHY + ' Nothing was written.'
# CLAUDE_CODE_SUBAGENT_EFFORT: the changelog adds it in 2.1.316 (Oct 31 2026); the env page says it needs 2.1.315.
# The later of the two is used (DOCS subagent_effort_added and subagent_effort).
NEEDS_EFFORT = (2, 1, 316)
SONNET_EFFORT = 'high'
BUSY_SECONDS = 300

DOCS_BASE = 'https://code.claude.com/docs/en/'
# Each sentence this module relies on, quoted from the live docs (checked against Claude Code 2.1.331).
DOCS = {
    'order': ('sub-agents#choose-a-model',
              'When more than one source sets a model, Claude Code uses the first in this order: 1. The '
              'per-invocation `model` parameter, which Claude can pass when it starts the subagent. ... 2. The '
              "`model` field in the subagent's definition, including `inherit` 3. The `CLAUDE_CODE_SUBAGENT_MODEL` "
              "environment variable 4. The main conversation's model, or the spawning subagent's model for a nested "
              'subagent'),
    'order_before': ('env-vars#variables',
                     "Before v2.1.251, this variable overrode both the per-invocation model and the definition's "
                     '`model` field'),
    'var': ('env-vars#variables',
            'The default model for subagents, agent team teammates, and workflow agents that aren\'t assigned a '
            'model another way. Accepts an alias such as `haiku` or a full model name. Two sources take '
            'precedence over it: a model Claude passes when it spawns the agent, and a `model` field in the '
            'agent\'s definition, including `inherit`.'),
    'var_inherit': ('env-vars#variables', 'Setting it to `inherit` is the same as leaving it unset.'),
    'explore_plan': ('sub-agents#choose-a-model',
                     "Setting `CLAUDE_CODE_SUBAGENT_MODEL` by itself doesn't change the model the built-in Explore "
                     'and Plan subagents run on.'),
    'explore_override': ('sub-agents#built-in-subagents',
                         'A user or project subagent named `Explore` overrides the built-in and keeps its own '
                         '`model` field, so define one with `model: haiku` to run exploration on a lower-cost model.'),
    'explore_model': ('sub-agents#built-in-subagents',
                      "**Model**: the parent conversation's model. When the parent conversation runs Fable, Explore "
                      'runs on Opus or stays on Fable, based on how you connect. ... With a Claude subscription, an '
                      'Anthropic Console account, or an LLM gateway reached through `ANTHROPIC_BASE_URL`, Explore '
                      'runs on the Opus model that the `opus` alias resolves to.'),
    'explore_tools': ('sub-agents#built-in-subagents', '**Tools**: read-only tools; Write and Edit are denied'),
    'explore_lean': ('sub-agents#built-in-subagents',
                     'Explore and Plan start without your CLAUDE.md files and the git status snapshot to keep '
                     'research fast and inexpensive.'),
    'git_status': ('sub-agents#what-loads-at-startup',
                   "Explore and Plan skip it regardless. ... You can't change which subagents receive git status."),
    'prompts': ('sub-agents#what-loads-at-startup',
                'Custom subagents define theirs in the markdown body or `prompt` field.'),
    'plan_agent': ('sub-agents#built-in-subagents',
                   '**Model**: inherits from the parent conversation, unless you set `CLAUDE_CODE_SUBAGENT_MODEL` and '
                   'force it onto every subagent'),
    'general_purpose': ('sub-agents#built-in-subagents',
                        "**Model**: the parent conversation's model by default, following the model order"),
    'claude_agent': ('sub-agents#built-in-subagents',
                     'None of its own; follows the model order when Claude spawns it as a subagent'),
    'own_model_builtins': ('sub-agents#built-in-subagents',
                           '| statusline-setup | Sonnet | ... | claude-code-guide | Haiku |'),
    'fork': ('sub-agents#how-forks-differ-from-other-subagents',
             '| Model | Same as the parent conversation | Follows the subagent model order |'),
    'force': ('sub-agents#run-every-subagent-on-one-model',
              "While `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` is on, Claude Code ignores the `model` field in subagent "
              "definitions, and Claude can't pass a model when it starts a subagent. The following subagents still "
              "run on their parent conversation's model: ... A fork ... A skill that runs in a subagent with "
              '`model: inherit`'),
    'force_two_vars': ('sub-agents#run-every-subagent-on-one-model',
                       'To apply one model to every subagent, teammate, and workflow agent, set '
                       '`CLAUDE_CODE_SUBAGENT_MODEL` to that model and `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` to `1`.'),
    'workflows': ('workflows#cost',
                  "Claude Code picks each workflow agent's model in the same order it uses for subagents. A model "
                  'the script names for a stage counts as the per-invocation model in that order. When nothing '
                  "else assigns one, the agent runs on your session's model."),
    'workflow_size': ('workflows#set-a-size-guideline',
                      'Claude Code sends the guideline to Claude as advice, not a cap, so a prompt that calls for a '
                      'different scale still overrides it.'),
    'strict_json': ('settings#edit-a-settings-file',
                    'Settings files are strict JSON: a `//` comment or a trailing comma is a syntax error, and '
                    'Claude Code reports the file as a Settings Error at the next start.'),
    'env_shell': ('settings-reference#how-env-values-interact-with-your-shell',
                  'A value here overwrites the same variable exported in your shell, and when more than one '
                  'settings file sets a variable, the highest-precedence one applies.'),
    'env_desktop': ('settings-reference#how-env-values-interact-with-your-shell',
                    'When the Claude Desktop app or a self-hosted environment runner starts the session, the launch '
                    'environment it builds takes precedence instead. Claude Code ignores an `env` value from a '
                    'settings file for a variable that environment already sets.'),
    'env_levels': ('env-vars#precedence',
                   'Between settings files, `env` values follow settings precedence, so a managed settings entry '
                   'overrides the same variable in user or project settings.'),
    'env_running': ('env-vars#set-variables-in-settings-files',
                    'A running session applies new and changed values to its environment when you save the file, '
                    "but a feature that reads its variables once at startup, such as OpenTelemetry monitoring, keeps "
                    'its startup values until you relaunch. Removing a variable from the file doesn\'t unset it in a '
                    'running session; the removal takes effect the next time you launch `claude`.'),
    'agents_watch': ('sub-agents#write-subagent-files',
                     'When you add or edit a subagent file on disk, or ask Claude to write one for you, Claude Code '
                     'detects the change within a few seconds and the next delegation uses the updated definition, '
                     'with no restart needed.'),
    'agents_scan': ('sub-agents#choose-the-subagent-scope',
                    'Claude Code scans `.claude/agents/` and `~/.claude/agents/` recursively, so you can organize '
                    'definitions into subfolders such as `agents/review/` or `agents/research/`. The subdirectory '
                    "path doesn't affect how a subagent is identified or invoked, because identity comes only from "
                    'the `name` frontmatter field.'),
    'agents_priority': ('sub-agents#choose-the-subagent-scope',
                        'When multiple subagents share the same name, Claude Code uses the one from the '
                        'higher-priority location.'),
    'omit_claude_md': ('sub-agents#supported-frontmatter-fields',
                       'Set to `true` to start this subagent without the user, project, and local CLAUDE.md files '
                       'in its initial context ... Requires Claude Code v2.1.271 or later'),
    'disable_explore': ('env-vars#variables',
                        'Set to `1` to disable the built-in Explore and Plan subagents. ... Custom subagents named '
                        '`Explore` or `Plan` are unaffected.'),
    'deny_agent': ('sub-agents#disable-specific-subagents',
                   'This works for both built-in and custom subagents.'),
    'effort_var': ('sub-agents#supported-frontmatter-fields',
                   'Effort level when this subagent is active. Overrides the session effort level, but not the '
                   '`CLAUDE_CODE_EFFORT_LEVEL` environment variable.'),
    'skill_model': ('skills#frontmatter-reference',
                    'Model to use when this skill is active. The override applies for the rest of the current turn '
                    "and isn't saved to settings. The session model resumes when you send your next prompt."),
    'model_order': ('model-config#setting-your-model',
                    'You can configure your model in several ways, listed in order of priority: 1. During session '
                    '... 2. At startup ... 3. Environment variable ... 4. Settings ... 5. Default for new sessions'),
    'prompt_hook': ('hooks', "`UserPromptSubmit`: can't replace the prompt; it only injects `additionalContext` "
                             'alongside it'),
    'start_hook': ('hooks', '| SessionStart, SubagentStart, PostModelSwitch | Context only |'),
    'tool_hook': ('hooks', '`PreToolUse`: `updatedInput` directly under `hookSpecificOutput` replaces a tool\'s '
                           'arguments before it runs.'),
    'agent_input': ('hooks', '| `model` | string | `"sonnet"` | Optional model alias to override the default |'),
    'mod_spawn': ('sub-agents#choose-a-model',
                  'If an installed mod sets a model in its `agent.spawn` hook, Claude Code uses that model in place '
                  'of the per-invocation parameter.'),
    'limits_shared': ('costs',
                      "a seat-based usage window on a subscription plan, shared across all models, so the developer "
                      "can't restore access by switching models with `/model`."),
    'managed_path': ('managed-settings', '**macOS**: `/Library/Application Support/ClaudeCode/managed-settings.json`'),
    # Models by the job and the effort of Sonnet for helpers (checked against the docs of Claude Code 2.1.331).
    'subagent_effort': ('env-vars#variables',
                        'The default effort level for subagents and workflow agents: `low`, `medium`, `high`, `xhigh`, '
                        "or `max`. It doesn't apply to forks, forked skills, or teammates. Requires Claude Code "
                        'v2.1.315 or later'),
    'subagent_effort_added': ('changelog',
                              'Added `CLAUDE_CODE_SUBAGENT_EFFORT` to set the effort level subagents run at when '
                              "nothing else names one, instead of inheriting the session's"),
    'effort_order': ('sub-agents#when-a-subagent-has-no-effort-field-or-parameter',
                     'A subagent other than a fork, forked skill, or teammate runs at the first of these that '
                     'applies to it: 1. The level in `CLAUDE_CODE_EFFORT_LEVEL`, if you set it 2. The `effort` set '
                     'for the subagent that spawned it, or a level set only for the current turn, such as a '
                     "skill's `effort` 3. The level in `CLAUDE_CODE_SUBAGENT_EFFORT`, if you set it"),
    'agent_required': ('sub-agents#frontmatter-reference', 'Only `name` and `description` are required.'),
    'agent_rows': ('sub-agents#supported-frontmatter-fields',      # the six fields the helper files use
                   '| `name` | Yes | Unique identifier of at most 256 characters ... | `description` | Yes | When '
                   'Claude should delegate to this subagent | ... | `disallowedTools` | No | Tools to deny, removed '
                   'from inherited or specified list. ... | `model` | No | Model to use: ... | `omitClaudeMd` | No | '
                   'Set to `true` to start this subagent without the user, project, and local CLAUDE.md files ... | '
                   '`effort` | No | Effort level when this subagent is active.'),
    'agent_unknown': ('sub-agents#frontmatter-reference',
                      'Multi-word field names use camelCase, such as `maxTurns` and `disallowedTools`, and must match '
                      "the table exactly: Claude Code ignores a field it doesn't recognize without reporting an "
                      'error.'),
    'agent_model': ('sub-agents#frontmatter-reference',
                    'Model to use: `sonnet`, `opus`, `haiku`, `fable`, a full model ID such as `claude-opus-5-5`, or '
                    '`inherit`.'),
    'effort_levels': ('sub-agents#frontmatter-reference',
                      'Options: `low`, `medium`, `high`, `xhigh`, `max`; available levels depend on the model.'),
    'agent_description': ('sub-agents#frontmatter-reference', 'When Claude should delegate to this subagent'),
    'delegation': ('sub-agents#understand-automatic-delegation',
                   'Claude automatically delegates tasks based on the task description in your request, the '
                   '`description` field in subagent configurations, and current context.'),
    'disallowed_tools': ('sub-agents#frontmatter-reference',
                         'Tools to deny, removed from inherited or specified list.'),
    'omit_use': ('sub-agents#frontmatter-reference',
                 'Use it for subagents that take everything they need from the delegation prompt.'),
    'claude_md_main': ('sub-agents#what-loads-at-startup',
                       "The main conversation still has your full CLAUDE.md when it reads these subagents' results, "
                       "so most rules don't need to reach the subagent itself."),
    'managed_agents': ('sub-agents#choose-the-subagent-scope',
                       'Managed definitions take precedence over project and user subagents with the same name.'),
    'same_name_one_dir': ('sub-agents#choose-the-subagent-scope',
                          'If two files under the same `.claude/agents/` directory declare the same name, Claude '
                          'Code loads only one of them.'),
    'user_rules': ('memory#user-level-rules',
                   'Personal rules in `~/.claude/rules/` apply to every project on your machine.'),
    'rules_launch': ('memory#set-up-rules',
                     'Rules without `paths` frontmatter are loaded at launch with the same priority as '
                     '`.claude/CLAUDE.md`.'),
    'rules_recursive': ('memory#set-up-rules',
                        'All `.md` files are discovered recursively, so you can organize rules into subdirectories '
                        'like `frontend/` or `backend/`'),
    'effort_param': ('sub-agents#choose-an-effort-level',
                     'When you ask Claude to run a non-fork subagent at a specific effort level, it can also pass an '
                     '`effort` parameter for that invocation. ... The per-invocation parameter requires Claude Code '
                     'v2.1.292 or later.'),
    'haiku_default': ('model-config#adjust-effort-level',
                      'Fable 5.2, Opus 5.5, Sonnet 5.5, and Haiku 5.5 default to `medium`'),
    'tools_bash': ('sub-agents#code-reviewer',
                   'A subagent that reviews code without the Edit and Write tools. It can still change files through '
                   'Bash.'),
    'effort_by_model': ('model-config#adjust-effort-level',
                        '| Opus 5.5, Sonnet 5.5, Haiku 5.5, Opus 5, Sonnet 5, Opus 4.8, and Opus 4.7 | `low`, `medium`, '
                        '`high`, `xhigh`, `max` |'),
}


def doc(key):
    """(quote, url) for a DOCS entry."""
    page, quote = DOCS[key]
    return quote, DOCS_BASE + page


# What this cannot do. Same order as the module docstring; a test keeps them in step.
LIMITS = (
    ('Pick a model per message by reading the prompt.',
     'Every documented control is fixed in advance (a setting, a flag, a definition, a skill); none reads the '
     'prompt, and the hook that sees the prompt can only add context.', ('model_order', 'prompt_hook')),
    ('Give each kind of helper its own default model.',
     'CLAUDE_CODE_SUBAGENT_MODEL is one value for every helper that names no model. A per-kind model needs '
     'a definition per kind, and a definition only applies when Claude picks that helper.', ('var',)),
    ('Cap or size workflow steps by kind.',
     "A workflow step's model is whatever the script Claude writes names; settings give one default for all "
     'steps, and the size guideline is advice.', ('workflows', 'workflow_size')),
    ('Move forks off the chat\'s model.',
     'A fork shares the chat\'s model and cache; no setting changes that, not even FORCE.', ('fork', 'force')),
    ('Move the built-in Plan helper off the chat\'s model.',
     'Plan ignores CLAUDE_CODE_SUBAGENT_MODEL unless FORCE is on, and FORCE breaks the never-override rule.',
     ('explore_plan', 'plan_agent')),
    ('Put Explore on Haiku without replacing the built-in Explore.',
     "The documented way is a user agent named Explore with model: haiku. It replaces the built-in's own "
     'instructions (not published) with Switchboard\'s, and it gets the git status snapshot the built-in '
     'skips. Switchboard keeps it read-only and without CLAUDE.md, like the built-in.',
     ('explore_override', 'prompts', 'git_status', 'explore_lean')),
    ('Offer "Everything small" without overriding helpers that name their own model.',
     'The only switch that moves every helper is the FORCE pair: two variables, and it ignores the model '
     'line in every definition. Forks and inherit skills still stay on the chat\'s model.',
     ('force_two_vars', 'force')),
    ('Keep a change away from sessions that are already running.',
     'Claude Code reloads the env block and the agents folder in running sessions, so helpers started after '
     'apply() use the new model. apply() therefore refuses while a chat is busy unless told otherwise.',
     ('env_running', 'agents_watch')),
    ('Take a removal back from sessions that are already running.',
     'Removing the variable (Off or undo) reaches new sessions only; running ones keep Sonnet for new helpers '
     'until they are restarted.', ('env_running',)),
    ('Win over project or managed settings, or over the Desktop app\'s launch environment.',
     'Switchboard edits the user settings file and the user agents folder. A project or managed file that sets '
     'the same variable wins, so does a project agent named Explore, and so does a variable the Desktop app sets '
     'when it launches a session. Switchboard cannot see project folders or server-managed settings.',
     ('env_levels', 'agents_priority', 'env_desktop')),
    ('Override CLAUDE_CODE_EFFORT_LEVEL for one helper.',
     'When that variable is set it beats every effort line; Switchboard reports it and leaves it.',
     ('effort_var',)),
    ('Use a hook or a mod to choose a helper\'s model.',
     "Start hooks only add context. A PreToolUse hook can replace a tool's arguments and the Agent tool has a "
     'model argument, but no page says Claude Code honours a model changed that way, so it is not used. A mod '
     'can choose the model, but a mod is code inside Claude Code, not a setting.',
     ('start_hook', 'tool_hook', 'agent_input', 'mod_spawn')),
    ('Say how much of a plan\'s usage limit a model\'s tokens use.',
     'The docs say the weekly and session windows are shared across all models and give no per-model weight, '
     'so would_have_moved() counts weighted tokens, not limit.', ('limits_shared',)),
    ('Make Claude hand a job to a helper.',
     'Claude picks a helper by its description and the task; the note says which helper is for what, and it is '
     'advice. A project\'s own helper with the same name wins in that project.',
     ('delegation', 'agent_description', 'agents_priority')),
)


def limits():
    """What this cannot do, each with the doc sentences that show it."""
    out = []
    for cannot, why, keys in LIMITS:
        out.append({'cannot': cannot, 'why': why,
                    'docs': [{'quote': doc(k)[0], 'url': doc(k)[1]} for k in keys]})
    return out


class Refused(Exception):
    """Switchboard will not do this. str(e) is the plain reason."""


# ---------------------------------------------------------------- paths, resolved at call time

def default_claude_dir():
    return os.environ.get('CLAUDE_CONFIG_DIR') or os.path.join(os.path.expanduser('~'), '.claude')


def default_state_path():
    data = os.environ.get('SWITCHBOARD_HOME') or os.path.join(os.path.expanduser('~'), '.switchboard')
    return os.path.join(data, 'models.json')


def default_managed_dir():
    if sys.platform == 'darwin':
        return '/Library/Application Support/ClaudeCode'
    if sys.platform.startswith('linux'):
        return '/etc/claude-code'
    return None


def _cd(claude_dir):
    return os.path.realpath(os.path.abspath(os.path.expanduser(claude_dir or default_claude_dir())))


# ---------------------------------------------------------------- small file helpers

def _read(path):
    """The file's bytes, or None when there is no file."""
    try:
        with open(path, 'rb') as f:
            return f.read()
    except FileNotFoundError:
        return None


def _sha(data):
    return None if data is None else hashlib.sha256(data).hexdigest()


def _atomic_write(path, data, mode):
    """Write bytes through a temp file in the same folder, then rename over the target."""
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix='.%s.' % os.path.basename(path), suffix='.switchboard-tmp')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _mode(path, default):
    try:
        return os.stat(path).st_mode & 0o7777
    except FileNotFoundError:
        return default


def _ver(text):
    m = re.match(r'^v?(\d+)\.(\d+)\.(\d+)', text or '')
    return tuple(int(x) for x in m.groups()) if m else None


def _vs(v):
    return '.'.join(str(x) for x in v)


# ---------------------------------------------------------------- settings JSON, edited in place
#
# The edit touches only the bytes of the member it adds, changes or removes. Everything else
# (key order, spacing, tabs, CRLF, escapes, the trailing newline) stays as the user wrote it.
# After every edit the result is parsed and compared with the expected object, so a wrong
# splice can never be written.

_WS = ' \t\n\r'


def _no_constant(name):
    raise ValueError('%s is not JSON' % name)


_DEC = json.JSONDecoder(parse_constant=_no_constant)


def _skip(text, i):
    n = len(text)
    while i < n and text[i] in _WS:
        i += 1
    return i


def _object_at(text, i):
    """Members of the JSON object whose '{' is text[i], and the index just past its '}'.
    Each member is a dict: key, k0 (its opening quote), k1 (past its closing quote), v0, v1 (its value)."""
    if i >= len(text) or text[i] != '{':
        raise ValueError('not an object')
    j = _skip(text, i + 1)
    members = []
    if j < len(text) and text[j] == '}':
        return members, j + 1
    while True:
        if j >= len(text) or text[j] != '"':
            raise ValueError('expected a key at %d' % j)
        key, k1 = json.decoder.scanstring(text, j + 1, True)
        c = _skip(text, k1)
        if c >= len(text) or text[c] != ':':
            raise ValueError('expected ":" at %d' % c)
        v0 = _skip(text, c + 1)
        value, v1 = _DEC.raw_decode(text, v0)
        members.append({'key': key, 'k0': j, 'k1': k1, 'v0': v0, 'v1': v1, 'value': value})
        e = _skip(text, v1)
        if e < len(text) and text[e] == ',':
            j = _skip(text, e + 1)
            continue
        if e < len(text) and text[e] == '}':
            return members, e + 1
        raise ValueError('expected "," or "}" at %d' % e)


class _Doc(object):
    """A settings file's text and the spans of its top-level object."""

    def __init__(self, raw, path):
        self.path = path
        if raw is None:
            text = '{}\n'           # no file yet: start from an empty object
        elif raw.startswith(b'\xef\xbb\xbf'):
            raise Refused('%s starts with a byte-order mark. Switchboard edits plain UTF-8 JSON only.' % path)
        else:
            try:
                text = raw.decode('utf-8')
            except UnicodeDecodeError:
                raise Refused('%s is not UTF-8 text.' % path)
            if not text.strip():
                text = '{}' + (text or '\n')    # an empty file holds no settings; keep its whitespace after
        self.text = text
        self.parse()

    def parse(self):
        text = self.text
        try:
            whole = _DEC.decode(text) if text.strip() else {}
            if not isinstance(whole, dict):
                raise ValueError('the top level is not an object')
            self.o = _skip(text, 0)
            self.members, self.end = _object_at(text, self.o)
            if _skip(text, self.end) != len(text):
                raise ValueError('text after the object')
        except ValueError as e:
            q, url = doc('strict_json')
            raise Refused('%s is not valid JSON (%s). Claude Code reads settings as strict JSON ("%s" %s), so '
                          'Switchboard will not edit it.' % (self.path, e, q, url))
        self.obj = whole

    def member(self, key, members=None):
        found = [m for m in (self.members if members is None else members) if m['key'] == key]
        if len(found) > 1:
            raise Refused('%s has the key "%s" %d times; Switchboard will not guess which one Claude Code uses.'
                          % (self.path, key, len(found)))
        return found[0] if found else None

    def env(self):
        """(env member or None, its members) for the top-level "env"."""
        m = self.member('env')
        if m is None:
            return None, []
        if not isinstance(m['value'], dict):
            raise Refused('In %s, "env" is not an object; Claude Code would reject it, so Switchboard leaves the '
                          'file alone.' % self.path)
        members, _ = _object_at(self.text, m['v0'])
        return m, members


def _nl(text):
    return '\r\n' if '\r\n' in text else '\n'


def _ws_before(text, pos):
    p = pos
    while p > 0 and text[p - 1] in _WS:
        p -= 1
    return text[p:pos]


def _line_indent(text, pos):
    start = text.rfind('\n', 0, pos) + 1
    j = start
    while j < len(text) and text[j] in ' \t':
        j += 1
    return text[start:j]


def _unit(doc_):
    """The file's indent step, from the first top-level member on its own line; two spaces if none."""
    base = _line_indent(doc_.text, doc_.o)
    for m in doc_.members:
        sep = _ws_before(doc_.text, m['k0'])
        if '\n' in sep:
            ind = sep[sep.rfind('\n') + 1:]
            return ind[len(base):] if ind.startswith(base) else ind
    return '  '


def _jdump(value):
    return json.dumps(value, ensure_ascii=False)


def _insert(text, o, end, members, key, render, unit, multiline_if_empty):
    """Text with `"key": value` added as the last member of the object at text[o:end].
    render(multiline, indent) gives the value's text. A member after others copies the spacing of
    the one before it, so taking it out again restores the text exactly."""
    nl = _nl(text)
    if members:
        last = members[-1]
        sep = _ws_before(text, last['k0'])
        colon = text[last['k1']:last['v0']]
        if '\n' in sep:
            ind = sep[sep.rfind('\n') + 1:]
            piece = ',' + nl + ind + _jdump(key) + colon + render(True, ind)
        else:
            if not sep and len(members) == 1 and colon.endswith(' '):
                sep = ' '           # {"a": 1} has no member gap to copy; its spaced colon says "a, b" is its style
            piece = ',' + sep + _jdump(key) + colon + render(False, '')
        return text[:last['v1']] + piece + text[last['v1']:]
    close = end - 1
    base = _line_indent(text, o)
    if multiline_if_empty:
        ind = base + unit
        inner = nl + ind + _jdump(key) + ': ' + render(True, ind) + nl + base
    else:
        inner = _jdump(key) + ': ' + render(False, '')
    return text[:o + 1] + inner + text[close:]


def _remove(text, o, end, members, i):
    """Text with member i of the object at text[o:end] taken out, with its comma."""
    m = members[i]
    if len(members) == 1:
        return text[:o + 1] + text[end - 1:]
    if i == 0:
        return text[:m['k0']] + text[members[1]['k0']:]
    return text[:members[i - 1]['v1']] + text[m['v1']:]


def _env_set(doc_, var, value):
    """New text with env[var] = value. Returns (text, made_env)."""
    text = doc_.text
    unit = _unit(doc_)
    env_m, env_members = doc_.env()
    have = doc_.member(var, env_members) if env_m else None
    if have is not None:
        return text[:have['v0']] + _jdump(value) + text[have['v1']:], False
    if env_m is not None:
        _, env_end = _object_at(text, env_m['v0'])
        multiline = '\n' in _ws_before(text, env_m['k0'])
        return _insert(text, env_m['v0'], env_end, env_members, var, lambda ml, ind: _jdump(value), unit,
                       multiline), False
    nl = _nl(text)
    colon = text[doc_.members[-1]['k1']:doc_.members[-1]['v0']] if doc_.members else ': '

    def render(multiline, ind):
        if multiline:
            return '{' + nl + ind + unit + _jdump(var) + colon + _jdump(value) + nl + ind + '}'
        return '{' + _jdump(var) + colon + _jdump(value) + '}'
    return _insert(text, doc_.o, doc_.end, doc_.members, 'env', render, unit, True), True


def _env_del(doc_, var, drop_empty_env):
    """New text without env[var]; the env object goes too when it ends up empty and drop_empty_env."""
    text = doc_.text
    env_m, env_members = doc_.env()
    have = doc_.member(var, env_members) if env_m else None
    if have is None:
        return text
    if len(env_members) == 1 and drop_empty_env:
        return _remove(text, doc_.o, doc_.end, doc_.members, doc_.members.index(env_m))
    _, env_end = _object_at(text, env_m['v0'])
    return _remove(text, env_m['v0'], env_end, env_members, env_members.index(have))


def _checked(doc_, new_text, expect):
    """new_text, after making sure it parses to exactly `expect`."""
    try:
        got = _DEC.decode(new_text)
    except ValueError:
        got = object()
    if got != expect:
        raise Refused('Switchboard could not make this edit to %s without changing something else; nothing was '
                      'written.' % doc_.path)
    return new_text


# ---------------------------------------------------------------- agent files

def _unquote(v):
    """A YAML scalar as written on one line: a quoted one up to its closing quote (a # inside it stays), else up to a
    comment (a # after a space or a tab)."""
    v = v.strip()
    if v[:1] in ('"', "'"):
        end = v.find(v[0], 1)
        if end > 0:
            return v[1:end]
    return re.split(r'[ \t]#', v, maxsplit=1)[0].rstrip()


def _front_lines(text):
    """The lines of a Markdown file's YAML front matter, between its opening and its closing `---` (None without
    both). A byte-order mark first is skipped."""
    lines = text.lstrip('\ufeff').splitlines()
    if not lines or lines[0].strip() != '---':
        return None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == '---':
            return lines[1:i]
    return None     # no closing ---


def _frontmatter(text):
    """Top-level `key: value` lines of a Markdown file's YAML frontmatter (None when it has none).
    A small reader: enough for name, model and effort lines; nested blocks are skipped. It reads a quoted key or
    value, a comment after either, and a byte-order mark first: a helper of yours named another way must still be
    seen as yours (two with one name in one folder: only one loads). What it can't read: _names_taken."""
    lines = _front_lines(text)
    if lines is None:
        return None
    out = {}
    for line in lines:
        if not line or line[0] in ' \t#-' or ':' not in line:
            continue
        if line[0] in ('"', "'") and line.find(line[0], 1) > 0:
            end = line.find(line[0], 1)
            k, rest = line[1:end], line[end + 1:]
            if not rest.lstrip().startswith(':'):
                continue
            v = rest.lstrip()[1:]
        else:
            k, v = line.split(':', 1)
        out[k.strip()] = _unquote(v)
    return out


# A name written in a YAML form _frontmatter does not follow starts with one of these: a block (> |), an anchor (&),
# a tag (!), an alias (*), a list ([) or a map ({).
NOT_PLAIN = tuple('>|&!*[{')


def _names_taken(text, fm):
    """The names of Switchboard's four helpers that a helper file of yours takes (lower case). Its own name, when this
    reader reads a plain one. When it doesn't (no top-level name, an empty one, or one in a YAML form it does not
    follow: NOT_PLAIN, the value on the next line, an explicit key, a front matter indented as a whole), every one
    of the four that stands as a whole word anywhere in the front matter, in any letter case. Claude Code reads these
    files with a YAML reader; two helpers with one name in one folder load only one of them, and leaving one of
    Switchboard's out costs nothing. A file without front matter takes none."""
    if fm is None:
        return []
    names = [h['name'] for h in HELPERS]
    name = (fm.get('name') or '').strip()
    if name and not name.startswith(NOT_PLAIN):
        return [name.lower()] if name.lower() in names else []
    words = re.findall(r'\b(%s)\b' % '|'.join(names), '\n'.join(_front_lines(text)), re.IGNORECASE)
    return sorted(set(w.lower() for w in words))


def _agents(claude_dir):
    """Every Markdown file under the user agents folder, with its name, model and effort lines."""
    root = os.path.join(claude_dir, AGENTS_DIR)
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            if not fn.endswith('.md'):
                continue
            p = os.path.join(dirpath, fn)
            raw = _read(p) or b''
            text = raw.decode('utf-8', 'replace')
            fm = _frontmatter(text)
            out.append({'file': os.path.relpath(p, claude_dir), 'path': p,
                        'name': (fm or {}).get('name'), 'model': (fm or {}).get('model'),
                        'effort': (fm or {}).get('effort'), 'takes': _names_taken(text, fm), 'sha256': _sha(raw)})
    return out


EXPLORE_TEXT = '''---
name: Explore
description: Fast read-only helper for finding files, searching code and answering questions about a codebase. Use it when the job is looking things up, not changing them. Say how thorough to be - quick for a targeted lookup, medium for balanced exploration, very thorough for a full survey.
model: haiku
disallowedTools: Write, Edit, NotebookEdit
omitClaudeMd: true
# Written by Switchboard (preset "By the job") so lookups run on Haiku. It takes the place of
# Claude Code's built-in Explore helper. Switchboard removes it when you choose Off.
# If you edit this file it becomes yours, and Switchboard will leave it alone.
---

You are a read-only search helper. Find what you were asked to find and report it.

- Use Glob, Grep and Read to find files and code. Use Bash only for commands that read, such as ls, git log, git show or git diff. Never create, change, move or delete anything.
- Match the thoroughness you were given. Quick: the first good answer. Medium: check the likely places. Very thorough: search widely, including other names and spellings.
- Report concrete findings: file paths with line numbers, the names you found, and short excerpts where they help. Say plainly what you looked for and did not find.
- Keep the answer short. The agent that asked sees only your final message.
'''


# ---------------------------------------------------------------- Models by the job: four helpers and a note
#
# Four user agent files, each with its job, model and effort, and one short note in the user rules folder that
# says which helper is for what. Claude picks a helper by its description (DOCS delegation, agent_description), so
# each description starts with when to use it. Every front matter field is one the docs name (DOCS agent_rows,
# agent_required): Claude Code ignores a field it does not know without a word (agent_unknown). None of the four has
# the Write or Edit tool, but each can still run commands and so change files (DOCS tools_bash): it is their
# instructions that tell them to change nothing (the verifier: nothing under test), and no word calls them read-only.
#
# omitClaudeMd, per helper (DOCS omit_use, claude_md_main): lookup and reader take everything they need from the
# request (a thing to find, a source to read), and the chat that reads their answer still has your CLAUDE.md, so
# they start without it, as the built-in Explore does. reviewer and verifier need the project's own rules: what
# counts as wrong here, how this project runs its tests. They load CLAUDE.md as any helper does.
# effort: lookup and reader medium (for lookup that is Haiku's own default in Claude Code, DOCS haiku_default,
# written out so that Sonnet for helpers' CLAUDE_CODE_SUBAGENT_EFFORT never reaches it: a helper's own effort line
# comes first, DOCS effort_order; Haiku 5.5 takes medium, DOCS effort_by_model); reviewer and verifier high.
# CLAUDE_CODE_EFFORT_LEVEL, when you set it, beats every one of these (DOCS effort_var); plan() says so.

LOOKUP_TEXT = '''---
name: lookup
description: Use for lookups - find files, search code, list, count, or fetch one fact from files or code - when the answer is a fact to find, not a judgment to make. On Haiku, with no Write or Edit tool.
model: haiku
effort: medium
disallowedTools: Write, Edit, NotebookEdit
omitClaudeMd: true
# Written by Switchboard (Models by the job) so lookups run on Haiku. Switchboard removes it when
# you turn Models by the job off.
# If you edit this file it becomes yours, and Switchboard will leave it alone.
---

You are a read-only lookup helper. Find what you were asked for and report it.

- Use Glob, Grep and Read to find files and code. Use Bash only for commands that read, such as ls, wc, git log or git show. Never create, change, move or delete anything.
- Report the facts: file paths with line numbers, names, counts, and short excerpts where they help. Say plainly what you looked for and did not find.
- Do not judge, review or decide. If the question needs judgment, say so and stop.
- Keep the answer short. The agent that asked sees only your final message.
'''

READER_TEXT = '''---
name: reader
description: Use to read material you point it at - docs, logs, long files or web pages - and report what is in them, with sources. On Sonnet, with no Write or Edit tool.
model: sonnet
effort: medium
disallowedTools: Write, Edit, NotebookEdit
omitClaudeMd: true
# Written by Switchboard (Models by the job) so reading runs on Sonnet. Switchboard removes it when
# you turn Models by the job off.
# If you edit this file it becomes yours, and Switchboard will leave it alone.
---

You read the material you were given and report what is in it. You never change anything.

- Read every source you were pointed at: files, logs, docs or web pages. Use Read, Grep and Glob for files, WebFetch for pages, and Bash only for commands that read.
- Report what the material says, each point with its source: a file path and line, or a URL and its section. Quote exactly where the words matter, and keep quotes short.
- Keep what the material says apart from your own reading of it. Say what you could not read or find.
- Keep the answer short and in plain words. The agent that asked sees only your final message.
'''

REVIEWER_TEXT = '''---
name: reviewer
description: Use to review a change or a plan - a diff, a commit, a design - and report findings with file and line, most serious first. On Sonnet at high effort, with no Write or Edit tool. It reports; it does not fix.
model: sonnet
effort: high
disallowedTools: Write, Edit, NotebookEdit
# Written by Switchboard (Models by the job) so reviews run on Sonnet at high effort. Switchboard
# removes it when you turn Models by the job off.
# If you edit this file it becomes yours, and Switchboard will leave it alone.
---

You review a change or a plan and report what is wrong with it. You never change anything.

- Read the change in full (git diff, git show, or the files you were given) and enough of the code around it to judge it.
- Look for what would break: wrong behaviour, missed cases, this project's own rules broken, unsafe handling of data, and tests that do not test what they claim.
- Report each finding with its file and line, what is wrong, why it matters, and how sure you are. Most serious first. Then say what you checked and found sound.
- Do not fix anything, and do not pad the list: no finding is better than a weak one.
'''

VERIFIER_TEXT = '''---
name: verifier
description: Use to run something and check that it works - tests, a build, a render, a command's output - and report what passed and what failed, with the output that shows it. On Sonnet at high effort, with no Write or Edit tool. It is told never to change the code under test.
model: sonnet
effort: high
disallowedTools: Write, Edit, NotebookEdit
# Written by Switchboard (Models by the job) so checks run on Sonnet at high effort. Switchboard
# removes it when you turn Models by the job off.
# If you edit this file it becomes yours, and Switchboard will leave it alone.
---

You run things and check that they work. You report evidence, not opinions.

- Run what you were asked to check: tests, a build, a render, a script or a command. Read its output in full.
- You may run commands, and make scratch files with a command in a temporary folder. Never change the code or files under test; if something needs fixing, say what and where.
- Report what you ran (the exact command), what passed, what failed, and the lines of output that show it. Say what you could not run, and why.
- Never say something works without output that shows it.
'''

# The jobs, as the page, the command and the plan say them.
HELPERS = (
    {'file': 'switchboard-lookup.md', 'name': 'lookup', 'text': LOOKUP_TEXT,
     'says': 'a helper named lookup, on Haiku at medium effort, with no Write or Edit tool, for finding, listing '
             'and counting'},
    {'file': 'switchboard-reader.md', 'name': 'reader', 'text': READER_TEXT,
     'says': 'a helper named reader, on Sonnet at medium effort, with no Write or Edit tool, for reading docs, '
             'logs, long files and web pages'},
    {'file': 'switchboard-reviewer.md', 'name': 'reviewer', 'text': REVIEWER_TEXT,
     'says': 'a helper named reviewer, on Sonnet at high effort, with no Write or Edit tool, for reviewing a '
             'change or a plan'},
    {'file': 'switchboard-verifier.md', 'name': 'verifier', 'text': VERIFIER_TEXT,
     'says': 'a helper named verifier, on Sonnet at high effort, with no Write or Edit tool, that runs tests and '
             'commands and checks them'},
)
HELPER_BY_FILE = dict((h['file'], h) for h in HELPERS)

# One note Claude Code reads at the start of every chat, in every project (DOCS user_rules, rules_launch). Advice:
# Claude still decides (DOCS delegation). It says nothing about accounts or limits. Only what a page documents: a
# workflow script names a model per stage (DOCS workflows); an effort can be named when Claude starts a helper
# (DOCS effort_param), not documented for a workflow stage.
NOTE_TEXT = '''<!-- Written by Switchboard (Models by the job). Switchboard removes it when you turn
Models by the job off. If you edit this file it becomes yours, and Switchboard will leave it alone. -->
# Helpers by the job

Four helpers each run on the model that fits their job. Hand a step to the one that fits:

- `lookup` (Haiku): find, list, count, or look up a fact in files or code.
- `reader` (Sonnet): read docs, logs, long files or web pages and report what they say, with sources.
- `reviewer` (Sonnet, high effort): review a change or a plan; findings with file and line.
- `verifier` (Sonnet, high effort): run tests, a build, a render or a command and report what passed.

Building, fixing and deciding are not jobs for these four. Keep them in this chat, or hand them to a helper that
names no model.
A helper with one of these names that a project or the command line defines takes the place of the one above there.

In a workflow script, name a model for each step: `sonnet` for review, check and reading steps, `haiku` for
lookups, and none for steps that build.
When you start a helper yourself and name an effort, use `high` for review and checks and `medium` for reading.

When a helper's answer looks wrong, do that step again in this chat.
'''

# ---------------------------------------------------------------- presets, as data

# Each preset is the set of things Switchboard wants in place, made of parts. Anything Switchboard owns that a
# preset does not want is taken out. 'settings' maps (section, variable) to a value; 'agents' maps a file name in
# the user agents folder to its text, 'rules' a file name in the user rules folder.
#
# The two switches are two parts, each on or off by itself: 'helpers' (Models by the job) and 'sonnet' (Sonnet for
# helpers). plan(..., only=part) moves that one part to what the preset says and leaves everything of the other
# part as it is, so turning one switch never changes a file or a key of the other. 'by-the-job' is the earlier
# preset's id: Sonnet for helpers plus an Explore helper on Haiku (lookups=False leaves the Explore out, which is
# how the page used it); a record written before the two switches names it.
SONNET_SETTINGS = {('env', SUBAGENT_VAR): 'sonnet', ('env', SUBAGENT_EFFORT_VAR): SONNET_EFFORT}
HELPER_AGENTS = dict((h['file'], h['text']) for h in HELPERS)
PARTS = ('helpers', 'sonnet')
PART_NAMES = {'helpers': 'Models by the job', 'sonnet': 'Sonnet for helpers'}
PRESETS = (
    {'id': 'off', 'name': 'Off', 'default': False, 'parts': (),
     'says': "Nothing of Switchboard's left behind: its settings, its helper files and its note are taken out.",
     'settings': {}, 'agents': {}, 'rules': {}},
    {'id': 'helpers', 'name': 'Models by the job', 'default': True, 'parts': ('helpers',),
     'says': ('Four helpers, each on the model that fits its job: lookups on Haiku; reading, review and checks on '
              'Sonnet. A short note says which is for what. Your chat keeps its model.'),
     'settings': {}, 'agents': HELPER_AGENTS, 'rules': {NOTE_FILE: NOTE_TEXT}},
    {'id': 'sonnet', 'name': 'Sonnet for helpers', 'default': False, 'parts': ('sonnet',),
     'says': ("Helpers and workflow agents that name no model run on Sonnet at high effort instead of your chat's "
              'model. Helpers that name a model keep it. Your chat keeps its model.'),
     'settings': SONNET_SETTINGS, 'agents': {}, 'rules': {}},
    {'id': 'helpers-and-sonnet', 'name': 'Models by the job and Sonnet for helpers', 'default': False,
     'parts': ('helpers', 'sonnet'), 'says': 'Both: the four helpers and their note, and Sonnet for the rest.',
     'settings': SONNET_SETTINGS, 'agents': HELPER_AGENTS, 'rules': {NOTE_FILE: NOTE_TEXT}},
    {'id': 'by-the-job', 'name': 'By the job', 'default': False, 'parts': ('sonnet', 'explore'),
     'says': ('Helpers and workflow agents that name no model run on Sonnet instead of your chat\'s model; '
              'lookups (Explore) run on Haiku. Helpers that name a model keep it. Your chat keeps its model.'),
     'settings': SONNET_SETTINGS, 'agents': {EXPLORE_FILE: EXPLORE_TEXT}, 'rules': {}},
)
# Which part each thing Switchboard can own belongs to: (kind, name) -> part.
PART_OF = dict([(('settings', 'env.' + k[1]), 'sonnet') for k in SONNET_SETTINGS] +
               [(('agents', fn), 'helpers') for fn in HELPER_AGENTS] +
               [(('rules', NOTE_FILE), 'helpers'), (('agents', EXPLORE_FILE), 'explore')])
FILE_KINDS = (('agents', AGENTS_DIR), ('rules', RULES_DIR))


def preset_with(parts):
    """The id of the preset made of exactly these switches ('helpers', 'sonnet'): 'off' for none."""
    want = set(parts)
    for p in PRESETS:
        if set(p['parts']) == want and p['id'] != 'by-the-job':
            return p['id']
    raise Refused('There is no preset made of %s.' % ', '.join(sorted(want)))
NOT_OFFERED = {
    'everything-small': ('"Everything small" is not offered. The only documented switch that moves every helper '
                         'is CLAUDE_CODE_SUBAGENT_MODEL plus CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1: two variables, and '
                         '"%s" (%s). That overrides helpers that name their own model, which Switchboard never '
                         'does.' % doc('force')),
}


def preset(pid):
    for p in PRESETS:
        if p['id'] == pid:
            return p
    if pid in NOT_OFFERED:
        raise Refused(NOT_OFFERED[pid])
    raise Refused('There is no preset called "%s".' % pid)


def _skey(section, var):
    return '%s.%s' % (section, var)


# ---------------------------------------------------------------- Switchboard's own record

def _blank_state():
    return {'version': STATE_VERSION, 'dirs': {}}


def _load_state(path):
    """(state, raw bytes). A record that cannot be read raises Refused."""
    raw = _read(path)
    if raw is None:
        return _blank_state(), None
    try:
        st = json.loads(raw.decode('utf-8'))
        if not isinstance(st, dict) or st.get('version') != STATE_VERSION or not isinstance(st.get('dirs'), dict):
            raise ValueError('unexpected shape')
    except ValueError as e:
        raise Refused("Switchboard's record of its model changes (%s) can't be read (%s). It will not change "
                      "files it can't account for." % (path, e))
    return st, raw


def _dir_state(st, cd):
    d = st['dirs'].get(cd)
    if d is None:
        d = {'preset': None, 'owned': {'settings': {}, 'agents': {}, 'rules': {}}, 'history': []}
    d['owned'].setdefault('rules', {})          # a record from before the note has no 'rules'
    return d


def _save_state(path, st):
    os.makedirs(os.path.dirname(os.path.abspath(path)), mode=0o700, exist_ok=True)
    _atomic_write(path, (json.dumps(st, indent=1, sort_keys=True) + '\n').encode('utf-8'), 0o600)


# ---------------------------------------------------------------- what decides models today

def _env_of(obj):
    env = obj.get('env') if isinstance(obj, dict) else None
    if not isinstance(env, dict):
        return {}
    return dict((k, env[k]) for k in MODEL_VARS if k in env)


def _managed(managed_dir):
    """Model keys from the file-based managed settings (managed-settings.json and managed-settings.d/*.json)."""
    out = {'files': [], 'env': {}, 'settings': {}, 'unreadable': []}
    if not managed_dir or not os.path.isdir(managed_dir):
        return out
    paths = []
    drop = os.path.join(managed_dir, 'managed-settings.d')
    if os.path.isdir(drop):
        paths += [os.path.join(drop, f) for f in sorted(os.listdir(drop)) if f.endswith('.json')]
    paths.append(os.path.join(managed_dir, 'managed-settings.json'))
    for p in paths:
        raw = _read(p)
        if raw is None:
            continue
        try:
            obj = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            out['unreadable'].append(p)
            continue
        if not isinstance(obj, dict):
            out['unreadable'].append(p)
            continue
        out['files'].append(p)
        out['env'].update(_env_of(obj))      # the main file is read last, so it wins on a tie
        for k in MODEL_KEYS:
            if k in obj:
                out['settings'][k] = obj[k]
    return out


def _version_of(exe):
    """The version of the Claude Code a `claude` executable points at, read from disk, or None."""
    real = os.path.realpath(exe)
    parts = real.split(os.sep)
    if 'versions' in parts[:-1]:                 # native installer: .../claude/versions/<version>
        v = _ver(parts[parts.index('versions') + 1])
        if v:
            return v
    d = os.path.dirname(real)
    for _ in range(4):                           # npm: .../@anthropic-ai/claude-code/bin/claude.exe
        raw = _read(os.path.join(d, 'package.json')) if os.path.isfile(os.path.join(d, 'package.json')) else None
        if raw:
            try:
                meta = json.loads(raw.decode('utf-8'))
            except (ValueError, UnicodeDecodeError):
                meta = {}
            if isinstance(meta, dict) and meta.get('name') == '@anthropic-ai/claude-code':
                return _ver(meta.get('version'))
        d = os.path.dirname(d)
    return None


def _versions(home=None, path=None, extra=None):
    """Claude Code installs a terminal can launch, found without running anything:
    [{'where', 'version', 'primary'}]. 'primary' is the one `claude` starts first on PATH.
    `extra` folders are looked in after PATH (default: where the installers put it)."""
    home = home or os.path.expanduser('~')
    dirs = (path if path is not None else os.environ.get('PATH', '')).split(os.pathsep)
    dirs += [os.path.join(home, '.local', 'bin'), os.path.join(home, '.npm-global', 'bin'),
             os.path.join(home, '.claude', 'local'), '/opt/homebrew/bin', '/usr/local/bin'] \
        if extra is None else list(extra)
    out, seen_real = [], set()
    for d in dirs:
        exe = os.path.join(d, 'claude') if d else None
        if not exe or not os.path.isfile(exe) or not os.access(exe, os.X_OK):
            continue
        real = os.path.realpath(exe)
        if real in seen_real:
            continue
        seen_real.add(real)
        v = _version_of(exe)
        if v is not None:
            out.append({'where': exe, 'version': _vs(v), 'primary': not out})
    return out


def _old(versions, need):
    """(primary install older than need or None, other installs older than need)."""
    old = [v for v in versions if (_ver(v.get('version')) or (0,)) < need]
    first = [v for v in old if v.get('primary')]
    return (first[0] if first else None), [v for v in old if not v.get('primary')]


def _is_on(value):
    return isinstance(value, str) and value.strip().lower() not in ('', '0', 'false', 'no', 'off')


def busy(claude_dir=None, within=BUSY_SECONDS, now=None):
    """How many chat or helper histories under claude_dir/projects were written in the last
    `within` seconds. A sign that a task is running; there is no documented way to know for sure."""
    root = os.path.join(_cd(claude_dir), 'projects')
    now = time.time() if now is None else now
    n = 0
    for dirpath, dirnames, filenames in os.walk(root):
        for fn in filenames:
            if fn.endswith('.jsonl'):
                try:
                    # a file dated past the clock is not "written lately" (a restore, a synced folder)
                    if now - within <= os.stat(os.path.join(dirpath, fn)).st_mtime <= now + 5:
                        n += 1
                except OSError:
                    pass
    return n


def current(claude_dir=None, state_path=None, environ=None, managed_dir=None, versions=None, check_busy=True):
    """A read-only picture of what decides models today.

    claude_dir: Claude Code's folder (default: CLAUDE_CONFIG_DIR or ~/.claude).
    environ: the environment Claude Code is started from (default: this process's).
    managed_dir: the folder of file-based managed settings (default: the platform's).
    versions: [{'where', 'version', 'primary'}] of Claude Code installs (default: looked up on disk).
    check_busy: also count histories written in the last few minutes ('busy'; see busy()).
    """
    cd = _cd(claude_dir)
    sp = state_path or default_state_path()
    environ = os.environ if environ is None else environ
    md = default_managed_dir() if managed_dir is None else managed_dir
    path = os.path.join(cd, SETTINGS_NAME)
    pic = {'claude_dir': cd, 'settings_file': path, 'settings_exists': False, 'settings_problem': None,
           'settings': {}, 'deny_agents': [], 'env': {}, 'agents': [], 'environ': {}, 'managed': {},
           'versions': [], 'ours': [], 'preset': None, 'backups': [], 'state_problem': None, 'busy': 0,
           'effort_level': None,
           'helpers_with_no_model_run': None, 'warnings': [], 'notes': []}
    warn, note = pic['warnings'].append, pic['notes'].append

    obj = {}
    raw = _read(path) if not os.path.isdir(path) else None
    if raw is not None:
        pic['settings_exists'] = True
        try:
            d = _Doc(raw, path)
            d.env()                 # a doubled or non-object "env" is a problem too
            obj = d.obj
        except Refused as e:
            pic['settings_problem'] = str(e)
            warn(str(e))
            obj = {}
    pic['settings'] = dict((k, obj[k]) for k in MODEL_KEYS if k in obj)
    deny = obj['permissions'].get('deny') if isinstance(obj.get('permissions'), dict) else None
    pic['deny_agents'] = [r for r in deny if isinstance(r, str) and r.startswith('Agent(')] \
        if isinstance(deny, list) else []

    try:
        st, _ = _load_state(sp)
    except Refused as e:
        st = _blank_state()
        pic['state_problem'] = str(e)
        warn(str(e))
    ds = _dir_state(st, cd)
    pic['preset'] = ds['preset']
    owned = ds['owned']
    pic['backups'] = [f['backup'] for h in ds['history'] for f in h['files']
                      if f.get('backup') and os.path.exists(f['backup'])]

    env = _env_of(obj)
    for var, value in sorted(env.items()):
        mine = owned['settings'].get(_skey('env', var))
        pic['env'][var] = {'value': value,
                           'set_by': 'switchboard' if mine and mine.get('value') == value else 'you'}
    for a in _agents(cd):
        mine = owned['agents'].get(os.path.basename(a['path'])) if os.path.dirname(a['file']) == AGENTS_DIR \
            else None
        a['set_by'] = ('switchboard' if mine and mine.get('sha256') == a['sha256'] and not os.path.islink(a['path'])
                       else 'you')                  # a file that became a link is yours (a dotfiles tool, say)
        del a['sha256']
        pic['agents'].append(a)
    for key, info in sorted(owned['settings'].items()):
        sec, var = key.split('.', 1)
        if sec == 'env' and pic['env'].get(var, {}).get('set_by') == 'switchboard':
            pic['ours'].append({'what': 'setting', 'key': key, 'value': info['value'], 'file': path})
    for kind, folder in FILE_KINDS:
        for fn in sorted(owned[kind]):
            p = os.path.join(cd, folder, fn)
            if not os.path.islink(p) and _sha(_read(p)) == owned[kind][fn].get('sha256'):
                pic['ours'].append({'what': 'file', 'file': p})
    # Which of Switchboard's things are in place, by switch (PART_OF): what the page shows as on, and what Off
    # for that switch takes out.
    pic['parts'] = dict((p, []) for p in PARTS + ('explore',))
    for o in pic['ours']:
        if o['what'] == 'setting':
            part, name = PART_OF.get(('settings', o['key'])), o['key']
        else:
            kind = 'rules' if os.path.dirname(o['file']) == os.path.join(cd, RULES_DIR) else 'agents'
            part, name = PART_OF.get((kind, os.path.basename(o['file']))), os.path.basename(o['file'])
        if part:
            pic['parts'][part].append(name)
    pic['parts']['sonnet'].sort(key=lambda k: (k != 'env.' + SUBAGENT_VAR, k))     # the model line first

    pic['environ'] = dict((k, environ[k]) for k in MODEL_VARS if k in environ)
    pic['managed'] = _managed(md)
    pic['versions'] = _versions() if versions is None else list(versions)
    pic['busy'] = busy(cd) if check_busy else None

    # Plain lines about anything that overrides the rest or makes a preset pointless.
    where = []      # (where, env dict), highest precedence first
    if pic['managed']['env']:
        where.append(('managed settings', pic['managed']['env']))
    where.append(('your user settings', env))
    where.append(('the environment Claude Code starts from', pic['environ']))
    force = None
    for w, e in where:
        if _is_on(e.get(FORCE_VAR)):
            force = w
            warn('%s is on in %s: every helper, teammate and workflow agent runs one model and their own model '
                 'lines are ignored ("%s" %s). "By the job" would then override helpers that name a model, so '
                 'Switchboard will not apply it while this is on.' % ((FORCE_VAR, w) + doc('force')))
            break
    pic['force'] = force
    for w, e in where:
        if SUBAGENT_VAR in e and not (w == 'your user settings' and pic['env'][SUBAGENT_VAR]['set_by'] == 'switchboard'):
            v = e[SUBAGENT_VAR]
            if (v or '').strip() in ('', 'inherit'):
                note('%s is set to "%s" in %s, which is the same as leaving it unset.' % (SUBAGENT_VAR, v, w))
            else:
                note('%s=%s in %s: helpers that name no model run %s. Switchboard leaves your value; "By the job" '
                     'would only add the Explore helper.' % (SUBAGENT_VAR, v, w, v))
            break
    for w, e in where:
        if SUBAGENT_EFFORT_VAR in e and not (w == 'your user settings' and
                                             pic['env'][SUBAGENT_EFFORT_VAR]['set_by'] == 'switchboard'):
            note('%s=%s in %s: helpers that name no effort run at %s. Switchboard leaves your value; Sonnet for '
                 'helpers would only add its model line ("%s" %s).'
                 % ((SUBAGENT_EFFORT_VAR, e[SUBAGENT_EFFORT_VAR], w, e[SUBAGENT_EFFORT_VAR]) + doc('effort_order')))
            break
    pic['effort_level'] = None
    for w, e in where:
        if e.get(EFFORT_VAR):
            note('%s=%s in %s sets the effort of every model and beats any effort line in a helper ("%s" %s).'
                 % ((EFFORT_VAR, e[EFFORT_VAR], w) + doc('effort_var')))
            pic['effort_level'] = {'value': e[EFFORT_VAR], 'where': w}     # what both switches' rows say
            break
    if pic['environ'].get('ANTHROPIC_MODEL'):
        note('ANTHROPIC_MODEL=%s in the environment Claude Code starts from: sessions started there run it '
             'instead of the model setting.' % pic['environ']['ANTHROPIC_MODEL'])
    for w, e in where:
        if _is_on(e.get(NO_EXPLORE_PLAN_VAR)):
            note('%s is on in %s: the built-in Explore and Plan are off, so Switchboard will not add an Explore '
                 'helper ("%s" %s).' % ((NO_EXPLORE_PLAN_VAR, w) + doc('disable_explore')))
            break
    for fam in ('SONNET', 'HAIKU'):
        var = 'ANTHROPIC_DEFAULT_%s_MODEL' % fam
        for w, e in where:
            if e.get(var):
                note('%s=%s in %s: the %s alias runs that model.' % (var, e[var], w, fam.lower()))
                break
    model = pic['managed']['settings'].get('model') or obj.get('model')
    if isinstance(model, str) and 'haiku' in model.lower():
        warn('Your default model is %s. With "By the job", helpers that name no model would run Sonnet, a larger '
             'model than your chat\'s.' % model)
    allow = pic['managed']['settings'].get('availableModels')
    if isinstance(allow, list) and allow and not any(isinstance(x, str) and 'sonnet' in x.lower() for x in allow):
        warn('Your organization\'s availableModels list has no Sonnet model, so a helper asked for Sonnet falls back '
             'to another model and "By the job" would do nothing for it.')
    if pic['managed']['unreadable']:
        warn('Managed settings that cannot be read: %s.' % ', '.join(pic['managed']['unreadable']))
    if 'Agent(Explore)' in pic['deny_agents']:
        note('Your settings deny the Explore helper, so Switchboard will not add one ("%s" %s).'
             % doc('deny_agent'))
    for a in pic['agents']:
        if (a['name'] or '').strip().lower() == 'explore' and a['set_by'] == 'you':
            note('You have your own Explore helper (%s, model: %s). It wins; Switchboard will not add one.'
                 % (a['file'], a['model'] or 'not set'))
    first, others = _old(pic['versions'], NEEDS_ORDER)
    for v in ([first] if first else []) + others:
        warn('Claude Code %s at %s%s is older than %s; there CLAUDE_CODE_SUBAGENT_MODEL overrides helpers that name '
             'a model ("%s" %s).%s' % ((v['version'], v['where'], ' (what `claude` starts)' if v['primary'] else '',
                                       _vs(NEEDS_ORDER)) + doc('order_before') +
                                      ('' if v['primary'] else ' Sessions started with it would break the rule, so '
                                       'update or remove it.',)))
    if not pic['versions']:
        note('Switchboard could not find the installed Claude Code version. "By the job" needs v%s or later.'
             % _vs(NEEDS_ORDER))
    note('Switchboard sees only your user settings and file-based managed settings. A project\'s '
         '.claude/settings.json, server-managed settings or an MDM profile can set the same keys and win '
         '("%s" %s).' % doc('env_levels'))

    sub = None
    for w, e in where:
        if SUBAGENT_VAR in e and (e[SUBAGENT_VAR] or '').strip() not in ('', 'inherit'):
            sub = (e[SUBAGENT_VAR], w)
            break
    chat = "your chat's model (%s)" % (model or 'your account default')
    if force:
        pic['helpers_with_no_model_run'] = ('every helper runs %s, named or not (FORCE is on)'
                                            % ('%s (%s in %s)' % (sub[0], SUBAGENT_VAR, sub[1]) if sub else chat))
    else:
        pic['helpers_with_no_model_run'] = '%s (%s in %s)' % (sub[0], SUBAGENT_VAR, sub[1]) if sub else chat
    return pic


# ---------------------------------------------------------------- plan: what would change

def plan(preset_id, claude_dir=None, state_path=None, environ=None, managed_dir=None, versions=None,
         lookups=True, only=None):
    """The exact edits that preset_id would make, without making them.

    Returns {'preset', 'name', 'claude_dir', 'state_path', 'made_at', 'blocked', 'edits', 'forget',
    'skipped', 'notes', 'fingerprints', 'state_sha', 'only'}. Each edit is {'file', 'what' ('setting' or 'file'),
    'kind' (files: 'agents' or 'rules'), 'key', 'line', 'old', 'new', 'op', 'says'}; 'old'/'new' are None where
    nothing is or will be. lookups=False leaves out the Explore helper. only='helpers' or 'sonnet' moves that one
    switch to what the preset says and leaves everything of the other as it is (PART_OF). Nothing here writes.
    """
    p = preset(preset_id)
    cd = _cd(claude_dir)
    sp = state_path or default_state_path()
    environ = os.environ if environ is None else environ
    md = default_managed_dir() if managed_dir is None else managed_dir
    out = {'preset': p['id'], 'name': p['name'], 'claude_dir': cd, 'state_path': sp, 'made_at': time.time(),
           'blocked': None, 'edits': [], 'forget': [], 'skipped': [], 'notes': [], 'fingerprints': {},
           'state_sha': None, 'only': only}
    skip = lambda what, why: out['skipped'].append({'what': what, 'why': why})   # noqa: E731
    in_scope = lambda kind, name: only is None or PART_OF.get((kind, name)) == only     # noqa: E731
    # Does this plan put anything in, and for which switch? Off, and Off for one switch, never needs more than
    # Switchboard's own record. Each switch is blocked for its own reasons, in its own words.
    adding = set(p['parts']) if only is None else set(p['parts']) & {only}
    adds = bool(adding)
    sonnet_adds = bool(adding & {'sonnet', 'explore'})
    helpers_adds = 'helpers' in adding

    if not os.path.isdir(cd):
        out['blocked'] = 'There is no Claude Code folder at %s.' % cd
        return out
    try:
        st, raw_state = _load_state(sp)
    except Refused as e:
        out['blocked'] = str(e)
        return out
    out['state_sha'] = _sha(raw_state)
    owned = _dir_state(st, cd)['owned']
    pic = current(cd, sp, environ, md, versions, check_busy=False)

    managed_env = pic['managed']['env']
    if pic['force'] and adds:
        out['blocked'] = ('%s is on in %s, so every helper runs one model and their own model lines are ignored '
                          '("%s" %s). Turn it off first; Switchboard never overrides a helper that names its model.'
                          % ((FORCE_VAR, pic['force']) + doc('force')))
        return out
    first, others = _old(pic['versions'], NEEDS_ORDER)
    if first and sonnet_adds:
        out['blocked'] = ('Claude Code %s (%s, what `claude` starts) is older than %s. There, '
                          'CLAUDE_CODE_SUBAGENT_MODEL overrides helpers that name a model ("%s" %s). Update Claude '
                          'Code first.' % ((first['version'], first['where'], _vs(NEEDS_ORDER)) + doc('order_before')))
        return out
    first_h, others_h = _old(pic['versions'], NEEDS_HELPERS)
    if first_h and helpers_adds:
        out['blocked'] = ('Claude Code %s (%s, what `claude` starts) is older than %s, which the helper files\' '
                          '`omitClaudeMd` field needs ("%s" %s). Update Claude Code first.'
                          % ((first_h['version'], first_h['where'], _vs(NEEDS_HELPERS)) + doc('omit_claude_md')))
        return out
    if sonnet_adds:
        for v in others:
            out['notes'].append('An older Claude Code (%s at %s) is also installed. A session started with it would '
                                'let this setting override helpers that name a model; update or remove it.'
                                % (v['version'], v['where']))
        if not pic['versions']:
            out['notes'].append('Switchboard could not find the installed Claude Code version; this preset needs '
                                'v%s or later.' % _vs(NEEDS_ORDER))
    if helpers_adds:
        for v in others_h:
            out['notes'].append('An older Claude Code (%s at %s) is also installed. A session started with it may not '
                                'read every field of the helper files (they need %s).'
                                % (v['version'], v['where'], _vs(NEEDS_HELPERS)))
        if not pic['versions']:
            out['notes'].append('Switchboard could not find the installed Claude Code version; Models by the job needs '
                                'v%s or later.' % _vs(NEEDS_HELPERS))

    # ---- the settings file
    spath = os.path.join(cd, SETTINGS_NAME)
    sraw = None
    sdoc = None
    sbad = False                # it can't be understood: deny rules and env are not known
    if os.path.islink(spath):
        sproblem = ('%s is a link to %s. Switchboard does not replace linked files; edit the target yourself.'
                    % (spath, os.path.realpath(spath)))
    elif os.path.isdir(spath):
        sproblem = '%s is a folder, not a file.' % spath
    else:
        sproblem = None
        sraw = _read(spath)
        try:
            sdoc = _Doc(sraw, spath)
        except Refused as e:
            sproblem = str(e)
            sbad = True
        if sproblem is None and sraw is not None and not os.access(spath, os.W_OK):
            sproblem = '%s is set read-only, so Switchboard leaves it alone.' % spath
    cur_env = {}
    if sdoc is not None:
        try:
            _, env_members = sdoc.env()
            cur_env = dict((m['key'], m['value']) for m in env_members)
            for var in set(k[1] for k in p['settings']) | set(k.split('.', 1)[1] for k in owned['settings']):
                sdoc.member(var, env_members)        # refuses a doubled key
        except Refused as e:
            sproblem = str(e)
            sdoc = None
            sbad = True
    if sproblem and sraw is not None and sonnet_adds:
        # Unreadable settings hide what the user has set (deny rules, env), so nothing is added.
        out['blocked'] = sproblem
        return out
    if sbad and helpers_adds:
        # A settings file that can't be understood may deny one of the helpers. One that is read-only or a link is
        # read all the same (current() reads through a link), and Models by the job writes no setting.
        out['blocked'] = HELPERS_UNREADABLE_WHY
        return out

    MISSING = object()
    model_key, effort_key = _skey('env', SUBAGENT_VAR), _skey('env', SUBAGENT_EFFORT_VAR)
    wanted = dict((_skey(*k), v) for k, v in p['settings'].items())
    ours_after = set()          # keys that are Switchboard's and in place once this plan is applied
    # The model line first: the effort line goes in only with it (and it reads best in that order).
    for key in sorted((k for k in set(wanted) | set(owned['settings']) if in_scope('settings', k)),
                      key=lambda k: (k != model_key, k)):
        sec, var = key.split('.', 1)
        mine = owned['settings'].get(key)
        cur = cur_env.get(var, MISSING) if sdoc is not None else MISSING
        want = wanted.get(key, MISSING)
        if sproblem and (want is not MISSING or mine):
            skip(key, sproblem)
            continue
        if mine is not None and cur is not MISSING and cur != mine['value']:
            out['forget'].append({'key': key, 'file': spath,
                                  'says': 'You changed %s to %s since Switchboard set it; it is yours now and stays '
                                          'as it is.' % (var, _jdump(cur))})
            mine = None
            if want is not MISSING:
                skip(key, 'Your value %s wins; Switchboard does not change keys it did not set.' % _jdump(cur))
            continue
        if want is MISSING:
            if mine is None:
                continue
            if cur is MISSING:
                out['forget'].append({'key': key, 'file': spath,
                                      'says': '%s is already gone from your settings.' % var})
                continue
            out['edits'].append({
                'file': spath, 'what': 'setting', 'key': key, 'line': None, 'old': cur, 'new': None, 'op': 'remove',
                'says': ('Takes out %s=%s, which Switchboard added. Helpers that name no %s go back to %s in sessions '
                         'you start after this; running sessions keep it until restarted.'
                         % (var, cur, 'effort' if var == SUBAGENT_EFFORT_VAR else 'model',
                            "the chat's effort" if var == SUBAGENT_EFFORT_VAR else "your chat's model")),
                'made_env': bool(mine.get('made_env')), 'made_file': bool(mine.get('made_file'))})
            continue
        if mine is not None and cur == want:
            ours_after.add(key)
            continue        # already in place
        if mine is None and cur is not MISSING:
            skip(key, 'You already set %s=%s in %s. Yours wins; Switchboard does not change keys it did not set.'
                 % (var, _jdump(cur), spath))
            continue
        if var in managed_env:
            skip(key, 'Managed settings set %s=%s, which beats your user settings ("%s" %s).'
                 % ((var, managed_env[var]) + doc('env_levels')))
            continue
        if var in pic['environ']:
            skip(key, '%s=%s is set in the environment Claude Code starts from. A value in settings would replace '
                      'it ("%s" %s), so Switchboard leaves yours.' % ((var, pic['environ'][var]) + doc('env_shell')))
            continue
        if key == effort_key:
            if model_key in wanted and model_key not in ours_after:
                skip(key, 'It goes with Switchboard\'s %s line, which is not going in.' % SUBAGENT_VAR)
                continue
            low, _ = _old(pic['versions'], NEEDS_EFFORT)
            if low:
                skip(key, 'Claude Code %s (%s) is older than %s, so only the model line goes in. The high effort '
                          'part needs %s or newer: the changelog adds the setting there ("%s" %s), the settings '
                          'page says %s ("%s" %s).'
                     % ((low['version'], low['where'], _vs(NEEDS_EFFORT), _vs(NEEDS_EFFORT))
                        + doc('subagent_effort_added') + ('2.1.315',) + doc('subagent_effort')))
                continue
        op = 'change' if mine is not None and cur is not MISSING else 'add'
        if var == SUBAGENT_VAR:
            says = ('Adds %s=%s to "env" in your user settings: helpers and workflow agents that name no model run '
                    'on %s instead of your chat\'s model. Helpers that name a model keep it; forks, Plan and the '
                    'built-in Explore are not moved by this line.' % (var, want, want.capitalize()))
        elif var == SUBAGENT_EFFORT_VAR:
            says = ('Adds %s=%s to "env" in your user settings: helpers and workflow agents that name no effort run '
                    'at %s effort. A helper\'s own effort line comes first, and CLAUDE_CODE_EFFORT_LEVEL before '
                    'both; forks, forked skills and teammates are not moved by this line.' % (var, want, want))
        else:
            says = 'Sets %s=%s.' % (var, want)
        out['edits'].append({
            'file': spath, 'what': 'setting', 'key': key, 'line': None,
            'old': None if cur is MISSING else cur, 'new': want, 'op': op, 'says': says,
            'made_env': sdoc.member('env') is None, 'made_file': sraw is None})
        ours_after.add(key)
    removes = [e for e in out['edits'] if e['op'] == 'remove']
    for e in removes:           # the env object (or the file) Switchboard made goes once all its lines go
        e['made_env'] = any(x['made_env'] for x in removes)
        e['made_file'] = any(x['made_file'] for x in removes)
    if any(e['what'] == 'setting' for e in out['edits']):
        out['fingerprints'][spath] = _sha(sraw)
    if any(e['key'] == effort_key and e['op'] == 'add' for e in out['edits']):
        _, older = _old(pic['versions'], NEEDS_EFFORT)
        for v in older:
            out['notes'].append('An older Claude Code (%s at %s) is also installed. It does not read %s (that needs '
                                '%s), so a session started with it runs helpers at the chat\'s effort.'
                                % (v['version'], v['where'], SUBAGENT_EFFORT_VAR, _vs(NEEDS_EFFORT)))
        if not pic['versions']:
            out['notes'].append('Switchboard could not find the installed Claude Code version. %s needs %s or newer '
                                '("%s" %s); an older Claude Code does not read it.'
                                % ((SUBAGENT_EFFORT_VAR, _vs(NEEDS_EFFORT)) + doc('subagent_effort_added')))

    # ---- the agents and rules folders
    managed_agents = _agents(os.path.join(md, '.claude')) if md and os.path.isdir(md) else []
    helpers_after = set()       # Switchboard's helper files that are in place once this plan is applied
    for kind, folder in FILE_KINDS:
        fdir = os.path.join(cd, folder)
        want_files = dict(p[kind])
        if kind == 'agents' and not lookups and EXPLORE_FILE in want_files:
            del want_files[EXPLORE_FILE]
            skip(os.path.join(fdir, EXPLORE_FILE), 'Left out on request (lookups=False).')
        for fn in sorted(f for f in set(want_files) | set(owned[kind]) if in_scope(kind, f)):
            fpath = os.path.join(fdir, fn)
            mine = owned[kind].get(fn)
            helper = HELPER_BY_FILE.get(fn) if kind == 'agents' else None
            if os.path.islink(fpath) or os.path.isdir(fpath):
                if mine is not None:            # it became one since Switchboard wrote it: yours now, as if edited
                    out['forget'].append({'key': None, 'file': fpath, 'kind': kind,
                                          'says': '%s is a link or a folder now; it is yours and stays.' % fpath})
                skip(fpath, '%s is a link or a folder; Switchboard leaves it alone.' % fpath)
                continue
            craw = _read(fpath)
            csha = _sha(craw)
            want = want_files.get(fn)
            if mine is not None and craw is not None and csha != mine['sha256']:
                out['forget'].append({'key': None, 'file': fpath, 'kind': kind,
                                      'says': 'You edited %s since Switchboard wrote it; it is yours now and stays.'
                                              % fpath})
                mine = None
                if want is not None:
                    skip(fpath, 'You edited it; Switchboard leaves your version.')
                continue
            if want is None:
                if mine is None:
                    continue
                if craw is None:
                    out['forget'].append({'key': None, 'file': fpath, 'kind': kind,
                                          'says': '%s is already gone.' % fpath})
                    continue
                out['edits'].append(_file_out(kind, fn, fpath, helper, None, mine))
                out['fingerprints'][fpath] = csha
                continue
            wsha = _sha(want.encode('utf-8'))
            if fn == EXPLORE_FILE:
                why = _explore_blocked(pic, fdir)
            elif helper:
                why = _helper_blocked(pic, helper, fdir, managed_agents)
            else:
                why = (None if helpers_after else
                       "No helper of Switchboard's is in place, so the note that names them stays out.")
                if why is None and os.path.exists(fdir) and not os.path.isdir(fdir):
                    why = '%s is not a folder.' % fdir
            if mine is not None and craw is not None and why:
                # Ours is in place but no longer should be: the user added one of their own with the same name
                # (two with one name in one folder: only one loads), or turned it off. Theirs wins.
                out['edits'].append(_file_out(kind, fn, fpath, helper, why, mine))
                out['fingerprints'][fpath] = csha
                continue
            if mine is not None and csha == wsha:
                if helper:
                    helpers_after.add(fn)
                continue
            if mine is None and craw is not None:
                skip(fpath, 'A file with this name is already there and is not Switchboard\'s.')
                continue
            if why:
                skip(fpath, why)
                continue
            made_dir = not os.path.isdir(fdir)
            if fn == EXPLORE_FILE:
                says = ('Adds %s: a read-only Explore helper of Switchboard\'s that runs on Haiku in place of the '
                        'built-in Explore. Its instructions are Switchboard\'s (the built-in ones are not published); '
                        'it reads no CLAUDE.md, like the built-in, but it does get the git status snapshot the '
                        'built-in skips.' % fpath)
            elif helper:
                says = 'Adds %s: %s. Claude picks it by its description.' % (fpath, helper['says'])
                helpers_after.add(fn)
            else:
                says = ('Adds %s: a short note Claude Code reads at the start of each chat, in every project, saying '
                        'which helper is for what. It is advice; Claude still decides. Chats already open read it '
                        'when they start again.' % fpath)
            out['edits'].append({'file': fpath, 'what': 'file', 'kind': kind, 'key': None,
                                 'line': _model_line(want), 'old': None, 'new': _model_line(want),
                                 'op': 'replace' if (mine is not None and craw is not None) else 'create',
                                 'says': says, 'made_dir': made_dir})
            out['fingerprints'][fpath] = csha

    # The two limits of an effort line, where it goes in: CLAUDE_CODE_EFFORT_LEVEL comes first, and the effort
    # variable does not reach forks, forked skills or teammates.
    effort_in = any(e['key'] == effort_key and e['op'] == 'add' for e in out['edits'])
    if effort_in or any(e.get('kind') == 'agents' and e['op'] != 'delete' and _helper_effort(e)
                        for e in out['edits']):
        lvl = pic['effort_level']
        if lvl:
            out['notes'].append('%s=%s in %s comes first: every helper runs at %s, whatever its own effort line or '
                                '%s says ("%s" %s).' % ((EFFORT_VAR, lvl['value'], lvl['where'], lvl['value'],
                                                         SUBAGENT_EFFORT_VAR) + doc('effort_var')))
    if effort_in:
        out['notes'].append('%s does not reach forks, forked skills or teammates ("%s" %s).'
                            % ((SUBAGENT_EFFORT_VAR,) + doc('subagent_effort')))
    if any(e['what'] == 'setting' and e['op'] != 'remove' for e in out['edits']):
        out['notes'].append('Claude Code applies a saved env change to running sessions: helpers they start after '
                            'this use it ("%s" %s). Helpers already running keep theirs.' % doc('env_running'))
    if any(e.get('kind') == 'agents' and e['op'] != 'delete' for e in out['edits']):
        out['notes'].append('Claude Code picks up a new helper file within seconds, with no restart ("%s" %s).'
                            % doc('agents_watch'))
    if any(e['op'] == 'remove' for e in out['edits']):
        out['notes'].append('Sessions already running keep what was taken out of "env" until they are restarted '
                            '("%s" %s).' % doc('env_running'))
    if not adds and pic['backups']:
        out['notes'].append('Switchboard keeps its %d backup copies (%s) so this can be undone. Claude Code does not '
                            'read them.' % (len(pic['backups']), ', '.join(pic['backups'])))
    return out


def _helper_effort(edit):
    """The effort line of the helper file an edit puts in, or None."""
    h = HELPER_BY_FILE.get(os.path.basename(edit['file']))
    return (_frontmatter(h['text']) or {}).get('effort') if h else None


def _model_line(text):
    """'model: haiku' or 'model: sonnet, effort: high': the lines of a helper file the plan names; None for a note."""
    fm = _frontmatter(text) or {}
    if not fm.get('model'):
        return None
    return 'model: %s' % fm['model'] + (', effort: %s' % fm['effort'] if fm.get('effort') else '')


def _file_out(kind, fn, fpath, helper, why, mine):
    """The edit that takes one of Switchboard's files out, and why."""
    if fn == EXPLORE_FILE:
        says = ("Takes out Switchboard's Explore helper. " + why if why else
                "Deletes Switchboard's Explore helper; the built-in Explore (your chat's model) comes back. Sessions "
                'pick this up within seconds.')
        line = 'model: haiku'
    elif helper:
        says = "Takes out Switchboard's helper named %s." % helper['name'] + (' ' + why if why else '')
        line = _model_line(helper['text'])
    else:
        says = "Takes out Switchboard's note on which helper is for what." + (' ' + why if why else '')
        line = None
    return {'file': fpath, 'what': 'file', 'kind': kind, 'key': None, 'line': line, 'old': line, 'new': None,
            'op': 'delete', 'says': says, 'made_dir': bool(mine.get('made_dir'))}


def _helper_blocked(pic, h, adir, managed_agents):
    """Why Switchboard must not add one of its four helpers, or None. A helper of yours with the same name is never
    replaced: two files with one name in one folder load only one of them."""
    name = h['name']
    for a in pic['agents']:
        if name in a['takes'] and a['set_by'] == 'you' and a['path'] != os.path.join(adir, h['file']):
            return ('You have your own helper named %s (%s). Yours stays, so Switchboard leaves its own %s out ("%s" '
                    '%s).' % ((name, a['file'], name) + doc('same_name_one_dir')))
    for a in managed_agents:
        if name in a['takes']:
            return ('Your organization\'s managed settings define a helper named %s (%s), which wins ("%s" %s).'
                    % ((name, a['path']) + doc('managed_agents')))
    if 'Agent(%s)' % name in pic['deny_agents']:
        return 'Your settings deny Agent(%s) ("%s" %s).' % ((name,) + doc('deny_agent'))
    if os.path.exists(adir) and not os.path.isdir(adir):
        return '%s is not a folder.' % adir
    return None


def _explore_blocked(pic, adir):
    """Why Switchboard must not add its Explore helper, or None."""
    for a in pic['agents']:
        if (a['name'] or '').strip().lower() == 'explore' and a['set_by'] == 'you':
            return ('You have your own Explore helper (%s). Yours wins ("%s" %s).'
                    % ((a['file'],) + doc('agents_priority')))
    for e in (pic['managed']['env'], dict((k, v['value']) for k, v in pic['env'].items()), pic['environ']):
        if _is_on(e.get(NO_EXPLORE_PLAN_VAR)):
            return ('%s is on: you turned the built-in Explore off, and a custom Explore would bring one back '
                    '("%s" %s).' % ((NO_EXPLORE_PLAN_VAR,) + doc('disable_explore')))
    if 'Agent(Explore)' in pic['deny_agents']:
        return 'Your settings deny Agent(Explore) ("%s" %s).' % doc('deny_agent')
    low, _ = _old(pic['versions'], NEEDS_OMIT)
    if low:
        return ('Claude Code %s (%s) is older than %s, which reads omitClaudeMd; the helper would load your '
                'CLAUDE.md files, unlike the built-in.' % (low['version'], low['where'], _vs(NEEDS_OMIT)))
    if os.path.exists(adir) and not os.path.isdir(adir):
        return '%s is not a folder.' % adir
    return None


# ---------------------------------------------------------------- apply and undo

def _stamp():
    return time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())


def _backup_next_to(path, data, mode):
    """The one copy of the settings file from before Switchboard's last change, beside it, named so Claude Code
    never reads it as settings: replaced each time (never piling up) and readable only by you (it can hold keys
    in `env`). The status line keeps the same single copy. `mode` is the file's own, and is not used for it."""
    b = '%s.switchboard-backup' % path
    _atomic_write(b, data, 0o600)
    return b


def _backup_in_state(state_path, name, data):
    """A copy kept in Switchboard's folder. Used for agent files: Claude Code scans the agents folder
    recursively, so a copy left there could load as a second Explore."""
    d = os.path.join(os.path.dirname(os.path.abspath(state_path)), 'models-backups')
    os.makedirs(d, mode=0o700, exist_ok=True)
    b = os.path.join(d, '%s.%s.%s.bak' % (name, _stamp(), _sha(data)[:12]))
    _atomic_write(b, data, 0o600)
    return b


def _settings_after(edits, raw, path):
    """The settings file's new bytes after the plan's setting edits, or None to delete the file."""
    doc_ = _Doc(raw, path)
    expect = json.loads(json.dumps(doc_.obj))
    text = doc_.text
    delete = False
    for e in edits:
        sec, var = e['key'].split('.', 1)
        if e['op'] in ('add', 'change'):
            text = _env_set(doc_, var, e['new'])[0]
            expect.setdefault('env', {})[var] = e['new']
        else:
            text = _env_del(doc_, var, e.get('made_env'))
            env = expect.get('env', {})
            env.pop(var, None)
            if not env and e.get('made_env'):
                expect.pop('env', None)
            if e.get('made_file') and not expect:
                delete = True
        _checked(doc_, text, expect)
        doc_.text = text
        doc_.parse()
    if delete:
        return None
    return text.encode('utf-8')


def _cant_write(path, writes):
    """Why Switchboard must not write (or, `writes` False, take away) `path` now, or None: it became a link (a dotfiles
    tool's, say: writing would replace the link with a plain file, and the linked file would keep the lines), or it is
    set read-only and would be written."""
    if os.path.islink(path):
        return "%s is a link now, and Switchboard doesn't write through links. Nothing was changed." % path
    if writes and os.path.isfile(path) and not os.access(path, os.W_OK):
        return '%s is set read-only, so Switchboard leaves it alone. Nothing was changed.' % path
    return None


def _last_look(path, before, writes):
    """Refuses (Refused) when `path` is not what the plan read any more, or became a link, or is set read-only and
    would be written (`writes`)."""
    if _sha(_read(path)) != _sha(before):
        raise Refused('%s changed while Switchboard was writing. Nothing of it was changed; make a new plan.' % path)
    why = _cant_write(path, writes)
    if why:
        raise Refused(why)


def _put_copy_back(op):
    """The copy beside a settings file as it was before apply() replaced it (op['copy']: path, bytes or None, mode)."""
    if not op.get('copy'):
        return
    p, data, mode = op['copy']
    if data is None:
        if os.path.lexists(p):
            os.unlink(p)
    else:
        _atomic_write(p, data, mode)


def _copy_from_before(path, before, after, history):
    """The copy of the settings file kept beside it, when Off may put its bytes back instead of `after` (the text with
    Switchboard's lines taken out): the file is exactly as Switchboard's last write to it left it, the copy is the one
    from before that write, and both hold the same settings. Then Off gives back the file's own bytes, even after the
    other switch moved in between (an empty file, a lone newline, an empty "env" over two lines). None otherwise."""
    for h in reversed(history):
        for f in h['files']:
            if f['path'] != path:
                continue
            if not f.get('existed') or not f.get('backup') or f.get('sha_after') != _sha(before):
                return None
            copy = _read(f['backup'])
            if copy is None or _sha(copy) != f.get('sha_before'):
                return None
            try:
                return copy if _Doc(copy, path).obj == _Doc(after, path).obj else None
            except Refused:
                return None
    return None


def apply(plan_, busy_ok=False, busy_within=BUSY_SECONDS):
    """Make a plan's edits. Atomic per file; a failure part way puts back what was already written.

    Refuses (raises Refused, nothing written) when the plan is blocked, when a file it touches changed
    since the plan was made, when Switchboard's record changed, or when a chat is busy (busy_ok=True
    skips that last check). Returns {'changed': [paths], 'backups': [paths], 'edits': plan's edits}.
    """
    if plan_.get('blocked'):
        raise Refused(plan_['blocked'])
    cd, sp = plan_['claude_dir'], plan_['state_path']
    st, raw_state = _load_state(sp)
    if _sha(raw_state) != plan_['state_sha']:
        raise Refused("Switchboard's record changed since this plan was made. Make a new plan.")
    edits, forget = plan_['edits'], plan_['forget']
    if not edits and not forget:
        return {'changed': [], 'backups': [], 'edits': []}
    if edits and not busy_ok:
        n = busy(cd, busy_within)
        if n:
            raise Refused('%d chat or helper histories were written in the last %d minutes, so a task may be '
                          'running. Claude Code would apply this to it for helpers it starts next, changing models '
                          'part way through ("%s" %s). Apply between tasks, or pass busy_ok=True.'
                          % ((n, busy_within // 60) + doc('env_running')))

    # Work out every new file before touching any.
    by_file = {}
    for e in edits:
        by_file.setdefault(e['file'], []).append(e)
    ops = []
    making = set()          # folders a write makes: only the first file in one makes it
    for path in sorted(by_file):
        before = _read(path)
        if _sha(before) != plan_['fingerprints'].get(path):
            raise Refused('%s changed since this plan was made (by you or by Claude Code). Nothing was written; '
                          'make a new plan.' % path)
        es = by_file[path]
        op = {'path': path, 'what': es[0]['what'], 'before': before, 'made': [], 'drop_dir': None}
        if op['what'] == 'setting':
            op['after'] = _settings_after(es, before, path)
            op['mode'] = _mode(path, 0o600)
            if op['after'] is not None and all(e['op'] == 'remove' for e in es):
                copy = _copy_from_before(path, before, op['after'], _dir_state(st, cd)['history'])
                if copy is not None:                    # an empty file's copy is b'': it counts
                    op['after'] = copy
        else:
            e = es[0]
            folder = os.path.dirname(path)
            text = PRESETS_BY_ID[plan_['preset']][e.get('kind', 'agents')].get(os.path.basename(path))
            op['after'] = text.encode('utf-8') if e['op'] in ('create', 'replace') else None
            op['mode'] = _mode(path, 0o644)
            if op['after'] is not None and not os.path.isdir(folder) and folder not in making:
                op['made'] = [folder]
                making.add(folder)
            if op['after'] is None and e.get('made_dir'):
                op['drop_dir'] = folder      # Switchboard made the folder; take it away if this empties it
        ops.append(op)

    ds = _dir_state(st, cd)
    owned_before = json.loads(json.dumps(ds['owned']))
    record = {'at': time.time(), 'preset': plan_['preset'], 'preset_before': ds['preset'],
              'owned_before': owned_before, 'files': []}
    done = []
    op = None
    try:
        for op in ops:
            op['removed'], op['copy'] = [], None
            path, before, after, mode = op['path'], op['before'], op['after'], op['mode']
            # The last look comes first, so that a refusal leaves the copy beside the settings file as it was (the
            # status line keeps its own copy in the same file).
            _last_look(path, before, after is not None)
            backup = None
            if before is not None:
                # Next to the file, except an agent file (the folder is scanned) or a settings file
                # Switchboard created and now deletes (it holds nothing of the user's).
                if op['what'] == 'setting' and after is not None:
                    beside = '%s.switchboard-backup' % path
                    op['copy'] = (beside, _read(beside), _mode(beside, 0o600))   # put back if this goes no further
                    backup = _backup_next_to(path, before, mode)
                else:
                    backup = _backup_in_state(sp, os.path.basename(path), before)
            for d in op['made']:
                os.makedirs(d)
            _last_look(path, before, after is not None)     # and again just before the swap: written meanwhile?
            if after is None:
                os.unlink(path)
                if op['drop_dir']:
                    try:
                        os.rmdir(op['drop_dir'])
                        op['removed'] = [op['drop_dir']]
                    except OSError:
                        pass        # not empty (something else lives there): it stays
            else:
                _atomic_write(path, after, mode)
            done.append(op)
            record['files'].append({'path': path, 'existed': before is not None, 'mode': mode,
                                    'sha_before': _sha(before), 'sha_after': _sha(after), 'backup': backup,
                                    'made_dirs': op['made'], 'removed_dirs': op['removed']})
        owned = ds['owned']
        for f in forget:
            if f['key']:
                owned['settings'].pop(f['key'], None)
            else:
                owned[f.get('kind', 'agents')].pop(os.path.basename(f['file']), None)
        for e in edits:
            if e['what'] == 'setting':
                if e['op'] == 'remove':
                    owned['settings'].pop(e['key'], None)
                else:
                    prev = owned['settings'].get(e['key']) or {}
                    owned['settings'][e['key']] = {
                        'value': e['new'],
                        'made_env': bool(prev.get('made_env') or e.get('made_env')),
                        'made_file': bool(prev.get('made_file') or e.get('made_file'))}
            else:
                name, kind = os.path.basename(e['file']), e.get('kind', 'agents')
                if e['op'] == 'delete':
                    owned[kind].pop(name, None)
                else:
                    prev = owned[kind].get(name) or {}
                    owned[kind][name] = {'sha256': _sha(_read(e['file'])),
                                         'made_dir': bool(prev.get('made_dir') or e.get('made_dir'))}
        ds['preset'] = plan_['preset']
        ds['history'].append(record)
        st['dirs'][cd] = ds
        _save_state(sp, st)
    except BaseException:
        if op is not None and op not in done:       # the step that failed: its new folder goes, its copy comes back
            for d in reversed(op['made']):
                try:
                    os.rmdir(d)
                except OSError:
                    pass
            try:
                _put_copy_back(op)
            except OSError:
                pass
        for op in reversed(done):
            try:
                _put_copy_back(op)
                for d in op['removed']:
                    os.makedirs(d, exist_ok=True)
                if op['before'] is None:
                    if os.path.lexists(op['path']):
                        os.unlink(op['path'])
                else:
                    _atomic_write(op['path'], op['before'], op['mode'])
                for d in reversed(op['made']):
                    try:
                        os.rmdir(d)
                    except OSError:
                        pass
            except OSError:
                pass
        raise
    return {'changed': [r['path'] for r in record['files']],
            'backups': [r['backup'] for r in record['files'] if r['backup']], 'edits': edits}


def undo(claude_dir=None, state_path=None, busy_ok=False, busy_within=BUSY_SECONDS):
    """Put back every file the last apply() changed, byte for byte (a file it created is removed).

    Refuses, changing nothing, when any of those files changed since (use the Off preset then: it
    takes out only Switchboard's own keys), when a backup is missing or altered, or when a chat is
    busy (busy_ok=True skips that check). Returns {'restored': [paths], 'preset': the preset now}.
    """
    cd = _cd(claude_dir)
    sp = state_path or default_state_path()
    st, _ = _load_state(sp)
    ds = _dir_state(st, cd)
    if not ds['history']:
        raise Refused('Nothing to undo.')
    last = ds['history'][-1]
    settings = os.path.join(cd, SETTINGS_NAME)
    now_bytes = []
    for f in last['files']:
        # Putting old bytes back writes. A new file only goes: Switchboard's own helper files and note do, but a
        # settings file set read-only stays, as the plan's way leaves it (both ways Off can take say the same).
        why = _cant_write(f['path'], f['existed'] or f['path'] == settings)
        if why:
            raise Refused(why)
        cur = _read(f['path'])
        if _sha(cur) != f['sha_after']:
            raise Refused('%s changed since Switchboard\'s last change (by you or by Claude Code), so putting the old '
                          'file back would lose that. Nothing was changed. Choose Off instead: it takes out only '
                          'what Switchboard added.' % f['path'])
        before = None
        if f['existed']:
            before = _read(f['backup']) if f['backup'] else None
            if _sha(before) != f['sha_before']:
                raise Refused('The backup of %s (%s) is missing or was changed. Nothing was changed.'
                              % (f['path'], f['backup']))
        now_bytes.append((f, cur, before))
    if last['files'] and not busy_ok:
        n = busy(cd, busy_within)
        if n:
            raise Refused('%d chat or helper histories were written in the last %d minutes, so a task may be '
                          'running. Undo between tasks, or pass busy_ok=True.' % (n, busy_within // 60))
    done = []
    try:
        # Folders first, files next, then the folders that change made: several files can share one folder, and
        # only one of them made it (or took it away).
        for f in last['files']:
            for d in f.get('removed_dirs') or []:
                os.makedirs(d, exist_ok=True)
        for f, cur, before in now_bytes:
            if before is None:
                if os.path.lexists(f['path']):
                    os.unlink(f['path'])
            else:
                _atomic_write(f['path'], before, f['mode'])
            done.append((f, cur))
        for f in reversed(last['files']):
            for d in reversed(f.get('made_dirs') or []):
                try:
                    os.rmdir(d)
                except OSError:
                    pass
        ds['owned'] = last['owned_before']
        ds['preset'] = last.get('preset_before')
        ds['history'].pop()
        st['dirs'][cd] = ds
        _save_state(sp, st)
        for f in last['files']:         # each file now equals its backup, so the copy can go
            if f.get('backup'):
                try:
                    os.unlink(f['backup'])
                except OSError:
                    pass
    except BaseException:
        for f, cur in reversed(done):
            try:
                for d in f.get('made_dirs') or []:
                    os.makedirs(d, exist_ok=True)
                if cur is None:
                    if os.path.lexists(f['path']):
                        os.unlink(f['path'])
                else:
                    _atomic_write(f['path'], cur, _mode(f['path'], f['mode']))
            except OSError:
                pass
        raise
    return {'restored': [f['path'] for f in last['files']], 'preset': ds['preset']}


PRESETS_BY_ID = dict((p['id'], p) for p in PRESETS)


# ---------------------------------------------------------------- what "Sonnet for helpers" would have moved
#
# would_have_moved() is about Sonnet for helpers only. There is no such number for Models by the job, and none is
# made up: history says which helper ran each request and on what model, but not which jobs Claude would have
# handed to lookup, reader, reviewer or verifier had they been there (Claude picks by description and the task).
# Once they have run, seen() shows each by its name (an agent's agentType is its `name`), with its models.
#
# Both functions read a summary from switchboard.usage.summarize. Today's summary says how much
# ran on the chat's model with no model set ('inherited'), by family and by its five biggest
# labels, but not which kind of helper each request came from. That matters: forks, Plan, the
# built-in Explore and agents defined in files are not moved by CLAUDE_CODE_SUBAGENT_MODEL. So
# when the summary has 'helper_rows' (one row per group of helper requests, below), the answer
# is exact; without them it is an upper bound and says so.
#
# summary['helper_rows'] = [{
#     'who':         'helpers' or 'workflows',
#     'type':        the agent's agentType ('general-purpose', 'workflow-subagent', 'Explore', 'Plan',
#                    'fork', 'claude', a custom name ...), None when it has no meta file,
#     'set_model':   the model its meta file names, None when none ('inherit' kept as 'inherit'),
#     'family':      the model family its requests ran on ('Fable', 'Opus', 'Sonnet', 'Haiku', 'Other'),
#     'chat_family': the chat's model family when the agent started, None when unknown,
#     'kind':        the kind of work (usage.KIND_RULES), 'label': its label as usage shows it,
#     'share':       share of the window's weighted tokens, 'requests': how many requests,
# }, ...]

FAMILY_SIZE = {'Haiku': 1, 'Sonnet': 2, 'Opus': 3, 'Fable': 4}
FAMILY_ORDER = ('Fable', 'Opus', 'Sonnet', 'Haiku', 'Other')
FOLLOW_VAR = ('general-purpose', 'claude', 'workflow-subagent')   # no model of their own: the variable applies
OWN_MODEL = ('claude-code-guide', 'statusline-setup')            # built-ins with a model of their own
NOT_MOVED = {
    'fork': "Forks always run on the chat's model.",
    'inherit': "Its definition says model: inherit, which comes before CLAUDE_CODE_SUBAGENT_MODEL.",
    'plan': "The built-in Plan helper isn't moved by CLAUDE_CODE_SUBAGENT_MODEL.",
    'explore_off': "The built-in Explore isn't moved by CLAUDE_CODE_SUBAGENT_MODEL, and the Explore helper was "
                   "left out.",
    'own': 'This built-in helper has a model of its own.',
    'custom': "Defined in an agent file whose model line history doesn't record; if it names a model, it keeps it.",
    'already': 'Already on the model this preset would give it, or a smaller one.',
    'unknown': "The history doesn't say what kind of helper this was, or what the chat ran on.",
}


def _not_set(m):
    return m in (None, '')


def _moves(r, lookups):
    """('Sonnet' / 'Haiku' / 'up', None) when "By the job" would move this row, else (None, reason)."""
    t, sm, fam, chat = r.get('type'), r.get('set_model'), r.get('family'), r.get('chat_family')
    if t == 'fork':
        return None, 'fork'
    if sm == 'inherit':
        return None, 'inherit'
    if not _not_set(sm):
        return None, None               # it named its model: not part of the question
    if t is None or fam not in FAMILY_SIZE or chat is None:
        return None, 'unknown'
    if t == 'Explore':
        # The built-in Explore runs the chat's model, or Opus under a Fable chat on a subscription.
        if fam != chat and not (chat == 'Fable' and fam == 'Opus'):
            return None, None
        if not lookups:
            return None, 'explore_off'
        return ('Haiku', None) if FAMILY_SIZE[fam] > FAMILY_SIZE['Haiku'] else (None, 'already')
    if fam != chat:
        return None, None               # something other than the chat chose its model
    if t == 'Plan':
        return None, 'plan'
    if t in OWN_MODEL:
        return None, 'own'
    if t not in FOLLOW_VAR:
        return None, 'custom'
    if FAMILY_SIZE[fam] > FAMILY_SIZE['Sonnet']:
        return 'Sonnet', None
    if fam == 'Sonnet':
        return None, 'already'
    return 'up', None


def would_have_moved(usage_summary, lookups=True):
    """The share of the summary's window that "By the job" would have moved to a smaller model.

    Counts only requests whose helper set no model, and only helpers the preset really moves:
    general-purpose, claude and workflow agents that inherited an Opus or Fable chat go to Sonnet;
    built-in Explore (when lookups) goes to Haiku. Returns {'exact', 'share', 'at_most',
    'to_sonnet', 'to_haiku', 'moved_up', 'no_model_set', 'from', 'not_counted', 'why'}.
    """
    s = usage_summary or {}
    rows = s.get('helper_rows')
    inh = s.get('inherited') or {}
    if rows is None:
        by = dict((x.get('family'), x.get('share') or 0.0) for x in inh.get('by_family') or [])
        at_most = by.get('Opus', 0.0) + by.get('Fable', 0.0) + (by.get('Sonnet', 0.0) if lookups else 0.0)
        return {'exact': False, 'share': None, 'at_most': at_most, 'to_sonnet': None, 'to_haiku': None,
                'moved_up': None, 'no_model_set': inh.get('share', 0.0), 'unknown_share': inh.get('unknown_share'),
                'from': [{'label': x.get('label'), 'share': x.get('share', 0.0), 'to': None}
                         for x in inh.get('top_labels') or []],
                'not_counted': [],
                'why': ("This summary doesn't say which kind of helper each request came from, so forks, Plan and "
                        'agents defined in files (which this preset does not move) cannot be told apart from the '
                        'ones it does. At most %.1f%% would have moved: the part that inherited an Opus or Fable '
                        "chat%s. Explore under a Fable chat runs Opus and isn't in that part."
                        % (100 * at_most, ', plus Sonnet for Explore' if lookups else ''))}
    moved, labels, reasons = {'Sonnet': 0.0, 'Haiku': 0.0, 'up': 0.0}, {}, {}
    base = 0.0      # usage's "inherited": no model set (or inherit) and ran on the chat's family
    for r in rows:
        share = r.get('share') or 0.0
        in_base = (_not_set(r.get('set_model')) or r.get('set_model') == 'inherit') \
            and r.get('family') is not None and r.get('family') == r.get('chat_family')
        if in_base:
            base += share
        to, why = _moves(r, lookups)
        if to:
            moved[to] += share
            if to != 'up':
                k = (r.get('label') or '(no label)', to)
                labels[k] = labels.get(k, 0.0) + share
        elif in_base:
            reasons[why or 'unknown'] = reasons.get(why or 'unknown', 0.0) + share
    share = moved['Sonnet'] + moved['Haiku']
    top = sorted(labels.items(), key=lambda kv: (-kv[1], kv[0]))
    return {'exact': True, 'share': share, 'at_most': share, 'to_sonnet': moved['Sonnet'], 'to_haiku': moved['Haiku'],
            'moved_up': moved['up'], 'no_model_set': base, 'unknown_share': inh.get('unknown_share'),
            'from': [{'label': lab, 'share': v, 'to': to} for (lab, to), v in top],
            'not_counted': [{'why': NOT_MOVED[k], 'reason': k, 'share': v}
                            for k, v in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))],
            'why': ('%.1f%% ran on the chat\'s model with no model set; %.1f%% would have moved to a smaller model.'
                    % (100 * base, 100 * share))}


def seen(usage_summary):
    """Which model each kind of helper actually ran on, from history. For the page's log.

    With 'helper_rows': {'exact': True, 'helpers': [{'helper', 'share', 'requests', 'no_model_set',
    'models': [{'family', 'share'}]}], 'kinds': [{'kind', 'share', 'requests', 'models'}]} where a
    model's share is of that helper's (or kind's) own total. Without them, the per-workflow mix the
    summary has, and 'exact': False.
    """
    s = usage_summary or {}
    rows = s.get('helper_rows')
    if rows is None:
        return {'exact': False, 'helpers': [], 'kinds': [],
                'workflows': [{'id': w.get('id'), 'name': w.get('name'), 'share': w.get('share', 0.0),
                               'labels': w.get('labels', []), 'phases': w.get('phases', []),
                               'models': w.get('models', [])} for w in s.get('workflows') or []],
                'why': "This summary doesn't split helpers by kind and model; only each workflow run's mix is known."}

    def group(keyf):
        g = {}
        for r in rows:
            k = keyf(r)
            e = g.setdefault(k, {'share': 0.0, 'requests': 0, 'fam': {}, 'unset': 0.0})
            sh = r.get('share') or 0.0
            e['share'] += sh
            e['requests'] += r.get('requests') or 0
            f = r.get('family') or 'Other'
            e['fam'][f] = e['fam'].get(f, 0.0) + sh
            if _not_set(r.get('set_model')):
                e['unset'] += sh
        return g

    def models(e):
        tot = e['share']
        return [{'family': f, 'share': (v / tot) if tot else 0.0}
                for f, v in sorted(e['fam'].items(), key=lambda kv: (-kv[1], FAMILY_ORDER.index(kv[0])
                                                                      if kv[0] in FAMILY_ORDER else 9))]

    def name(r):
        t = r.get('type')
        if t == 'workflow-subagent':
            return 'workflow agents'
        if t == 'fork':
            return 'forks'
        return t or '(unknown)'

    helpers = group(name)
    kinds = group(lambda r: r.get('kind') or 'not labelled')
    order = lambda g: sorted(g.items(), key=lambda kv: (-kv[1]['share'], kv[0]))   # noqa: E731
    return {'exact': True,
            'helpers': [{'helper': k, 'share': e['share'], 'requests': e['requests'],
                         'no_model_set': (e['unset'] / e['share']) if e['share'] else 0.0, 'models': models(e)}
                        for k, e in order(helpers)],
            'kinds': [{'kind': k, 'share': e['share'], 'requests': e['requests'], 'models': models(e)}
                      for k, e in order(kinds)]}


# ---------------------------------------------------------------- the command (reads only)

def main(argv=None, out=None):
    out = out or sys.stdout
    pic = current()
    w = out.write
    w('Claude Code folder: %s\n' % pic['claude_dir'])
    w('Helpers that name no model run: %s\n' % pic['helpers_with_no_model_run'])
    for k, v in sorted(pic['settings'].items()):
        w('  setting %s: %s\n' % (k, _jdump(v)))
    for k, v in sorted(pic['env'].items()):
        w('  env %s=%s (set by %s)\n' % (k, v['value'], v['set_by']))
    for a in pic['agents']:
        w('  agent %s: name %s, model %s, effort %s (%s)\n' % (a['file'], a['name'], a['model'], a['effort'],
                                                               a['set_by']))
    for line in pic['warnings']:
        w('! %s\n' % line)
    for line in pic['notes']:
        w('- %s\n' % line)
    on = set(part for part in PARTS if pic['parts'][part])
    for part in PARTS:
        now = part in on
        pl = plan(preset_with((on - {part}) if now else (on | {part})), only=part)
        w('\n%s is %s. Turning it %s would%s:\n' % (PART_NAMES[part], 'on' if now else 'off', 'off' if now else 'on',
                                                    ' be blocked' if pl['blocked'] else ''))
        if pl['blocked']:
            w('  %s\n' % pl['blocked'])
        for e in pl['edits']:
            w('  %s %s: %s\n' % (e['op'], e['key'] or e['file'], e['says']))
        for sk in pl['skipped']:
            w('  skip %s: %s\n' % (sk['what'], sk['why']))
        if not pl['edits'] and not pl['blocked']:
            w('  change nothing.\n')
    w('\nWhat this cannot do: %d limits (models.limits()).\n' % len(LIMITS))
    return 0


if __name__ == '__main__':
    sys.exit(main())
