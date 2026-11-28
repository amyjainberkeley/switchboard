"""The router's one look in each pass of the page's loop (server.loop calls step()).

step() runs wire.pass_() at most once a minute, puts the routing decisions it folded into History (wire.history), and
puts each fact an app's part of the router told (a signal: the account in use is at the person's line, or a turn
stopped at a usage limit) into History as one line, except in a trial run, where a signal is only noted in the log.
After routing is turned on, its first look counts the helpers' re-stores once, on a thread of its own, and sets an hour
cache for helpers when cache_rule calls for it. It asks the loop for nothing more: the loop's own reading of the
accounts takes the numbers in at its next pass. It never raises; a failure is written to the log once, and again only
after a look that worked.
"""
import os
import threading
import time

from .. import core
from . import loop_words as LW

EVERY_S = 60.0              # the most often the router looks, whatever the page's loop does
CACHE_LOOKED = 'router/cache-looked'    # an empty mark: the one look at the helpers' cache after turn-on was made


def _start(fn) -> None:
    threading.Thread(target=fn, daemon=True).start()


def _cache_once() -> None:
    """Count the helpers' re-stores, and set an hour cache for helpers when cache_rule calls for it; said in History
    in one line with how to undo it. Runs on a thread of its own; never raises."""
    try:
        from .. import history
        from . import cache_count, cache_rule, cache_setting, wire
        value = cache_rule.pick(cache_count.count(cache_count.projects_folders()))
        if value is None:
            return
        r = cache_setting.apply(value)
        if r.get('written'):
            core.write_bytes(core.data_path(wire.CACHE_MARK), b'', 0o600)    # routing off takes it back
            history.record('setting', LW.CACHE_ON, by='auto')
    except Exception as e:  # noqa: BLE001 - the key is left as it was
        _say_once('the cache look', e)
_STATE = {'last': None, 'since': None, 'said': False}


def _say_once(what, e) -> None:
    if not _STATE['said']:
        _STATE['said'] = True
        core.log('routing: %s failed (%s)' % (what, type(e).__name__))


def _pct(x):
    return int(round(x)) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def signal_line(fact):
    """The History sentence for one signal (record.signals()'s shape), or None for one it cannot say."""
    from .. import providers
    agent = fact.get('agent') if isinstance(fact, dict) else None
    if not isinstance(agent, str) or not providers.known(agent):
        return None
    app = providers.name_of(agent)
    if fact.get('what') == 'limit_stop':
        return LW.LIMIT_STOP.format(app=app)
    window, pct = LW.WINDOW.get(fact.get('window')), _pct(fact.get('pct'))
    if fact.get('what') == 'at_line' and window and pct is not None and 0 <= pct <= 100:
        return LW.AT_LINE.format(app=app, window=window, pct=pct)
    return None


def step(now=None, mono=None, kit=None) -> dict:
    """One look, unless the last one was under EVERY_S ago (monotonic). Returns wire.pass_()'s answer plus
    'history' (how many lines went to History), or {'skipped': True}. Never raises."""
    mono = time.monotonic() if mono is None else mono
    last = _STATE['last']
    if last is not None and 0 <= mono - last < EVERY_S:
        return {'skipped': True}
    _STATE['last'] = mono
    try:
        from .. import history
        from . import wire
        now = time.time() if now is None else now
        got = wire.pass_(now, kit=kit)
        n = 0
        for t, text in wire.history(now, _STATE['since']):
            history.record('info', text, t=t, by='auto')
            _STATE['since'] = t if _STATE['since'] is None else max(_STATE['since'], t)
            n += 1
        facts = list(got.get('signals') or ())
        got['signals_ignored'] = 0
        if got.get('dry') and facts:            # a trial run changes nothing: a signal is noted, not acted on
            got['signals_ignored'] = len(facts)
            core.log('routing: %d signal(s) in a trial run, nothing done' % len(facts))
            facts = []
        for fact in facts:
            text = signal_line(fact)
            if text:
                window = LW.WINDOW.get(fact.get('window'))
                history.record('info', text, t=fact.get('t'), by='auto', window=window, pct=_pct(fact.get('pct')))
                n += 1
        got['history'] = n
        got['cache_look'] = False
        mark = core.data_path(CACHE_LOOKED)
        if got.get('on') and not got.get('dry') and not os.path.exists(mark):
            core.write_bytes(mark, b'', 0o600)      # once, even when the look finds nothing to do
            _start(_cache_once)
            got['cache_look'] = True
        _STATE['said'] = False
        return got
    except Exception as e:  # noqa: BLE001 - routing never stops the page's loop
        _say_once('a look', e)
        return {'failed': True}
