"""Whether the person's own count calls for an hour cache for helpers: pick(result) -> '1h' or None (never '5m').

The weights are the published cache prices relative to a plain input token, used as a stand-in, not a price: a write
for five minutes is 1.25, a write for an hour 2.0, a read 0.1. So each token stored again that an hour cache would
read instead spares 1.25 - 0.1 = 1.15, and each token written for an hour in place of five minutes costs
2.0 - 1.25 = 0.75 more. The hour is picked only when what is spared beats what it costs with a margin of 1.2, on a
count that finished (or read at least MIN_RECORDS records) and saw at least MIN_RESTORES re-stores.
"""

SPARED = 1.25 - 0.1         # a token stored again, read from an hour cache instead
DEARER = 2.0 - 1.25         # a token written for an hour instead of five minutes
MARGIN = 1.2
MIN_RECORDS = 200
MIN_RESTORES = 20


def _n(x) -> float:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else 0.0


def pick(result):
    """'1h' when the count in `result` (cache_count.count()'s dict) calls for an hour cache for helpers, else None."""
    if not isinstance(result, dict):
        return None
    whole = not result.get('stopped_early') or _n(result.get('records_read')) >= MIN_RECORDS
    if not whole or _n(result.get('restores')) < MIN_RESTORES:
        return None
    if SPARED * _n(result.get('hour_would_read')) > MARGIN * DEARER * _n(result.get('hour_would_write')):
        return '1h'
    return None
