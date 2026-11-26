"""Your week, under the accounts: the numbers the page draws, up to three short insights, and the readings log.

Everything here is made from numbers Switchboard already has on this Mac: Claude Code's history (usage.summarize, the
reader History's Work uses), History (history.jsonl: switches and limits) and the readings log below. No model is
called and nothing leaves the Mac. What it holds: numbers, chat titles, workflow names and phases, account emails and
times; never what a message said.

/api/usage (server.usage_view, the page's "work" payload) gains, beside History's Work's cuts:

  week       {since, until, weight, who {chat, helpers, workflows}}: the week, the 7 days up to now, as the cuts count it
  series     days: 8, the 7 local days before today and today so far, each {day (YYYY-MM-DD), weight,
               who {chat, helpers, workflows}, projects [{label, weight}], models {Fable, Opus, Sonnet, Haiku, Other},
               kinds {build, fix, research, review, docs, tests, other, not labelled}}. projects are the 8 days' five
               biggest (chat titles as History's Work names them; chats with one title are one project), the same five
               in the same order every day, then Other.
             hours: 168, the local hours up to now (the last one so far), each {at (the hour's start, ISO local time
               with its offset), weight, who {chat, helpers, workflows}}. An hour with use also says what it went to:
               projects, models and kinds, each [{label, share}] in whole percents that add to 100, biggest first (the
               hour's three biggest projects, the rest as one 'N more'; kinds named as the page names them); tokens
               {cache_read, cache_write, output, input}: whole percents of the hour's weighted tokens; agents, workflows
               and tools: how many helpers and workflow agents made a request in it, of how many workflow runs, and its
               tool calls; and accounts: the emails Claude Code was on in it, in order, where History says
  last_week  {weight, who, days}: the 7 days before the week, and how many of its days had use (who and days None
             when its read doesn't say); None when it could not be read
  totals     {workflows, agents, helpers}: the week's workflow runs, their agents, and the helpers chats started
  average    {hour, day}: an average hour with use (the hour so far never in it) and an average full day with use (today
             never in it): the one average the chart's line, its multiples and every spike word are measured against
  peak       {hour {at, weight, times}, day {day, weight, times}}: the busiest hour and day against that average
  tokens     {input, output, cache_write, cache_read, cache_write_1h}: the week's (cache_write_1h: the 1-hour part)
  workflows  the week's 10 biggest {name, chat, share (whole percent of the week), weight, agents, phases}
  events     switches and limits reached, from the first day on, oldest first:
               {at, kind ('switch' or 'limit'), from, to, account, why, by, window, pct}
  readings   {email: [{at, five, week, models {name: pct}}]}: the readings log from the first day on
  insights   up to 3 {key, figure, text, numbers}: the figure set big ('43%', '4×'), then the rest of the line

A weight is weighted tokens (usage.WEIGHT_FORMULA), rounded to the hundredth. A day's who, projects, models and kinds
each add up to its weight, the days add up to the requests from the first day on, and the hours to those from the
first hour on.
"""
import bisect
import fcntl
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

from . import core, history, usage

WEEK = 7 * 86400
DAYS = 8                    # the 7 local days before today, and today so far
HOURS = 168                 # the local hours up to now, the last one so far
TOP_PROJECTS = 5
HOUR_PROJECTS = 3           # an hour names its three biggest projects; two or more past them are one "N more"
OTHER = 'Other'
EVENTS_READ = 5000          # History's newest lines read for the events (a week holds far fewer)

WEEKDAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
MORE_RE = re.compile(r'^\d+ more$')


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float('inf')


def _n(v):
    return float(v) if _num(v) else 0.0


def _w(v):
    """A weight as the payload gives it: rounded to the hundredth."""
    return round(_n(v), 2)


def _whole(v):
    """A whole percent, half up."""
    return int(_n(v) + 0.5)


def _text(v):
    return v if isinstance(v, str) and v else None


def iso(t, tz=None):
    """'2026-11-17T23:00:00-08:00': the local time of `t`, to the second, with its offset (tz a tzinfo; None: this
    Mac's local time)."""
    d = datetime.fromtimestamp(t, tz) if tz is not None else datetime.fromtimestamp(t, timezone.utc).astimezone()
    return d.isoformat(timespec='seconds')


def series_start(now, tz=None):
    """Where the series begin: local midnight 7 days before today."""
    first = usage.local_day(now, tz) - timedelta(days=DAYS - 1)
    if tz is not None:
        return datetime(first.year, first.month, first.day, tzinfo=tz).timestamp()
    return time.mktime((first.year, first.month, first.day, 0, 0, 0, 0, 0, -1))


# ---------------------------------------------------------------- the payload

def week(summary, now, tz=None, last=None, lines=(), readings=None):
    """The payload's new parts (all but insights) from usage.summarize's result for the week, called with
    series_since=series_start(now); `last`, the result for the 7 days before it, or None; `lines`, History's lines;
    `readings`, the readings log as read_readings gives it."""
    win = summary.get('window') or {}
    since, until = win.get('since', now - WEEK), win.get('until', now)
    totals = summary.get('totals') or {}
    lines = list(lines or ())
    series = _series(summary.get('series'), now, tz)
    if series:
        starts = [_at(h['at']).timestamp() for h in series['hours']]
        for h, on in zip(series['hours'], accounts_by_hour(lines, starts, now)):
            if on and h['weight'] > 0:
                h['accounts'] = on
    out = {
        'week': {'since': iso(since, tz), 'until': iso(until, tz), 'weight': _w(totals.get('weight')),
                 'who': _who(summary)},
        'series': series,
        'last_week': {'weight': _w((last.get('totals') or {}).get('weight')), 'who': _who(last),
                      'days': _days_used(last)} if isinstance(last, dict) else None,
        'totals': {'workflows': totals.get('workflows') or 0, 'agents': totals.get('workflow_agents') or 0,
                   'helpers': totals.get('helper_agents') or 0},
        'tokens': dict(totals.get('tokens') or {}),
        'workflows': [{'name': x.get('name'), 'chat': x.get('chat'), 'share': _whole(100 * _n(x.get('share'))),
                       'weight': _w(x.get('weight')), 'agents': x.get('agents') or 0, 'phases': list(x.get('phases') or [])}
                      for x in (summary.get('workflows') or [])[:10]],
        'events': events(lines, series_start(now, tz), now, tz),
        'readings': readings_view(readings or {}, tz),
    }
    out.update(week_numbers({'series': series}))
    return out


def _who(summary):
    """{chat, helpers, workflows}: a summary's weight by who did it; None when it doesn't say."""
    rows = (summary or {}).get('who')
    if not isinstance(rows, list):
        return None
    by = dict((usage.WHO_LABEL[k], k) for k in usage.WHO)
    out = dict.fromkeys(usage.WHO, 0.0)
    for r in rows:
        if isinstance(r, dict) and r.get('label') in by:
            out[by[r['label']]] = _w((r.get('detail') or {}).get('weight'))
    return out


def _days_used(summary):
    """How many local days of a summary had use; None when it doesn't say."""
    rows = (summary or {}).get('days')
    if not isinstance(rows, list):
        return None
    return len([d for d in rows if isinstance(d, dict) and _n(d.get('weight')) > 0])


def week_numbers(page):
    """{average, peak} from the page's series: the one average the chart and the words are measured against."""
    s = page.get('series') or {}
    hours = [h for h in s.get('hours') or [] if isinstance(h, dict)]
    days = [d for d in s.get('days') or [] if isinstance(d, dict)]
    out = {'average': {'hour': None, 'day': None}, 'peak': {'hour': None, 'day': None}}
    for what, items, key, floor in (('hour', hours, 'at', MIN_HOURS), ('day', days, 'day', MIN_DAYS)):
        used = [_n(x.get('weight')) for x in items[:-1] if _n(x.get('weight')) > 0]
        top = max(items, key=lambda x: _n(x.get('weight'))) if items else None
        if not used or top is None or _n(top.get('weight')) <= 0:
            continue
        avg = sum(used) / len(used)
        out['average'][what] = round(avg, 2)
        if len(used) >= floor:                  # spike()'s floor: a busiest hour or day is named only past it
            out['peak'][what] = {key: top.get(key), 'weight': _n(top.get('weight')),
                                 'times': round(_n(top.get('weight')) / avg, 1)}
    return out


def _parts(d, names):
    return dict((x, _w((d or {}).get(x))) for x in names)


def _series(s, now, tz):
    """The 8 days and 168 hours from the summary's series (None when the summary has none)."""
    if not isinstance(s, dict):
        return None
    have = dict((d.get('day'), d) for d in s.get('days') or [] if isinstance(d, dict))
    today = usage.local_day(now, tz)
    wanted = [(today - timedelta(days=DAYS - 1 - i)).strftime('%Y-%m-%d') for i in range(DAYS)]
    totals = {}
    for name in wanted:
        for label, x in ((have.get(name) or {}).get('projects') or {}).items():
            totals[label] = totals.get(label, 0.0) + _n(x)
    top = [k for k in sorted(totals, key=lambda k: (-totals[k], k)) if k != OTHER and totals[k] > 0][:TOP_PROJECTS]
    days = []
    for name in wanted:
        d = have.get(name) or {}
        projects = d.get('projects') or {}
        named = [{'label': k, 'weight': _w(projects.get(k))} for k in top]
        named.append({'label': OTHER, 'weight': _w(sum(_n(x) for k, x in projects.items() if k not in top))})
        days.append({'day': name, 'weight': _w(d.get('weight')), 'who': _parts(d.get('who'), usage.WHO),
                     'projects': named, 'models': _parts(d.get('models'), usage.FAMILIES),
                     'kinds': _parts(d.get('kinds'), usage.KINDS)})
    # The hours: the 168 up to the one now is in. Each of the summary's hours goes to the one it starts in (the same
    # hour in every whole-hour or half-hour zone).
    starts = [usage.hour_start(now, tz) - (HOURS - 1 - i) * 3600 for i in range(HOURS)]
    slots = [[0.0, dict.fromkeys(usage.WHO, 0.0), None] for _ in starts]
    for h in s.get('hours') or []:
        t = h.get('t') if isinstance(h, dict) else None
        if not _num(t) or t < starts[0]:
            continue
        slot = slots[bisect.bisect_right(starts, t) - 1]
        slot[0] += _n(h.get('weight'))
        for k in usage.WHO:
            slot[1][k] += _n((h.get('who') or {}).get(k))
        if isinstance(h.get('projects'), dict):          # a reader that says where the hour went
            slot[2] = _went(slot[2], h)
    hours = []
    for t, (w_, who, went) in zip(starts, slots):
        row = {'at': iso(t, tz), 'weight': _w(w_), 'who': _parts(who, usage.WHO)}
        if went is not None and row['weight'] > 0:
            row.update(_hour(went))
        hours.append(row)
    return {'days': days, 'hours': hours}


def _went(have, h):
    """Where an hour went, as the reader gives it, added to what its slot already holds (two of the reader's hours
    share a slot only where the clocks moved by part of an hour)."""
    if have is None:
        have = {'projects': {}, 'models': {}, 'kinds': {}, 'tokens': dict.fromkeys(usage.TOKENS, 0.0), 'tools': 0,
                'agents': set(), 'runs': set()}
    for part in ('projects', 'models', 'kinds', 'tokens'):
        got = h.get(part)
        for k, v in (got.items() if isinstance(got, dict) else ()):
            if isinstance(k, str):
                have[part][k] = have[part].get(k, 0.0) + _n(v)
    have['tools'] += int(_n(h.get('tools')))
    for part in ('agents', 'runs'):
        got = h.get(part)
        have[part].update(x for x in (got if isinstance(got, (list, tuple)) else ()) if isinstance(x, str))
    return have


def _shares(items, top=None):
    """[{label, share}] from [(label, weight)]: the heaviest first (the order given breaks a tie), in whole percents
    that add to 100, a row that comes to 0% left out. With `top`, two or more rows past it are one 'N more' row, last
    whatever it weighs."""
    items = sorted(((k, _n(v)) for k, v in items if _n(v) > 0), key=lambda x: -x[1])
    if top is not None and len(items) > top + 1:
        rest = items[top:]
        items = items[:top] + [('%d more' % len(rest), sum(v for _, v in rest))]
    return [{'label': k, 'share': p} for (k, _), p in zip(items, usage.whole_percents([v for _, v in items])) if p > 0]


def _hour(went):
    """What an hour with use adds to its row on the page (see the notes at the top)."""
    weighted = usage.weighted(went['tokens'])
    kinds = usage.TOKEN_KINDS
    tokens = dict(zip(kinds, usage.whole_percents([weighted[k] for k in kinds])))
    return {'projects': _shares(sorted(went['projects'].items()), HOUR_PROJECTS),
            'models': _shares((f, went['models'].get(f)) for f in usage.FAMILIES),
            'kinds': _shares((usage.PAGE_KIND.get(k, k), went['kinds'].get(k)) for k in usage.KINDS),
            'tokens': tokens, 'agents': len(went['agents']), 'workflows': len(went['runs']), 'tools': went['tools']}


def accounts_by_hour(lines, starts, until):
    """For each hour (its start; it ends at the next one's start, the last at `until`): the accounts Claude Code was on
    in it, oldest first, from History's switches. An hour that ends before History's first line says nothing: what
    Claude Code was on before Switchboard kept a History is not known. The hour that line is in says what is known of
    it from then on. Two spellings of one email that differ only in case are one account."""
    switches, first = [], None
    for x in lines or ():
        t = x.get('t') if isinstance(x, dict) else None
        if not _num(t):
            continue
        first = t if first is None else min(first, t)
        if x.get('kind') == 'switch':
            to = _text(x.get('to')) or _text(x.get('account'))
            if to:
                switches.append((t, _text(x.get('from')), to))
    switches.sort(key=lambda sw: sw[0])
    times = [sw[0] for sw in switches]
    out = []
    for i, a in enumerate(starts):
        b = starts[i + 1] if i + 1 < len(starts) else until
        if first is None or b <= first or not switches:
            out.append([])
            continue
        j = bisect.bisect_right(times, a)               # the switches up to the hour's start
        on = [switches[j - 1][2]] if j else ([switches[0][1]] if switches[0][1] else [])
        for _, _, to in switches[j:bisect.bisect_left(times, b)]:
            if not on or on[-1].lower() != to.lower():
                on.append(to)
        out.append(on)
    return out


def events(lines, since, until, tz=None):
    """History's switches and limits reached (not refills) from `since` to `until`, oldest first."""
    out = []
    for x in lines or ():
        t = x.get('t') if isinstance(x, dict) else None
        kind = x.get('kind') if isinstance(x, dict) else None
        if not _num(t) or not since <= t < until:
            continue
        if not (kind == 'switch' or (kind == 'limit' and _num(x.get('pct')))):
            continue
        account = _text(x.get('account')) or (_text(x.get('to')) if kind == 'switch' else None)
        out.append((t, {'at': iso(t, tz), 'kind': kind, 'from': _text(x.get('from')), 'to': _text(x.get('to')),
                        'account': account, 'why': _text(x.get('reason')), 'by': _text(x.get('by')),
                        'window': _text(x.get('window')), 'pct': x.get('pct') if _num(x.get('pct')) else None}))
    out.sort(key=lambda p: p[0])
    return [e for _, e in out]


def read_events(since, until, tz=None):
    """events() over History's newest lines. [] when History can't be read."""
    try:
        return events(history.read(EVENTS_READ), since, until, tz)
    except Exception as e:  # noqa: BLE001 - the week shows without its events
        core.log('reading History for the week failed: %r' % e)
        return []


def read_week(summary, now, page=None):
    """For server.read_usage: the payload's new parts and the insights, with the week before read through
    usage.summarize and the same cache (a second read about as long as the week's own), History's events and the
    readings log. Never raises: a part that can't be read is left None or empty, and the rest still shows."""
    try:
        since = (summary.get('window') or {}).get('since', now - WEEK)
        last = None
        try:
            last = usage.summarize(core.projects(), since - WEEK, since, cache_path=core.data_path('usage-cache.json'))
        except Exception as e:  # noqa: BLE001
            core.log('reading the week before failed: %r' % e)
        start = series_start(now)
        try:
            lines = history.read(EVENTS_READ)
        except Exception as e:  # noqa: BLE001
            core.log('reading History for the week failed: %r' % e)
            lines = []
        out = week(summary, now, last=last, lines=lines, readings=read_readings(start, now))
        out['insights'] = find(dict(page or {}, **out))
        return out
    except Exception as e:  # noqa: BLE001 - the cuts still show
        core.log('the week was not made: %r' % e)
        return {}


# ---------------------------------------------------------------- the insights
#
# Up to three highlights under the chart, each a figure set big ('43%', '4×') and the rest of its line. Each rule looks
# at one thing and says it only past its own bar; the ones that pass are kept in the fixed order below (never a score,
# so two reads of the same week give the same three in the same order), and the first three are shown. The chart names
# the week's spike in place (week_numbers), so the highlights never repeat it, and the legend gives who did the work.
# Words are fixed; the numbers, chat titles, emails and times come from the page.
#
#   rule        what                                                       bar
#   week        this week against last week, and the part that explains    +-15% (a sixth more or less), last week
#               most of the change ("mostly helpers")                      with use on 3 days or more (that part: the
#                                                                          biggest over half the change, same way)
#   moved       helpers and workflow steps that ran on their Opus or Fable 20% (`moved`: models.would_have_moved)
#               chat's model because they chose no model
#   limit       an account's switch line, and the auto switch it caused    any, in the week (the latest)
#   project     the biggest project's share of the week                    40%
#   context     re-reading context (cache reads), with storing it (cache   30% of the use
#               writes) and Claude's replies (output) beside it. Last: it
#               passes in nearly every week, so it never crowds out a
#               week's own news
#
# The spike (hour, else day: 2x an average one with use) is kept for `numbers` and older callers as spike(); it is never
# a highlight. The chart names its busiest hour in place from 1.5x (NAME), past the same floor.

SPIKE, MIN_HOURS, MIN_DAYS = 2.0, 6, 3
NAME = 1.5                  # the chart names its busiest hour (week_numbers' peak) at this or more
PROJECT, CONTEXT, MOVED, CHANGE = 40, 30, 20, 0.15
LAST_DAYS = 3               # last week needs this many days with use before this week is measured against it
PAIR = 15 * 60              # a switch is a limit's when it is this close to it, either side
TITLE_MAX = 48
MOSTLY = {'workflows': 'workflows', 'helpers': 'helpers', 'chat': 'your own turns'}


def find(page):
    """Up to 3 {key, figure, text, numbers} for the page, in the rules' fixed order. Made only from the page's numbers."""
    found = []
    for rule in (_week, _moved, _limit, _project, _context):
        try:
            got = rule(page or {})
        except (AttributeError, KeyError, TypeError, ValueError, ZeroDivisionError):
            got = None
        if got:
            found.append(got)
    return [{'key': k, 'figure': f, 'text': t, 'numbers': nums} for k, f, t, nums in found[:3]]


def _when(t, today):
    """'Tuesday 11 PM' for an hour in the 6 days before today or today; 'Nov 11, 4 PM' before that."""
    h = t.hour
    hour = '12 AM' if h == 0 else '12 PM' if h == 12 else '%d AM' % h if h < 12 else '%d PM' % (h - 12)
    return '%s %s' % (_day(t.date(), today), hour) if 0 <= (today - t.date()).days <= 6 else \
        '%s, %s' % (_day(t.date(), today), hour)


def _day(d, today):
    return WEEKDAYS[d.weekday()] if 0 <= (today - d).days <= 6 else '%s %d' % (MONTHS[d.month - 1], d.day)


def _times(r):
    return ('%d×' % int(r + 0.5)) if r >= 3 else ('%s×' % ('%.1f' % r).rstrip('0').rstrip('.'))


def _at(text):
    return datetime.fromisoformat(text)


def spike(page):
    """(key, words, numbers) for the busiest hour, else day, at 2x an average one with use or more; None below it.
    The page names it on the chart from week_numbers; this is the same rule, said in words."""
    s = page.get('series') or {}
    hours = [h for h in s.get('hours') or [] if isinstance(h, dict)]
    used = [_n(h.get('weight')) for h in hours[:-1] if _n(h.get('weight')) > 0]
    if len(used) >= MIN_HOURS:
        avg = sum(used) / len(used)
        peak = max(hours, key=lambda h: _n(h.get('weight')))
        times = _n(peak.get('weight')) / avg
        if times >= SPIKE:
            when = _at(peak['at'])
            text = '%s was your busiest hour: %s an average one.' % (_when(when, _at(hours[-1]['at']).date()),
                                                                     _times(times))
            return 'hour', text, {'at': peak['at'], 'weight': _n(peak.get('weight')), 'average': round(avg, 2),
                                  'times': round(times, 1)}
    days = [d for d in s.get('days') or [] if isinstance(d, dict)]
    used = [_n(d.get('weight')) for d in days[:-1] if _n(d.get('weight')) > 0]
    if len(used) >= MIN_DAYS:
        avg = sum(used) / len(used)
        peak = max(days, key=lambda d: _n(d.get('weight')))
        times = _n(peak.get('weight')) / avg
        if times >= SPIKE:
            today = datetime.strptime(days[-1]['day'], '%Y-%m-%d').date()
            day = datetime.strptime(peak['day'], '%Y-%m-%d').date()
            text = '%s was your busiest day: %s an average one.' % (_day(day, today), _times(times))
            return 'day', text, {'day': peak['day'], 'weight': _n(peak.get('weight')), 'average': round(avg, 2),
                                 'times': round(times, 1)}
    return None


def _week(page):
    wk, lw = page.get('week') or {}, page.get('last_week') or {}
    this, last = wk.get('weight'), lw.get('weight')
    if not _num(this) or not _num(last) or last <= 0:
        return None
    if _num(lw.get('days')) and lw['days'] < LAST_DAYS:
        return None                             # a day or two of last week is no week to measure against
    change = (this - last) / float(last)
    if abs(change) < CHANGE - 1e-9:
        return None
    pct = int(abs(change) * 100 + 0.5)
    mostly = None
    now, then = wk.get('who'), lw.get('who')
    if isinstance(now, dict) and isinstance(then, dict):
        net = this - last
        best = 0.0
        for k in ('workflows', 'helpers', 'chat'):      # the biggest part that passes; a tie keeps the first
            d = _n(now.get(k)) - _n(then.get(k))
            if d * net > 0 and abs(d) > 0.5 * abs(net) and abs(d) > best:
                mostly, best = k, abs(d)
    tail = ', mostly %s.' % MOSTLY[mostly] if mostly else '.'
    if change >= 1:
        figure, text = _times(this / float(last)), 'last week’s use' + tail
    else:
        figure, text = '%d%%' % pct, '%s than last week%s' % ('more' if change > 0 else 'less', tail)
    return 'week', figure, text, {'change': pct if change > 0 else -pct, 'weight': this, 'last_week': last,
                                  'mostly': mostly}


def _moved(page):
    m = page.get('moved')
    if not isinstance(m, dict) or not _num(m.get('share')) or m['share'] < MOVED:
        return None
    fams = [f for f in m.get('from') or [] if isinstance(f, str) and f]
    ran = ' and '.join(fams) if fams else 'their chat’s model'
    return ('moved', '%d%%' % m['share'],
            'of the week was helpers and workflow steps that ran on %s because they chose no model.' % ran,
            {'share': m['share'], 'from': fams})


def _context(page):
    t = page.get('tokens') or {}
    w = usage.weighted(dict((k, _n(t.get(k))) for k in usage.TOKENS))
    parts = [w[k] for k in usage.TOKEN_KINDS]
    if sum(parts) <= 0:
        return None
    pct = dict(zip(usage.TOKEN_KINDS, usage.whole_percents(parts)))
    if pct['cache_read'] < CONTEXT:
        return None
    return ('context', '%d%%' % pct['cache_read'],
            'of the use was re-reading context. Storing it was %d%%, Claude’s replies %d%%.' % (pct['cache_write'],
                                                                                                 pct['output']),
            {'cache_read': pct['cache_read'], 'cache_write': pct['cache_write'], 'output': pct['output'],
             'input': pct['input']})


def _project(page):
    shares, order = {}, []
    for r in (page.get('cuts') or {}).get('work') or []:
        label = r.get('label')
        if not isinstance(label, str) or MORE_RE.match(label):
            continue
        if label not in shares:
            order.append(label)
        shares[label] = shares.get(label, 0) + int(_n(r.get('share')))     # chats with one title are one project
    if not order:
        return None
    label = max(order, key=lambda k: shares[k])
    p = shares[label]
    if p < PROJECT:
        return None
    title = label if len(label) <= TITLE_MAX else label[:TITLE_MAX - 1] + '…'
    return 'project', '%d%%' % p, 'of the week went to %s.' % title, {'label': label, 'share': p}


def _limit(page):
    wk = page.get('week') or {}
    since = _at(wk['since']).timestamp() if isinstance(wk.get('since'), str) else None
    until = _at(wk['until']).timestamp() if isinstance(wk.get('until'), str) else None
    evs = [(_at(e['at']).timestamp(), e) for e in page.get('events') or [] if isinstance(e, dict)
           and isinstance(e.get('at'), str)]
    limits = [(t, e) for t, e in evs if e.get('kind') == 'limit' and e.get('account') and _num(e.get('pct'))]
    pairs = []
    for t, e in evs:
        if e.get('kind') != 'switch' or e.get('by') != 'auto' or not e.get('from') or not e.get('to'):
            continue
        if (since is not None and t < since) or (until is not None and t > until):
            continue
        hit = [(abs(lt - t), lt, lim) for lt, lim in limits if lim['account'] == e['from'] and abs(lt - t) <= PAIR]
        if hit:
            pairs.append((t, e, min(hit, key=lambda x: (x[0], x[1]))[2]))
    if not pairs:
        return None
    t, e, lim = max(pairs, key=lambda x: x[0])
    today = _at(wk['until']).date() if until is not None else _at(e['at']).date()
    window = lim.get('window')
    pct = _pct(lim.get('pct'))
    # the figure is the reading when the line was reached: "95% of the weekly limit on <account>"
    text = 'of %s limit on %s, %s. Auto-switch moved you to %s.' % ('the %s' % window if window else 'its', e['from'],
                                                                    _when(_at(e['at']), today), e['to'])
    return 'limit', '%s%%' % pct, text, {'at': e['at'], 'window': window, 'pct': lim.get('pct'), 'from': e['from'],
                                         'to': e['to'], 'count': len(pairs)}


# ---------------------------------------------------------------- the readings log
#
# ~/.switchboard/readings.jsonl keeps each account's limit readings over time, so the page can draw them over the
# week's work. One line per reading, {t, account, five, week, models {name: pct}}, written from the page's tick (the
# place it takes in a new reading) and only when a number changed since that account's last line. Only you can read
# it (0600). Each tick's lines go in one append; the file is rewritten whole (temp file, then rename) when it is
# pruned: lines older than 8 days go every few hours, and past READINGS_CAP bytes only the newest half of the cap
# stays. A line that can't be read is skipped, and goes at the next prune. Writing never waits (another writer
# holding the log means this tick's lines are written at the next) and never raises. `uninstall --all` removes it
# with ~/.switchboard.

READINGS_FILE = 'readings.jsonl'
READINGS_KEEP = 8 * 86400
READINGS_CAP = 1000000
READINGS_PRUNE_EVERY = 6 * 3600
READINGS_SLOT = 900         # the page gets the last reading in each quarter hour, and each one before a drop
READINGS = {'last': {}, 'loaded': False, 'pruned_at': None}     # each account's last line, as written


def _pct(v):
    if not _num(v):
        return None
    r = round(float(v), 1)
    return int(r) if r.is_integer() else r


def take_readings(st):
    """[(email, at, five, week, {model: pct})] for each account with numbers, from the page's state as the tick has
    just read it (called inside the state's lock; nothing is written here, and it never raises)."""
    try:
        return _take(st)
    except Exception:  # noqa: BLE001 - an odd state: no readings this tick
        return []


def _take(st):
    accounts = st.get('accounts') or {}
    order = [e for e in st.get('order') or [] if e in accounts]
    out = []
    for email in order + sorted(set(accounts) - set(order)):
        a = accounts.get(email) or {}
        at, u = a.get('usage_at'), a.get('usage')
        if not _num(at) or not isinstance(u, dict):
            continue
        five = _pct(u['session'].get('pct')) if isinstance(u.get('session'), dict) else None
        wk = _pct(u['weekly'].get('pct')) if isinstance(u.get('weekly'), dict) else None
        models = dict((m['name'].strip(), _pct(m.get('pct'))) for m in u.get('scoped') or []
                      if isinstance(m, dict) and isinstance(m.get('name'), str) and m['name'].strip()
                      and _pct(m.get('pct')) is not None)
        if five is None and wk is None and not models:
            continue
        out.append((email, float(at), five, wk, models))
    return out


def _key(row):
    return (row['five'], row['week'], tuple(sorted(row['models'].items())))


def _row(raw):
    """One line of the log, or None when it can't be read as one."""
    try:
        x = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(x, dict) or not _num(x.get('t')) or not isinstance(x.get('account'), str) or not x['account']:
        return None
    models = x.get('models') if isinstance(x.get('models'), dict) else {}
    return {'t': x['t'], 'account': x['account'], 'five': _pct(x.get('five')), 'week': _pct(x.get('week')),
            'models': dict((k, _pct(v)) for k, v in sorted(models.items()) if isinstance(k, str) and _pct(v) is not None)}


def _lines(path):
    """The log's complete lines (a last one with no newline may still be being written), or [] when there is none."""
    try:
        with open(path, 'rb') as f:
            raw = f.read()
    except FileNotFoundError:
        return []
    return raw.split(b'\n')[:-1]


def note_readings(took, now=None):
    """Append a line for each account whose numbers changed since its last line (take_readings gives `took`).
    Returns how many lines were written. Never raises, and never waits."""
    if core.DEMO or not took:
        return 0
    now = time.time() if now is None else now
    try:
        return _note(took, now)
    except Exception as e:  # noqa: BLE001 - the log only draws the page's lines: the page goes on without it
        try:
            core.log('the readings log was not written: %r' % e)
        except Exception:  # noqa: BLE001
            pass
        return 0


def _note(took, now):
    path = core.data_path(READINGS_FILE)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    lock = os.open(path + '.lock', os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 0                            # another writer has it: these lines go at the next tick
        if not READINGS['loaded']:
            last = {}
            for raw in _lines(path):
                row = _row(raw)
                if row is not None:
                    last[row['account']] = _key(row)
            READINGS.update(last=last, loaded=True)
        cur, lines = dict(READINGS['last']), []
        for email, at, five, wk, models in took:
            if not isinstance(email, str) or not email or not _num(at):
                continue
            row = {'t': int(at) if float(at).is_integer() else round(at, 3), 'account': email, 'five': _pct(five),
                   'week': _pct(wk), 'models': dict((k, _pct(v)) for k, v in sorted((models or {}).items())
                                                   if _pct(v) is not None)}
            if cur.get(email) == _key(row):
                continue
            cur[email] = _key(row)
            lines.append(json.dumps(row, separators=(',', ':'), ensure_ascii=False))
        size = _append(path, lines) if lines else (os.path.getsize(path) if os.path.exists(path) else 0)
        READINGS['last'] = cur
        due = READINGS['pruned_at'] is None or not 0 <= now - READINGS['pruned_at'] < READINGS_PRUNE_EVERY
        if size > READINGS_CAP or (due and size):
            _prune(path, now)
        return len(lines)
    finally:
        os.close(lock)


def _append(path, lines):
    """The lines in one write at the end of the log (0600). A last line cut short (a crash mid-write) is ended first,
    so it never runs into these. Returns the log's size."""
    data = ('\n'.join(lines) + '\n').encode('utf-8')
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        st = os.fstat(fd)
        if st.st_mode & 0o077:
            os.fchmod(fd, 0o600)
        if st.st_size and os.pread(fd, 1, st.st_size - 1) != b'\n':
            data = b'\n' + data
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        return os.fstat(fd).st_size
    finally:
        os.close(fd)


def _prune(path, now):
    """Keep the readable lines of the last 8 days; past three quarters of the cap, only the newest that fit in half."""
    keep = []
    for raw in _lines(path):
        row = _row(raw)
        if row is not None and row['t'] >= now - READINGS_KEEP:
            keep.append(raw)
    if sum(len(x) + 1 for x in keep) > READINGS_CAP * 3 // 4:
        kept, size = [], 0
        for raw in reversed(keep):
            if size + len(raw) + 1 > READINGS_CAP // 2:
                break
            kept.append(raw)
            size += len(raw) + 1
        keep = kept[::-1]
    core.write_bytes(path, b''.join(raw + b'\n' for raw in keep))
    READINGS['pruned_at'] = now


def read_readings(since, until=None):
    """{email: [{t, five, week, models}]} from the log, oldest first: the lines from `since` (to `until`). Lines that
    can't be read are left out. {} when there is no log or it can't be read."""
    try:
        raws = _lines(core.data_path(READINGS_FILE))
    except (OSError, RuntimeError):
        return {}
    out = {}
    for raw in raws:
        row = _row(raw)
        if row is None or row['t'] < since or (until is not None and row['t'] > until):
            continue
        out.setdefault(row.pop('account'), []).append(row)
    for rows in out.values():
        rows.sort(key=lambda r: r['t'])
    return out


def _drops(a, b):
    """b reads lower than a: a window refilled between them, so a was its peak."""
    pairs = [(a.get(k), b.get(k)) for k in ('five', 'week')]
    pairs += [(v, (b.get('models') or {}).get(k)) for k, v in (a.get('models') or {}).items()]
    return any(_num(x) and _num(y) and y < x for x, y in pairs)


def readings_view(by_email, tz=None):
    """What the page gets: each account's readings, oldest first, the last in each quarter hour and each one before
    a drop (a window's peak), as {at, five, week, models}."""
    out = {}
    for email in sorted(by_email):
        rows = sorted((r for r in by_email[email] or [] if isinstance(r, dict) and _num(r.get('t'))),
                      key=lambda r: r['t'])
        kept = []
        for i, r in enumerate(rows):
            nxt = rows[i + 1] if i + 1 < len(rows) else None
            if nxt is None or int(nxt['t'] // READINGS_SLOT) != int(r['t'] // READINGS_SLOT) or _drops(r, nxt):
                kept.append({'at': iso(r['t'], tz), 'five': r.get('five'), 'week': r.get('week'),
                             'models': dict(r.get('models') or {})})
        if kept:
            out[email] = kept
    return out
