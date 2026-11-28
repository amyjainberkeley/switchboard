"""Every sentence a person can see about the helpers' cache setting (cache_setting.py). Plain words, short sentences.

tests/test_router_cache_setting.py holds every string here to the same word rules as switchboard/providers/words.py,
and switchboard/router/ is in reach of tests/test_router_words.py. None of these promises a saving or a number: the
switch is worth turning on only from a person's own count.
"""

# The key, as Claude Code's settings page names it.
KEY = 'subagentPromptCacheTtl'

# What the switch is, what it costs, who turns it on, and the page's rule for usage credits.
WHAT = ('Asks Claude Code to keep the cache of everything outside your own chat (helpers, workflow agents, teammates, '
        'forks, compaction and session titles) for an hour between requests, not five minutes.')
COST = ('An hour cache is dearer to write, and a compaction writes the whole chat to it. It helps only when helpers '
        'often wait more than five minutes between requests, as they do while a long command runs.')
YOURS = 'It stays off until you turn it on from your own count. Switchboard never turns it on by itself.'
CREDITS = ('If your subscription is drawing on usage credits, that usage is billed, and Claude Code still uses the '
           'hour set here, so its cache writes are billed at the higher rate.')
DEFAULT = ('Without it, Claude Code gives helpers five minutes on a subscription, apart from a few helper requests '
           'Anthropic sets to an hour.')

# What the key is now. {value}: what the file holds.
STATE_UNSET = "Helpers' cache: not set, so Claude Code uses its own default."
STATE_OURS = "Helpers' cache: {value}, set by Switchboard."
STATE_YOURS = "Helpers' cache: {value}, set by you."
STATE_OTHER = "Helpers' cache: {value}, set by you. Claude Code takes only 5m or 1h and ignores any other value."

# Whether this Claude Code reads the key. {version}: the installed one; {need}: the first that reads it.
VERSION_OK = 'Claude Code {version} reads this setting.'
VERSION_OLD = 'Claude Code {version} is older than {need}, the first version that reads this setting.'
VERSION_OLD_NO_WRITE = 'Claude Code {version} is older than {need}, which this setting needs, so nothing was written.'
VERSION_UNKNOWN = 'Switchboard could not find which Claude Code is installed. This setting needs {need} or later.'
VERSION_OTHER_OLD = 'An older Claude Code ({version}) is also installed. Sessions it starts ignore this setting.'

# What a change does, before (plan) and after (apply). {value}, {old}: 5m or 1h.
WILL_ADD = ('Adds "subagentPromptCacheTtl": "{value}" to your Claude Code user settings. Nothing else in the file '
            'changes.')
WILL_CHANGE = ('Changes "subagentPromptCacheTtl" from "{old}" to "{value}", a value Switchboard set. Nothing else in '
               'the file changes.')
WILL_REMOVE = ('Takes out "subagentPromptCacheTtl", which Switchboard added, so helpers go back to Claude Code\'s '
               'default. Nothing else in the file changes.')
DID_ADD = 'Added "subagentPromptCacheTtl": "{value}" to your Claude Code user settings.'
DID_CHANGE = 'Changed "subagentPromptCacheTtl" from "{old}" to "{value}".'
DID_REMOVE = 'Took out "subagentPromptCacheTtl", which Switchboard added.'
MAKES_FILE = 'There is no user settings file yet, so Switchboard makes one that holds only this key.'
DROPS_FILE = 'Switchboard made the settings file for this key alone, so the file goes too.'
NEW_SESSIONS = 'A session started after this reads it. Whether a session already open picks it up is not known.'
# {path}: the folder in Switchboard's own data folder where the one copy is kept.
COPY_KEPT = ('Switchboard keeps one copy of your settings file as it was, readable only by you, in {path} until the '
             'key comes out again.')

# Why nothing changes. {value}: what the file holds; {path}: the Claude Code folder; {why}: a WHY phrase.
ALREADY = 'Already "{value}", set by Switchboard. Nothing to change.'
YOURS_KEPT = 'You set "subagentPromptCacheTtl" to "{value}" yourself, so Switchboard leaves it as it is.'
CHANGED_SINCE = ('You changed "subagentPromptCacheTtl" to "{value}" after Switchboard set it, so it is yours now and '
                 'stays as it is.')
GONE_ALREADY = '"subagentPromptCacheTtl" is already gone from your settings. Nothing to change.'
NOTHING_OURS = 'Switchboard has not set "subagentPromptCacheTtl", so there is nothing to put back.'
BROKEN = 'Your Claude Code settings file is not valid JSON, so Switchboard leaves it as it is.'
CANT_EDIT = 'Switchboard cannot edit your Claude Code settings file safely ({why}), so it leaves it as it is.'
WHY = {'link': 'it is a link to another file', 'folder': 'it is a folder', 'read_only': 'it is read-only',
       'twice': 'the key is in it twice', 'text': 'it is not plain UTF-8 text',
       'other': 'the edit would change something else'}
NO_FOLDER = 'There is no Claude Code folder at {path}, so there is nothing to change.'
RECORD_UNREADABLE = ("Switchboard's own record of this setting cannot be read, so it changes nothing it cannot account "
                     'for.')
CHANGED_WHILE = 'Your Claude Code settings file changed while Switchboard was writing, so nothing was written.'

# Something that beats the key. {name}: an environment variable's name; {where}: a WHERE phrase; {value}: 5m or 1h.
ENV_WINS = '{name} is set in {where}, and Claude Code takes it before this setting.'
FORCE_WINS = 'FORCE_PROMPT_CACHING_5M is on in {where}, so every request gets five minutes whatever this setting says.'
MANAGED_WINS = 'Managed settings set "subagentPromptCacheTtl" to "{value}", and they win over your user settings.'
WHERE = {'settings': 'the env block of your user settings', 'environ': 'the environment Claude Code starts from',
         'managed': 'managed settings'}
