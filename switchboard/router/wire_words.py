"""Every sentence of the router's wiring a person can see: the one switch, the trial run, the terminal list's lines,
History's count line, and the key that makes Claude Code load Switchboard's plugin. Plain words, short sentences.

tests/test_router_plugin_setting.py and tests/test_router_wire.py hold every string here to the same word rules as
switchboard/providers/words.py.
"""

KEY = 'CLAUDE_CODE_PLUGIN_DIRS'

# ---------- the key that makes Claude Code load the plugin (plugin_setting.py) ----------

STATE_UNSET = "Claude Code's plugin folders: not set, so Switchboard's plugin is not loaded."
STATE_OURS = "Claude Code is set to load Switchboard's plugin, set by Switchboard."
STATE_YOURS = "Claude Code is set to load Switchboard's plugin, set by you."
STATE_OTHER = "Claude Code's plugin folders are set by you, without Switchboard's plugin."
VERSION_OK = "Claude Code {version} is new enough for this setting, by Claude Code's own docs."
VERSION_OLD = 'Claude Code {version} is older than {need}, the first version that runs this plugin.'
VERSION_OLD_NO_WRITE = 'Claude Code {version} is older than {need}, which this plugin needs, so nothing was written.'
VERSION_UNKNOWN = ('Switchboard could not find which Claude Code is installed. Its plugin needs {need} or later, so '
                   'nothing was written.')
VERSION_UNKNOWN_SHORT = 'Switchboard could not find which Claude Code is installed. Its plugin needs {need} or later.'
VERSION_OTHER_OLD = 'An older Claude Code ({version}) is also installed. Sessions it starts do not run the plugin.'
WILL_ADD = 'Adds "CLAUDE_CODE_PLUGIN_DIRS" to the env block of your Claude Code user settings, naming {path}.'
WILL_APPEND = 'Adds {path} after the plugin folders you set in "CLAUDE_CODE_PLUGIN_DIRS". Yours stay as they are.'
WILL_MOVE = 'Puts {path}, where Switchboard\'s plugin is now, in place of the folder Switchboard named before.'
WILL_REMOVE = 'Takes out "CLAUDE_CODE_PLUGIN_DIRS", which Switchboard added.'
WILL_TAKE_OUT = 'Takes {path} out of "CLAUDE_CODE_PLUGIN_DIRS". The folders you set stay.'
DID_ADD = ("Claude Code is set to load Switchboard's plugin: added \"CLAUDE_CODE_PLUGIN_DIRS\" to your user "
           'settings.')
DID_APPEND = ("Claude Code is set to load Switchboard's plugin: added its folder after yours in "
              '"CLAUDE_CODE_PLUGIN_DIRS".')
DID_MOVE = ("Claude Code is set to load Switchboard's plugin from its new place: changed its folder in "
            '"CLAUDE_CODE_PLUGIN_DIRS".')
DID_REMOVE = "Claude Code is no longer set to load Switchboard's plugin: took out \"CLAUDE_CODE_PLUGIN_DIRS\"."
DID_TAKE_OUT = ("Claude Code is no longer set to load Switchboard's plugin: took its folder out of "
                '"CLAUDE_CODE_PLUGIN_DIRS". Yours stay.')
MAKES_FILE = 'There is no user settings file yet, so Switchboard makes one that holds only this key.'
MAKES_ENV = 'There is no env block in your user settings yet, so Switchboard adds one that holds only this key.'
DROPS_FILE = 'Switchboard made the settings file for this key alone, so the file goes too.'
NEW_SESSIONS = 'Claude Code sessions started from now on read it. Whether a session already open does is not known.'
ALREADY = "Claude Code is already set to load Switchboard's plugin. Nothing to change."
YOURS_KEPT = ('You named Switchboard\'s plugin in "CLAUDE_CODE_PLUGIN_DIRS" yourself, so Switchboard leaves it as '
              'it is.')
GONE_ALREADY = "Switchboard's plugin is already out of \"CLAUDE_CODE_PLUGIN_DIRS\". Nothing to change."
NOTHING_OURS = 'Switchboard has not set "CLAUDE_CODE_PLUGIN_DIRS", so there is nothing to put back.'
BROKEN = 'Your Claude Code settings file is not valid JSON, so Switchboard leaves it as it is.'
CANT_EDIT = 'Switchboard cannot edit your Claude Code settings file safely ({why}), so it leaves it as it is.'
WHY = {'link': 'it is a link to another file', 'folder': 'it is a folder', 'read_only': 'it is read-only',
       'twice': 'a key is in it twice', 'text': 'it is not plain UTF-8 text',
       'env': 'its env block is not an object', 'value': '"CLAUDE_CODE_PLUGIN_DIRS" in it is not text',
       'other': 'the edit would change something else'}
NO_FOLDER = 'There is no Claude Code folder at {path}, so there is nothing to change.'
NO_PLUGIN = "Switchboard's plugin is not at {path}, so nothing was written."
BAD_PLUGIN_PATH = "Switchboard's plugin folder ({path}) has a colon in its name, so it cannot go in the list."
RECORD_UNREADABLE = "Switchboard's own record of this setting cannot be read, so it changes nothing it cannot " \
    'account for.'
CHANGED_WHILE = 'Your Claude Code settings file changed while Switchboard was writing, so nothing was written.'
STILL_THERE = ('Switchboard has not taken its plugin folder out of "CLAUDE_CODE_PLUGIN_DIRS". It tries again the next '
               'time routing is turned off.')
ENV_TOO = ('"CLAUDE_CODE_PLUGIN_DIRS" is also set in the environment Claude Code starts from. Which of the two it '
           'reads is not known.')

# ---------- the one switch (wire.py) ----------

OFF = 'Routing is off.'
ON = 'Routing is on.'
ON_NOTHING_SET = 'Routing is on, with no route set, so nothing changes: switchboard routes set KIND CHOICE sets one.'
ON_TRIAL = 'Routing is on, as a trial run: the apps record what they would do and change nothing.'
ON_NO_PLUGIN = 'Routing is on, but Claude Code is not set to load its part, so work in Claude Code is not routed.'
ON_NO_PLUGIN_TRIAL = ('Routing is on, as a trial run, but Claude Code is not set to load its part, so nothing in '
                      'Claude Code is recorded.')
TURNED_ON = 'Routing is on.'
# turn_on's short form: each change it made, with its undo (the list's own sentence comes first)
BRIEF_PLUGIN = "Claude Code now loads Switchboard's part for routing (undo: switchboard routing off)."
BRIEF_CODEX = "Codex now has Switchboard's two hooks{files} (undo: switchboard routing off)."
BRIEF_CODEX_FILES = {1: ' and 1 helper file'}
BRIEF_CODEX_FILES_N = ' and {n} helper files'
BRIEF_CACHE = "Helpers now keep their cache for an hour (undo: switchboard routing cache off)."
TURNED_OFF = 'Routing is off. Work stays where it is typed, on the model it starts with.'
ALREADY_OFF = 'Routing is already off. Nothing was changed.'
TRIAL_ON = 'Trial run is on: the apps record what they would do and change nothing.'
TRIAL_OFF = 'Trial run is off: the apps now apply your routes.'
TRIAL_ROUTING_OFF = 'Routing is off, so there is no trial run to change.'
OFF_SWITCH_FAILED = 'Your routing switch could not be saved ({why}). It counts as off, and the rest was taken back.'
SWITCH_FAILED = 'Your routing switch could not be saved, so nothing else was done: {why}'
OFF_NOT_SAVED = 'Your routing switch could not be saved, so routing is still on and nothing was taken back: {why}'
PLAN_WRITTEN = 'Wrote the plan the apps read.'
PLAN_FAILED = 'The plan the apps read could not be written ({why}). Switchboard tries again at its next look.'
PLAN_REMOVED = 'Took away the plan, so every app works as if Switchboard were not there.'
PLAN_NONE = 'There was no plan to take away.'
PLAN_REMOVE_FAILED = 'The plan could not be taken away ({why}). Apps step aside on their own within three minutes.'
STEP_FAILED = 'This step could not be done ({why}). The other steps stand.'
CODEX_UNKNOWN = 'Whether Codex is on your list could not be read, so nothing was written for it.'
CODEX_NOT_ADDED = 'Codex is not on your list, so nothing was written for it.'
CODEX_NOT_INSTALLED = 'Codex is not installed, so nothing was written for it.'
CODEX_NO_FOLDER = 'There is no Codex folder at {path}, so nothing was written for Codex.'
CODEX_NOTHING = 'Switchboard has written nothing for Codex, so there is nothing to take back.'
CODEX_FILES_NONE = 'No route sends work to a Codex helper now, so no helper file was written.'
CODEX_FILES_WROTE = {1: 'Wrote 1 Codex helper file from your routes.'}
CODEX_FILES_WROTE_N = 'Wrote {n} Codex helper files from your routes.'
CODEX_FILES_REMOVED = {1: 'Took out 1 Codex helper file Switchboard wrote.'}
CODEX_FILES_REMOVED_N = 'Took out {n} Codex helper files Switchboard wrote.'
CODEX_FILES_GONE = 'No Codex helper file Switchboard wrote is left.'
CODEX_FILES_SAME = 'The Codex helper files already match your routes.'

# ---------- the terminal list's lines (wire.lines) ----------

PLAN_AGE = 'The plan was written {age} ago.'
PLAN_OLD = 'The plan is {age} old, so the apps work as if Switchboard were not there until it is written again.'
PLAN_MISSING = 'No plan has been written yet, so the apps work as if Switchboard were not there.'
LAST = 'Last: {line}'
LAST_NONE = 'No piece of work has been routed yet.'
SEEN = 'Seen in the routing record: {what}, on {models}.'
SEEN_NO_MODEL = 'Seen in the routing record: {what}.'
SEEN_AND = ' and '
SEEN_MODEL = '{model} ({n})'
SEEN_OTHER_MODELS = {1: '1 other model ({n})'}
SEEN_OTHER_MODELS_N = '{k} other models ({n})'
HELPERS = {1: '1 helper'}
HELPERS_N = '{n} helpers'
WORKFLOWS = {1: '1 workflow agent'}
WORKFLOWS_N = '{n} workflow agents'
SECONDS = {1: '1 second'}
SECONDS_N = '{n} seconds'
MINUTES = {1: '1 minute'}
MINUTES_N = '{n} minutes'
HOURS = {1: '1 hour'}
HOURS_N = '{n} hours'
DAYS = {1: '1 day'}
DAYS_N = '{n} days'

# ---------- History (wire.history) ----------

MORE = {1: '1 more routing decision was recorded before these.'}
MORE_N = '{n} more routing decisions were recorded before these.'
