"""Every sentence and name of the router that a person can see. Plain words, short sentences.

tests/test_router_words.py holds every string here to the same word rules as switchboard/providers/words.py.
"""

# The kinds of work (providers/kinds.py), each as a helper, as a piece of work, and as the reason a route gives.
KIND_HELPER = {'build': 'A build helper', 'fix': 'A fix helper', 'review': 'A review helper',
               'plan': 'A planning helper', 'find': 'A lookup helper', 'write': 'A writing helper',
               'other': 'A helper'}
KIND_WORK = {'build': 'A build job', 'fix': 'A fix', 'review': 'A review', 'plan': 'A planning job',
             'find': 'A lookup', 'write': 'A writing job', 'other': 'A job'}
KIND_PLURAL = {'build': 'builds', 'fix': 'fixes', 'review': 'reviews', 'plan': 'plans', 'find': 'lookups',
               'write': 'writing', 'other': 'other jobs'}
ROUTE_WHY = {k: 'your route for ' + v for k, v in KIND_PLURAL.items()}
PRESET_WHY = {k: 'the preset you turned on, for ' + v for k, v in KIND_PLURAL.items()}
WHY_NONE = 'your route'
# Why work whose kind has no route ran as it did: the person's default for where it was started.
DEFAULT_WHY = {'workflow': 'your default for workflow agents', 'helper': 'your default for helpers',
               'chat': 'your default for your chat'}

# Who did the work, at the start of a History line. {helper}: a KIND_HELPER phrase. {app}: Claude, Codex.
SUBJECT = {'helper': '{helper} in {app}', 'chat': 'Your chat in {app}', 'workflow': 'A workflow agent in {app}'}

# One History line per decision an adapter recorded. {at}: AT or ''. {on}: ON or ''.
LINE_MODEL = '{subject} ran on {model}{at}: {why}.'
LINE_EFFORT = '{subject} ran at {effort} effort: {why}.'
LINE_HAND = '{work} from {app} went to {to}{on}{at}: {why}.'
LINE_KEPT_MODEL = '{subject} kept {model}, the model it was started with.'
LINE_KEPT = '{subject} kept the model it was started with.'
LINE_KEPT_EFFORT = '{subject} kept its own effort, already at or below {effort}: {why}.'
LINE_KEPT_EFFORT_BARE = '{subject} kept its own effort: {why}.'
LINE_NONE = '{subject} stayed as it was: no choice on {why} could take it now ({passed}).'
LINE_NONE_BARE = '{subject} stayed as it was: no choice on {why} could take it now.'
LINE_WOULD_MODEL = 'Trial run, nothing changed: {subject} would have run on {model}{at}.'
LINE_WOULD_EFFORT = 'Trial run, nothing changed: {subject} would have run at {effort} effort.'
LINE_WOULD_HAND = 'Trial run, nothing changed: {work} from {app} would have gone to {to}{on}{at}.'
LINE_WOULD_NONE = 'Trial run, nothing changed: {subject} would have stayed as it was.'
LINE_CARRY_ON = '{subject} was told to continue after Switchboard moved the account.'
AT = ' at {effort}'
ON = ' on {model}'
A_MODEL = 'a model'
APP_OTHER = 'another app'

# Why a route choice was passed, after the app's name ("Codex at your lines").
PASSED = {'not_added': 'not added', 'not_installed': 'not installed', 'signed_out': 'signed out',
          'too_old': 'too old a version', 'never': 'not used for this', 'out': 'at your lines',
          'hand_off': 'handing work over is off', 'unknown': 'numbers not known'}
PASSED_JOIN = ', '

# The person's switches, by the name settings.py keeps them under.
SWITCHES = {'on': 'Route work by your routes',
            'dry': 'Trial run: record what would happen, change nothing',
            'helpers.force': 'Set the model of a helper even when it names its own',
            'chat.effort': 'Set the effort of your own chat by the kind of work (a chat default also lowers an '
                           'effort you set by hand)',
            'chat.model_on_first_turn': "Set the model too, on a chat's first turn only",
            'hand.on': 'Hand work to another app on your route',
            'hand.write': 'Let that app change files (else it only reads)',
            'carry_on': 'After a move at your line, send the chat one "continue"'}

# The switches file cannot be read. SETTINGS_UNREADABLE goes to History once; SETTINGS_REFUSED answers a change.
SETTINGS_UNREADABLE = ('Your routing switches could not be read, so every one counts as off until the file is fixed '
                       'or reset.')
SETTINGS_REFUSED = ('Your routing switches ({path}) cannot be read, so nothing was changed. Move the file aside and '
                    'try again.')
SETTINGS_UNKNOWN = 'There is no routing switch called {name}. The switches are: {names}.'

# The record could not all be read.
RECORD_SKIPPED = '{n} lines of the routing record could not be read and were passed over.'
SIGNALS_LEFT = '{n} files beside the routing record could not be read and were left where they are.'
RECORD_FOLD_REMOVED = '{n} files of the routing record still could not be read an hour after they were written, so ' \
    'they were removed.'

# Every History sentence with the kind it is filed under (history.KINDS).
HISTORY = tuple((t, 'info') for t in (LINE_MODEL, LINE_EFFORT, LINE_HAND, LINE_KEPT_MODEL, LINE_KEPT,
                                      LINE_KEPT_EFFORT, LINE_KEPT_EFFORT_BARE, LINE_NONE, LINE_NONE_BARE,
                                      LINE_WOULD_MODEL, LINE_WOULD_EFFORT, LINE_WOULD_HAND, LINE_WOULD_NONE,
                                      LINE_CARRY_ON)) + (
    (SETTINGS_UNREADABLE, 'problem'),)
