"""The sentences the page's loop puts into History for what an app's part of the router told Switchboard."""

# {app}: the app's name; {window}: one of WINDOW's words; {pct}: percent used, a whole number.
AT_LINE = '{app} says the account in use is at your {window} line ({pct}% used).'
LIMIT_STOP = '{app} stopped a turn at a usage limit.'
WINDOW = {'five_hour': '5-hour', 'week': 'weekly'}
# The loop's one look at the helpers' cache after routing was turned on, when the person's own count called for it.
CACHE_ON = ('Helpers now keep their cache for an hour, as your own count of their re-stores called for. '
            'switchboard routing cache off puts it back.')
