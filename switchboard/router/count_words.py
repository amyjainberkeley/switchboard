"""Every sentence a person can see about their own count behind the helpers' cache switch (cache_count.py).

The switch shows these before it can be turned on. They say what was counted and nothing more: no money, no share of
a plan's limit, no promise that an hour cache costs less. tests/test_router_cache_count.py holds each one to the word
rules of switchboard/providers/words.py, and switchboard/router/ is in reach of tests/test_router_words.py.
"""

# The window a count covers. {when} starts a sentence, {span} ends one; {days}: whole days.
WHEN_DAYS = 'In the last {days} days'
WHEN_DAY = 'In the last day'
WHEN_TODAY = 'Today'
SPAN_DAYS = 'the last {days} days'
SPAN_DAY = 'the last day'
SPAN_TODAY = 'today'

# How often a helper stored its whole context again after a wait of five minutes to an hour. {when}: the window;
# {size}: the smallest context counted (cache_count.MIN_CONTEXT), with commas; {n}: the count, with commas.
COUNTED_MANY = ('{when} your helpers stored a context of {size} tokens or more again {n} times after a wait of five '
                'minutes to an hour.')
COUNTED_ONE = ('{when} your helpers stored a context of {size} tokens or more again once after a wait of five minutes '
               'to an hour.')
# No re-store, said only when every record found was read whole.
NONE = '{when} no helper stored a context of {size} tokens or more again after a wait of five minutes to an hour.'
# No re-store in the records that were read, when some could not be read or counting stopped early. {read}: how many.
NONE_FOUND_ONE = ('In the one helper record Switchboard could read, no helper stored a context of {size} tokens or '
                  'more again after a wait of five minutes to an hour.')
NONE_FOUND_MANY = ('In the {read} helper records Switchboard could read, no helper stored a context of {size} tokens '
                   'or more again after a wait of five minutes to an hour.')
# Records were found, but not one could be read whole.
NOTHING_COUNTED = 'Switchboard has no count yet: it could not read a whole helper record from {span}.'
NO_RECORDS = 'Switchboard found no helper records from {span} on this Mac.'

# Said with a count: a fork's record holds the requests it copied, and they are read again there.
FORKS = 'A fork starts with a copy of the requests before it, so those can be counted twice.'

# The two totals a person can weigh. {read}: tokens stored again in those times (an estimate, so "up to"); {write}:
# every other token the helpers wrote to the cache for five minutes.
TOKENS = ('With an hour cache, up to {read} of the tokens stored again are read from the cache instead, and the other '
          '{write} tokens your helpers write to the cache are written at the dearer hour rate.')
WEIGH = ('These are counts of tokens, not a price. An hour cache is dearer to write, so it does not help helpers that '
         'seldom wait more than five minutes.')

# What the count left out.
UNREAD_ONE = 'One helper record could not be read and is not in the count.'
UNREAD_MANY = '{n} helper records could not be read and are not in the count.'
STOPPED = 'Switchboard stopped counting after {s} seconds, so this covers {read} of {total} helper records.'
# A record that took the whole time limit last time and has not changed since. {n}: how many; {s}: the limit.
TOO_LONG_ONE = 'One helper record is too long to read in {s} seconds, so it is not in the count.'
TOO_LONG_MANY = '{n} helper records are too long to read in {s} seconds, so they are not in the count.'
# Stopped while still looking for records, so how many there are is not known.
STOPPED_LOOKING = ('Switchboard stopped looking for helper records after {s} seconds, so there may be more than it '
                   'counted.')
