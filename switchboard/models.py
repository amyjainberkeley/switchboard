"""Models: the right model for each job, using only Claude Code's own settings.

Switchboard never sits between Claude Code and Anthropic. This module only reads and edits
two things Claude Code itself documents: the `env` block of the user settings file
(~/.claude/settings.json) and the user agents folder (~/.claude/agents/). It never changes the
chat's own model, never overrides a helper that names its model, and never edits a key or a
file it did not set.

What decides a helper's model today (code.claude.com/docs, checked 2026-11-13; the sentences
are in DOCS below, each with its URL):

  1. the model Claude passes when it starts the helper (aliases only),
  2. the `model` line in the helper's definition (`inherit` means the chat's model),
  3. CLAUDE_CODE_SUBAGENT_MODEL,
  4. the chat's own model.
  CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1 turns this upside down: one model for every helper.
  Workflow agents follow the same order. Forks always run on the chat's model. The built-in
  Explore and Plan helpers ignore CLAUDE_CODE_SUBAGENT_MODEL; a user agent named Explore
  replaces the built-in one and keeps its own model line.

Presets (PRESETS):

  Off         nothing of Switchboard's left behind.
  By the job  env CLAUDE_CODE_SUBAGENT_MODEL=sonnet in the user settings file, so helpers and
              workflow agents that name no model run on Sonnet instead of the chat's model;
              and a user agent file agents/switchboard-explore.md (name: Explore,
              model: haiku), so lookups run on Haiku. Nothing else changes.
  Everything small is not offered: the only documented switch is the FORCE pair, which
  overrides helpers that name their own model (see LIMITS).

current() reads; plan() says exactly what would change and changes nothing; apply() writes
atomically, keeps a backup, and records what it did in Switchboard's own state file so
undo() can put every file back byte for byte. would_have_moved() and seen() read a usage
summary (switchboard.usage.summarize).

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
EXPLORE_FILE = 'switchboard-explore.md'     # identity comes from `name:`, not the file name
SUBAGENT_VAR = 'CLAUDE_CODE_SUBAGENT_MODEL'
FORCE_VAR = 'CLAUDE_CODE_SUBAGENT_MODEL_FORCE'
EFFORT_VAR = 'CLAUDE_CODE_EFFORT_LEVEL'
NO_EXPLORE_PLAN_VAR = 'CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS'
# The environment variables that decide models or effort. Only these are ever reported;
# other `env` entries (keys, tokens) are never read out.
MODEL_VARS = (SUBAGENT_VAR, FORCE_VAR, EFFORT_VAR, 'ANTHROPIC_MODEL', 'ANTHROPIC_DEFAULT_MODEL',
              'ANTHROPIC_DEFAULT_FABLE_MODEL', 'ANTHROPIC_DEFAULT_OPUS_MODEL',
              'ANTHROPIC_DEFAULT_SONNET_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL',
              NO_EXPLORE_PLAN_VAR, 'CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS')
MODEL_KEYS = ('model', 'effortLevel', 'modelSettings', 'availableModels', 'advisorModel', 'fallbackModel')
# Versions below these let the change do something other than what the preset says.
NEEDS_ORDER = (2, 1, 251)       # before it, CLAUDE_CODE_SUBAGENT_MODEL overrode named models
NEEDS_OMIT = (2, 1, 271)        # omitClaudeMd, which keeps our Explore as lean as the built-in
BUSY_SECONDS = 300

DOCS_BASE = 'https://code.claude.com/docs/en/'
# Each sentence this module relies on, quoted from the live docs (checked 2026-11-13).
DOCS = {
    'order': ('sub-agents#choose-a-model',
              "Claude Code resolves the subagent's model in this order: 1. The per-invocation `model` parameter "
              "2. The subagent definition's `model` frontmatter, where `inherit` selects the main conversation's "
              "model 3. The `CLAUDE_CODE_SUBAGENT_MODEL` environment variable, when you set it to a model alias "
              "or model ID 4. The main conversation's model"),
    'order_before': ('sub-agents#choose-a-model',
                     'Before v2.1.251, `CLAUDE_CODE_SUBAGENT_MODEL` came first in this order and overrode both the '
                     'per-invocation parameter and the frontmatter, including `model: inherit`.'),
    'var': ('env-vars#variables',
            'The default model for subagents, agent team teammates, and workflow agents that aren\'t assigned a '
            'model another way. Accepts an alias such as `haiku` or a full model name. Two sources take '
            'precedence over it: a model Claude passes when it spawns the agent, and a `model` field in the '
            'agent\'s definition, including `inherit`.'),
    'var_inherit': ('sub-agents#choose-a-model',
                    'An alias in `CLAUDE_CODE_SUBAGENT_MODEL` always resolves to the version the alias points to, '
                    'even when it names the main conversation\'s family. Setting the variable to `inherit` is the '
                    'same as leaving it unset.'),
    'explore_plan': ('sub-agents#choose-a-model',
                     "Setting `CLAUDE_CODE_SUBAGENT_MODEL` by itself doesn't change the model the built-in Explore "
                     'and Plan subagents run on.'),
    'explore_override': ('sub-agents#built-in-subagents',
                         'A user or project subagent named `Explore` overrides the built-in and keeps its own '
                         '`model` field, so define one with `model: haiku` to run exploration on a lower-cost model.'),
    'explore_model': ('sub-agents#built-in-subagents',
                      "**Model**: the main conversation's model. When the main conversation runs Fable, Explore's "
                      'model depends on how you connect: With a Claude subscription, an Anthropic Console account, '
                      'or an LLM gateway reached through `ANTHROPIC_BASE_URL`, Explore runs on the Opus model that '
                      'the `opus` alias resolves to.'),
    'explore_tools': ('sub-agents#built-in-subagents', '**Tools**: read-only tools; Write and Edit are denied'),
    'explore_lean': ('sub-agents#built-in-subagents',
                     'Explore and Plan start without your CLAUDE.md files and the git status snapshot to keep '
                     'research fast and inexpensive.'),
    'git_status': ('sub-agents#what-loads-at-startup',
                   "You can't change which subagents receive git status. Only Explore and Plan skip it."),
    'prompts': ('sub-agents#what-loads-at-startup',
                'Custom subagents define theirs in the markdown body or `prompt` field. Built-in agents have '
                'predefined prompts.'),
    'plan_agent': ('sub-agents#built-in-subagents',
                   '**Model**: inherits from the main conversation, unless you set `CLAUDE_CODE_SUBAGENT_MODEL` and '
                   'force it onto every subagent'),
    'general_purpose': ('sub-agents#built-in-subagents',
                        '**Model**: the `CLAUDE_CODE_SUBAGENT_MODEL` model if you set one and nothing assigns a '
                        "model another way, otherwise the main conversation's model"),
    'claude_agent': ('sub-agents#built-in-subagents',
                     'None of its own; follows the model order when Claude spawns it as a subagent'),
    'own_model_builtins': ('sub-agents#built-in-subagents',
                           '| statusline-setup | Sonnet | ... | claude-code-guide | Haiku |'),
    'fork': ('sub-agents#how-forks-differ-from-other-subagents',
             '| Model | Same as main session | From the subagent\'s `model` field |'),
    'force': ('sub-agents#run-every-subagent-on-one-model',
              "While `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` is on, Claude Code ignores the `model` field in subagent "
              "definitions, and Claude can't pass a model when it starts a subagent. These subagents still run on "
              "the main conversation's model: ... A fork ... A skill that runs in a subagent with `model: inherit`"),
    'force_two_vars': ('sub-agents#run-every-subagent-on-one-model',
                       '`CLAUDE_CODE_SUBAGENT_MODEL` is a default, so a subagent\'s definition or a model Claude '
                       'passes still takes precedence over it. To apply one model to every subagent, teammate, and '
                       'workflow agent, also set `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` to `1`.'),
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
                    'environment it builds takes precedence instead: Claude Code ignores an `env` value from any '
                    'settings file for a variable the launch environment already sets.'),
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
    'agents_new_dir': ('sub-agents#write-subagent-files',
                       'The watcher covers only directories that existed when the session started, so after '
                       "creating a scope's first agent file in a new `agents` directory, restart to load it."),
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

def _frontmatter(text):
    """Top-level `key: value` lines of a Markdown file's YAML frontmatter (None when it has none).
    A small reader: enough for name, model and effort lines; nested blocks are skipped."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != '---':
        return None
    out = {}
    for line in lines[1:]:
        if line.strip() == '---':
            return out
        if not line or line[0] in ' \t#-' or ':' not in line:
            continue
        k, v = line.split(':', 1)
        v = v.strip()
        if v[:1] in ('"', "'") and v[-1:] == v[:1] and len(v) >= 2:
            v = v[1:-1]
        elif ' #' in v:
            v = v.split(' #', 1)[0].rstrip()
        out[k.strip()] = v
    return None     # no closing ---


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
            fm = _frontmatter(raw.decode('utf-8', 'replace'))
            out.append({'file': os.path.relpath(p, claude_dir), 'path': p,
                        'name': (fm or {}).get('name'), 'model': (fm or {}).get('model'),
                        'effort': (fm or {}).get('effort'), 'sha256': _sha(raw)})
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


# ---------------------------------------------------------------- presets, as data

# Each preset is the set of things Switchboard wants in place. Anything Switchboard owns that a
# preset does not want is taken out. 'settings' maps (section, variable) to a value;
# 'agents' maps a file name in the user agents folder to its text.
PRESETS = (
    {'id': 'off', 'name': 'Off', 'default': False,
     'says': "Nothing of Switchboard's left behind: its setting and its agent file are taken out.",
     'settings': {}, 'agents': {}},
    {'id': 'by-the-job', 'name': 'By the job', 'default': True,
     'says': ('Helpers and workflow agents that name no model run on Sonnet instead of your chat\'s model; '
              'lookups (Explore) run on Haiku. Helpers that name a model keep it. Your chat keeps its model.'),
     'settings': {('env', SUBAGENT_VAR): 'sonnet'}, 'agents': {EXPLORE_FILE: EXPLORE_TEXT}},
)
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
        d = {'preset': None, 'owned': {'settings': {}, 'agents': {}}, 'history': []}
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
        a['set_by'] = 'switchboard' if mine and mine.get('sha256') == a['sha256'] else 'you'
        del a['sha256']
        pic['agents'].append(a)
    for key, info in sorted(owned['settings'].items()):
        sec, var = key.split('.', 1)
        if sec == 'env' and pic['env'].get(var, {}).get('set_by') == 'switchboard':
            pic['ours'].append({'what': 'setting', 'key': key, 'value': info['value'], 'file': path})
    for fn in sorted(owned['agents']):
        p = os.path.join(cd, AGENTS_DIR, fn)
        if _sha(_read(p)) == owned['agents'][fn].get('sha256'):
            pic['ours'].append({'what': 'file', 'file': p})

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
        if e.get(EFFORT_VAR):
            note('%s=%s in %s sets the effort of every model and beats any effort line in a helper ("%s" %s).'
                 % ((EFFORT_VAR, e[EFFORT_VAR], w) + doc('effort_var')))
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
         lookups=True):
    """The exact edits that preset_id would make, without making them.

    Returns {'preset', 'name', 'claude_dir', 'state_path', 'made_at', 'blocked', 'edits', 'forget',
    'skipped', 'notes', 'fingerprints', 'state_sha'}. Each edit is {'file', 'what' ('setting' or 'file'),
    'key', 'line', 'old', 'new', 'op', 'says'}; 'old'/'new' are None where nothing is or will be.
    lookups=False leaves out the Explore helper. Nothing here writes.
    """
    p = preset(preset_id)
    cd = _cd(claude_dir)
    sp = state_path or default_state_path()
    environ = os.environ if environ is None else environ
    md = default_managed_dir() if managed_dir is None else managed_dir
    out = {'preset': p['id'], 'name': p['name'], 'claude_dir': cd, 'state_path': sp, 'made_at': time.time(),
           'blocked': None, 'edits': [], 'forget': [], 'skipped': [], 'notes': [], 'fingerprints': {},
           'state_sha': None}
    skip = lambda what, why: out['skipped'].append({'what': what, 'why': why})   # noqa: E731

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
    if pic['force'] and p['id'] != 'off':
        out['blocked'] = ('%s is on in %s, so every helper runs one model and their own model lines are ignored '
                          '("%s" %s). Turn it off first; Switchboard never overrides a helper that names its model.'
                          % ((FORCE_VAR, pic['force']) + doc('force')))
        return out
    first, others = _old(pic['versions'], NEEDS_ORDER)
    if first and p['id'] != 'off':
        out['blocked'] = ('Claude Code %s (%s, what `claude` starts) is older than %s. There, '
                          'CLAUDE_CODE_SUBAGENT_MODEL overrides helpers that name a model ("%s" %s). Update Claude '
                          'Code first.' % ((first['version'], first['where'], _vs(NEEDS_ORDER)) + doc('order_before')))
        return out
    if p['id'] != 'off':
        for v in others:
            out['notes'].append('An older Claude Code (%s at %s) is also installed. A session started with it would '
                                'let this setting override helpers that name a model; update or remove it.'
                                % (v['version'], v['where']))
        if not pic['versions']:
            out['notes'].append('Switchboard could not find the installed Claude Code version; this preset needs '
                                'v%s or later.' % _vs(NEEDS_ORDER))

    # ---- the settings file
    spath = os.path.join(cd, SETTINGS_NAME)
    sraw = None
    sdoc = None
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
    if sproblem and sraw is not None and p['id'] != 'off':
        # Unreadable settings hide what the user has set (deny rules, env), so nothing is added.
        out['blocked'] = sproblem
        return out

    MISSING = object()
    wanted = dict((_skey(*k), v) for k, v in p['settings'].items())
    for key in sorted(set(wanted) | set(owned['settings'])):
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
                'says': 'Takes out %s=%s, which Switchboard added. Helpers that name no model go back to your '
                        "chat's model in sessions you start after this; running sessions keep it until restarted."
                        % (var, cur),
                'made_env': bool(mine.get('made_env')), 'made_file': bool(mine.get('made_file'))})
            continue
        if mine is not None and cur == want:
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
        op = 'change' if mine is not None and cur is not MISSING else 'add'
        out['edits'].append({
            'file': spath, 'what': 'setting', 'key': key, 'line': None,
            'old': None if cur is MISSING else cur, 'new': want, 'op': op,
            'says': ('Adds %s=%s to "env" in your user settings: helpers and workflow agents that name no model run '
                     'on %s instead of your chat\'s model. Helpers that name a model keep it; forks, Plan and the '
                     'built-in Explore are not moved by this line.' % (var, want, want.capitalize()))
            if var == SUBAGENT_VAR else 'Sets %s=%s.' % (var, want),
            'made_env': sdoc.member('env') is None, 'made_file': sraw is None})
    if any(e['what'] == 'setting' for e in out['edits']):
        out['fingerprints'][spath] = _sha(sraw)

    # ---- the agents folder
    adir = os.path.join(cd, AGENTS_DIR)
    want_files = dict(p['agents']) if lookups else {}
    if not lookups and p['agents']:
        skip(os.path.join(adir, EXPLORE_FILE), 'Left out on request (lookups=False).')
    for fn in sorted(set(want_files) | set(owned['agents'])):
        fpath = os.path.join(adir, fn)
        mine = owned['agents'].get(fn)
        if os.path.islink(fpath) or os.path.isdir(fpath):
            skip(fpath, '%s is a link or a folder; Switchboard leaves it alone.' % fpath)
            continue
        craw = _read(fpath)
        csha = _sha(craw)
        want = want_files.get(fn)
        if mine is not None and craw is not None and csha != mine['sha256']:
            out['forget'].append({'key': None, 'file': fpath,
                                  'says': 'You edited %s since Switchboard wrote it; it is yours now and stays.' % fpath})
            mine = None
            if want is not None:
                skip(fpath, 'You edited it; Switchboard leaves your version.')
            continue
        if want is None:
            if mine is None:
                continue
            if craw is None:
                out['forget'].append({'key': None, 'file': fpath, 'says': '%s is already gone.' % fpath})
                continue
            out['edits'].append({
                'file': fpath, 'what': 'file', 'key': None, 'line': 'model: haiku', 'old': 'model: haiku',
                'new': None, 'op': 'delete',
                'says': "Deletes Switchboard's Explore helper; the built-in Explore (your chat's model) comes back. "
                        'Sessions pick this up within seconds; one may need a restart.',
                'made_dir': bool(mine.get('made_dir'))})
            out['fingerprints'][fpath] = csha
            continue
        wsha = _sha(want.encode('utf-8'))
        why = _explore_blocked(pic, adir)
        if mine is not None and craw is not None and why:
            # Ours is in place but no longer should be: the user added an Explore of their own (two
            # with one name load in no fixed order), or turned Explore off. Theirs wins.
            out['edits'].append({
                'file': fpath, 'what': 'file', 'key': None, 'line': 'model: haiku', 'old': 'model: haiku',
                'new': None, 'op': 'delete', 'says': "Takes out Switchboard's Explore helper. " + why,
                'made_dir': bool(mine.get('made_dir'))})
            out['fingerprints'][fpath] = csha
            continue
        if mine is not None and csha == wsha:
            continue
        if mine is None and craw is not None:
            skip(fpath, 'A file with this name is already there and is not Switchboard\'s.')
            continue
        if why:
            skip(fpath, why)
            continue
        made_dir = not os.path.isdir(adir)
        says = ('Adds %s: a read-only Explore helper of Switchboard\'s that runs on Haiku in place of the built-in '
                'Explore. Its instructions are Switchboard\'s (the built-in ones are not published); it reads no '
                'CLAUDE.md, like the built-in, but it does get the git status snapshot the built-in skips.' % fpath)
        if made_dir:
            says += ' The agents folder is new, so sessions already running see it only after a restart.'
        out['edits'].append({'file': fpath, 'what': 'file', 'key': None, 'line': 'model: haiku',
                             'old': None, 'new': 'model: haiku',
                             'op': 'replace' if (mine is not None and craw is not None) else 'create',
                             'says': says, 'made_dir': made_dir})
        out['fingerprints'][fpath] = csha

    if out['edits'] and p['id'] != 'off':
        out['notes'].append('Claude Code applies a saved env change and a new agent file to running sessions: '
                            'helpers they start after this use the new model ("%s" %s). Helpers already running '
                            'keep theirs.' % doc('env_running'))
    if any(e['op'] == 'remove' for e in out['edits']):
        out['notes'].append('Sessions already running keep CLAUDE_CODE_SUBAGENT_MODEL until they are restarted '
                            '("%s" %s).' % doc('env_running'))
    if p['id'] == 'off' and pic['backups']:
        out['notes'].append('Switchboard keeps its %d backup copies (%s) so this can be undone. Claude Code does not '
                            'read them.' % (len(pic['backups']), ', '.join(pic['backups'])))
    return out


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
        else:
            e = es[0]
            folder = os.path.dirname(path)
            text = PRESETS_BY_ID[plan_['preset']]['agents'].get(os.path.basename(path))
            op['after'] = text.encode('utf-8') if e['op'] in ('create', 'replace') else None
            op['mode'] = _mode(path, 0o644)
            if op['after'] is not None and not os.path.isdir(folder):
                op['made'] = [folder]
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
            op['removed'] = []
            path, before, after, mode = op['path'], op['before'], op['after'], op['mode']
            backup = None
            if before is not None:
                # Next to the file, except an agent file (the folder is scanned) or a settings file
                # Switchboard created and now deletes (it holds nothing of the user's).
                backup = (_backup_next_to(path, before, mode) if op['what'] == 'setting' and after is not None
                          else _backup_in_state(sp, os.path.basename(path), before))
            for d in op['made']:
                os.makedirs(d)
            if _sha(_read(path)) != _sha(before):          # last look before the swap
                raise Refused('%s changed while Switchboard was writing. Nothing of it was changed; make a new '
                              'plan.' % path)
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
                owned['agents'].pop(os.path.basename(f['file']), None)
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
                name = os.path.basename(e['file'])
                if e['op'] == 'delete':
                    owned['agents'].pop(name, None)
                else:
                    prev = owned['agents'].get(name) or {}
                    owned['agents'][name] = {'sha256': _sha(_read(e['file'])),
                                             'made_dir': bool(prev.get('made_dir') or e.get('made_dir'))}
        ds['preset'] = plan_['preset']
        ds['history'].append(record)
        st['dirs'][cd] = ds
        _save_state(sp, st)
    except BaseException:
        if op is not None and op not in done:       # the step that failed: only its new folder goes
            for d in reversed(op['made']):
                try:
                    os.rmdir(d)
                except OSError:
                    pass
        for op in reversed(done):
            try:
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
    now_bytes = []
    for f in last['files']:
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
        for f, cur, before in now_bytes:
            for d in f.get('removed_dirs') or []:
                os.makedirs(d, exist_ok=True)
            if before is None:
                if os.path.lexists(f['path']):
                    os.unlink(f['path'])
            else:
                _atomic_write(f['path'], before, f['mode'])
            done.append((f, cur))
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


# ---------------------------------------------------------------- what "By the job" would have moved
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
    pl = plan('by-the-job')
    w('\n"By the job" would%s:\n' % (' be blocked' if pl['blocked'] else ''))
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
