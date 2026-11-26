"""`switchboard auto check` and `switchboard auto why`: one pass of auto-switch now, from a terminal or a scheduled job,
and what it would do and why.

    switchboard auto check [--dry-run] [--json] [--five-hour N] [--week N] [--order WORD]
    switchboard auto why [--json] [--five-hour N] [--week N] [--order WORD]

A check is the background loop's own pass (autoloop.Loop.run_pass), so it decides as the loop does: the same policy,
settings, account rules, locks and reading pace (the pace is kept in usage.json, so a check right after another reads
nothing new and says how old its numbers are). It holds the loop's lease while it runs, so there are never two
switchers: while the loop holds it, a check moves nothing, says the loop's last decision, and exits 5. With
auto-switch off a check still runs, deciding as if it were on (autoloop.ONE_CHECK); it never turns auto-switch on and
stores no setting. --five-hour, --week and --order change the lines and the order for this one check, checked as the
page checks them (engine.check_line, engine.STRATEGIES). A move it makes goes through the account engine like any
other, with source 'check' and a reason that starts "auto check: ", so History and `switchboard log` say where it came
from. Its lines in auto.log start "check: ".

A dry run (`--dry-run`, or `why`) decides on what is on disk now, as autoloop.gather sees it: the numbers last read
(it sends no read to Anthropic, since a read writes usage.json), the live login, Claude Code's activity, the load and
the battery. It never switches and never writes a file, so it answers whether the loop runs or not. It exits as the
check would (0 for a move it would make).

Exit codes are the loop's own: 0 it moved Claude Code, 1 it failed or could not look (no_look), 3 it wanted to move
and was held back or had nowhere to go, 4 nothing to do, 5 the loop holds the lease. 2: the command line was wrong.

Every reason it gives comes from policy: its decision, its views of the accounts, and its answer again for one account
(policy.need) or one hold (policy._hold) when a reason is needed on its own.
"""
import dataclasses
import json
import sys
import time
from contextlib import contextmanager

from . import core, engine, names
from .accounts import autoloop, policy, usage

SCHEMA = 1
FORMS = ('switchboard auto check [--dry-run] [--json] [--five-hour N] [--week N] [--order WORD]',
         'switchboard auto why [--json] [--five-hour N] [--week N] [--order WORD]')
ORDERS = '%s or %s' % (', '.join(engine.STRATEGIES[:-1]), engine.STRATEGIES[-1])    # 'a or b', 'a, b or c'
FLAGS = (('--five-hour', 'five_hour', 'session_at', 'five_hour_line'), ('--week', 'week', 'weekly_at', 'week_line'),
         ('--order', 'order', 'strategy', 'order'))         # flag, option, policy.Settings field, the document's name
STATUS_WORDS = {'unreadable': "its saved login can't be read right now",
                'conflict': 'Anthropic said its saved login belongs to another account'}
NOT_MOVED = ('upkeep:', 'pass ', 'kept:', 'journal')    # loop lines that never say why a wanted move did not happen


class Wrong(Exception):
    """The command line was wrong; str() is the sentence that says so."""


# ---------- the command line ----------

def parse(args):
    """{'word', 'dry_run', 'json', 'five_hour', 'week', 'order'} from the words after `switchboard auto`. A flag's value
    is the next word or follows '='. Raises Wrong."""
    word, rest = args[0], list(args[1:])
    opts = {'word': word, 'dry_run': word == 'why', 'json': False, 'five_hour': None, 'week': None, 'order': None}
    flags = dict((f[0], f[1]) for f in FLAGS)
    i = 0
    while i < len(rest):
        a = rest[i]
        name, eq, value = a.partition('=')
        if a == '--dry-run':
            opts['dry_run'] = True
        elif a == '--json':
            opts['json'] = True
        elif name in flags:
            if not eq:
                if i + 1 >= len(rest):
                    raise Wrong('%s needs %s.' % (name, ORDERS if name == '--order' else
                                                  'a number'))
                i += 1
                value = rest[i]
            opts[flags[name]] = _order(value) if name == '--order' else _line(value, name)
        else:
            raise Wrong('Unknown word: %s.' % a)
        i += 1
    return opts


def _line(text, flag):
    """A switch line as typed, checked as the page checks one (engine.check_line: 50 to 99.9, never clamped)."""
    try:
        return engine.check_line(float(text))
    except (ValueError, core.SwitchError):
        raise Wrong(line_words(flag))


def line_words(name):
    """What a terminal says for a line that is not one, naming the flag or the setting it was for. One sentence
    everywhere: `auto check`, `auto line` and `config set`."""
    return '%s takes a number from %g to %g.' % (name, engine.LINE_MIN, engine.LINE_MAX)


def _order(text):
    if text not in engine.STRATEGIES:
        raise Wrong('--order takes %s.' % ORDERS)
    return text


def check_settings(opts, stored):
    """The settings this check decides on: the stored ones, turned on, with this check's lines and order. Never
    written."""
    change = dict((field, opts[key]) for _, key, field, _ in FLAGS if opts.get(key) is not None)
    return dataclasses.replace(stored, enabled=True, **change)


@contextmanager
def deciding_on(settings):
    """While the block runs, every settings read of the loop in this process (autoloop.load_settings) gives
    `settings`."""
    autoloop.ONE_CHECK.append(settings)
    try:
        yield
    finally:
        autoloop.ONE_CHECK.remove(settings)


# ---------- one pass of the loop ----------

class CheckLog(autoloop.AutoLog):
    """The loop's log, each line marked as a check's, and kept here too to say why a wanted move did not happen."""

    def __init__(self, path=None, clock=None):
        autoloop.AutoLog.__init__(self, path, clock)
        self.said = []

    def line(self, msg):
        self.said.append(msg)
        autoloop.AutoLog.line(self, 'check: %s' % msg)


class CheckLoop(autoloop.Loop):
    """The loop, for one pass: it keeps the decision it acted on and the snapshot behind it, to explain them."""
    decided = None

    def _all_out(self, decision, snap):
        self.decided = (decision, snap)
        return autoloop.Loop._all_out(self, decision, snap)


def _from_check(switch, tried):
    """deps.switch for a check: the account engine's switch with source 'check' and its reason marked, noting each
    target it was asked for."""
    def run(target, kind='manual', source='check', reason=''):
        tried.append(target)
        return switch(target, kind=kind, source='check', reason='auto check: %s' % reason)
    return run


def one_pass(opts, deps, clock):
    """(exit code, document) of one real check."""
    other = engine.foreign_switcher()
    if other:
        return autoloop.EXIT_ERROR, refused(opts, engine.GUARD % other)
    stored = autoloop.load_settings()
    settings = check_settings(opts, stored)
    deps = deps or autoloop.Deps()
    tried = []
    deps.switch = _from_check(deps.switch, tried)
    log = CheckLog(clock=clock)
    loop = CheckLoop(deps=deps, clock=clock, log=log)
    if not loop.lease.acquire(loop.clock):
        return autoloop.EXIT_BUSY, busy(opts, loop.clock)
    try:
        with deciding_on(settings):
            loop.start()
            try:
                res = loop.run_pass()
            except Exception as e:  # noqa: BLE001 - one sentence; the loop's log has the rest
                log.line('the check did not finish: %s' % autoloop._err_words(e))
                return autoloop.EXIT_ERROR, refused(opts, 'The check did not finish (%s).' % type(e).__name__)
    finally:
        loop.hb.update(stage='stopped', words=autoloop.STAGE_WORDS['stopped'], wall=round(loop.clock.wall(), 3),
                       mono=round(loop.clock.mono(), 3))
        loop.lease.release()
    decision, snap = loop.decided if loop.decided else (res.decision, None)
    if res.switched is not None:
        code, outcome, why = autoloop.EXIT_SWITCHED, 'switched', None
    elif decision.action == 'switch':
        why = next((x for x in reversed(log.said) if not x.startswith(NOT_MOVED)), None)
        code, outcome = (autoloop.EXIT_ERROR, 'failed') if tried else (autoloop.EXIT_BLOCKED, 'blocked')
    else:
        code, outcome, why = _stay_code(decision, snap) + (None,)
    doc = document(opts, decision, snap, stored, settings, outcome, code)
    if why:
        doc['problem'] = 'It did not move: %s.' % why.rstrip('.')
    elif outcome == 'failed':
        doc['problem'] = no_look(snap, decision)
    return code, doc


def dry_run(opts, deps, clock):
    """(exit code, document) of a dry run: the snapshot autoloop.gather makes from what is on disk now (no read is
    sent, nothing is written), with the battery as a pass reads it, decided on with this check's settings."""
    stored = autoloop.load_settings()
    deps = deps or autoloop.Deps()
    snap = autoloop.gather(deps, clock)
    on_ac, pct = autoloop._call(deps.power, default=(None, None)) or (None, None)
    snap = dataclasses.replace(snap, settings=check_settings(opts, stored), on_ac=on_ac, battery_pct=pct)
    return judge(opts, snap, stored)


def judge(opts, snap, stored):
    """(exit code, document) for policy.decide's answer on `snap`, said as a dry run says it."""
    decision = policy.decide(snap)
    if decision.action == 'switch':
        code, outcome = autoloop.EXIT_SWITCHED, 'would_switch'
    else:
        code, outcome = _stay_code(decision, snap)
    doc = document(opts, decision, snap, stored, snap.settings, outcome, code)
    if outcome == 'failed':
        doc['problem'] = no_look(snap, decision)
    return code, doc


def no_look(snap, decision):
    """The sentence for a stay because the check could not look (policy.decide's first stays, in its order): Claude
    Code's login could not be read, or, on a saved account, the Mac just woke or is offline. None for any other."""
    if snap is None or decision.action != 'stay' or decision.kind is not None or not snap.settings.enabled:
        return None
    if not snap.live_ok:
        return 'Nothing was checked: %s.' % (snap.live_why or "Claude Code's login can't be read right now").rstrip('.')
    if snap.active not in set(a.id for a in snap.accounts):
        return None
    if snap.settling:
        return 'Nothing was checked: the Mac just woke, and the network is not back yet.'
    if snap.offline:
        return 'Nothing was checked: there is no connection to Anthropic.'
    return None


def _stay_code(decision, snap=None):
    if no_look(snap, decision):
        return autoloop.EXIT_ERROR, 'failed'
    if decision.kind == 'forced':
        return autoloop.EXIT_BLOCKED, 'blocked'
    if decision.kind == 'optional' and decision.hold:
        return autoloop.EXIT_NOTHING, 'waits'
    return autoloop.EXIT_NOTHING, 'stays'


def busy(opts, clock):
    """The loop holds the lease: this check moves nothing, and says the loop's last decision and when."""
    owner = autoloop.lease_owner()
    hb = usage.read_json(core.data_path(autoloop.HEARTBEAT)) or {}
    d = hb.get('decision') if isinstance(hb.get('decision'), dict) else None
    at = usage.num(hb.get('last_pass_at'))
    said = d.get('reason') if d and isinstance(d.get('reason'), str) and d.get('reason').strip() else None
    if hb.get('pid') != owner['pid'] or at is None:
        said = None
    who = ' (process %d)' % owner['pid'] if isinstance(owner['pid'], int) else ''
    line = "Switchboard's loop already runs%s, so this check moves nothing. " % who
    line += 'Its last decision, at %s: %s' % (usage.clock_text(at, clock.wall()), _sentence(said)) if said else \
        'It has not decided yet.'
    doc = blank(opts, 'loop_running', autoloop.EXIT_BUSY)
    pid = owner['pid'] if isinstance(owner['pid'], int) else None
    doc.update(said=line, checked_at=iso(clock.wall()),
               loop={'running': True, 'pid': pid, 'decided_at': iso(at) if said else None, 'said': said})
    return doc


def refused(opts, words, code=autoloop.EXIT_ERROR):
    doc = blank(opts, 'refused', code)
    doc['problem'] = words
    return doc


# ---------- what it decided, and why ----------

def iso(t):
    """Seconds as '2026-11-25T06:10:00Z' (UTC, to the second), or None."""
    if t is None:
        return None
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(int(round(t))))


def _n(x):
    """A number for the document: one decimal at most, a whole number as an int; None stays None."""
    if x is None:
        return None
    x = round(float(x), 1)
    return int(x) if x.is_integer() else x


def _sentence(text):
    text = (text or '').strip()
    return text if text.endswith('.') else text + '.'


def blank(opts, outcome, code):
    return {'schema': SCHEMA, 'dry_run': bool(opts.get('dry_run')), 'checked_at': iso(time.time()),
            'outcome': outcome, 'exit_code': code, 'said': None, 'action': None, 'from': None, 'to': None,
            'from_number': None, 'to_number': None, 'forced': None, 'reasons': [], 'waiting_for': None,
            'in_use': None, 'candidates': [], 'first_refill': None, 'would_change': None, 'settings': None, 'oldest_numbers_s': None, 'loop': None,
            'problem': None}


def settings_doc(stored, used, opts, snap=None):
    """What the check decided on. The flags of one check change the shared lines; an account with a line of its own
    keeps it (`own_lines`), and ranks stay as they are (`ranks`): each entry names its account as the candidates do."""
    def who(aid):
        email, number, _ = _who(snap, aid)
        return {'email': email, 'number': number}
    own = [dict(who(aid), five_hour_line=_n(dict(lines).get('session_at')), week_line=_n(dict(lines).get('weekly_at')))
           for aid, lines in used.own]
    ranks = [dict(who(aid), rank=rank) for aid, rank in used.ranks]
    return {'auto_switch_on': bool(stored.enabled), 'order': used.strategy, 'five_hour_line': _n(used.session_at),
            'week_line': _n(used.weekly_at), 'model_line': _n(used.model_at), 'model': used.model,
            'this_check_only': [name for _, key, _, name in FLAGS if opts.get(key) is not None],
            'own_lines': sorted(own, key=lambda x: (x['number'] is None, x['number'] or 0)),
            'ranks': sorted(ranks, key=lambda x: (x['number'] is None, x['number'] or 0))}


def _limits(v):
    """A view's windows that count: [{'limit', 'model', 'used_percent', 'line', 'points_left'}]."""
    out = []
    for name, pct in v.pcts.items():
        line = v.lines.get(name)
        kind = 'five_hour' if name == policy.FIVE else 'week' if name == policy.WEEK else 'model'
        out.append({'limit': kind, 'model': name[len(policy.MODEL):] if kind == 'model' else None,
                    'used_percent': _n(pct), 'line': _n(line),
                    'points_left': _n(line - pct) if pct is not None and line is not None else None})
    return out


def _window_words(name):
    return policy._window_words(name)


def _pct(x):
    return policy._pct(x)


def _against_line(name, pct, line):
    """'5-hour 71% (19 points under its 90% line)'."""
    if pct is None:
        return '%s not known' % _window_words(name)
    gap = line - pct
    if abs(gap) < 0.05:
        where = 'at its %s%% line' % _pct(line)
    elif gap > 0:
        where = '%s points under its %s%% line' % (_pct(gap), _pct(line))
    else:
        where = '%s points over its %s%% line' % (_pct(-gap), _pct(line))
    return '%s %s%% (%s)' % (_window_words(name), _pct(pct), where)


def _plain(v):
    """'5-hour 10%, weekly 50%'."""
    return ', '.join('%s %s' % (_window_words(n), 'not known' if p is None else '%s%%' % _pct(p))
                     for n, p in v.pcts.items())


def _age_words(v):
    if v.age is not None:
        return 'numbers %s old' % usage.span_text(v.age)
    if any(p is not None for p in v.pcts.values()):
        return 'some numbers not known'
    return 'no numbers yet'


def _who(snap, aid):
    """(email, number, organization) of an account in the snapshot: its whole email, its place in the list (the
    number a person types), and its organization's words (names.org_words, None when not known). Two accounts with
    one email differ in the last two, as in `switchboard status --json`."""
    for i, a in enumerate(snap.accounts if snap is not None else (), 1):
        if a.id == aid:
            return (a.email or a.alias or a.id), i, names.org_words(a.org_name) or None
    return aid, None, None


def _account_doc(v, now, snap=None):
    email, number, org = _who(snap, v.id)
    return {'email': email, 'number': number, 'organization': org, 'limits': _limits(v),
            'checked_at': iso(now - v.age) if v.age is not None else None,
            'age_s': int(round(v.age)) if v.age is not None else None}


def _views(snap):
    return dict((a.id, policy._view(snap, a)) for a in snap.accounts)


def _cleared(snap):
    """The snapshot as policy.need sees one account on its own: switching on, the live login readable, and the account
    in use under no rule, so its answer is about that account alone."""
    rules = tuple((k, r) for k, r in snap.settings.rules if k != snap.active)
    return dataclasses.replace(snap, live_ok=True, settling=False, offline=False,
                               settings=dataclasses.replace(snap.settings, enabled=True, rules=rules))


def _why_not(snap, alone, a, v, av, decision):
    """(why_not word, words) for one account that is not in use; why_not is None when a move could land there."""
    rule = policy.rule_of(snap.settings, a.id)
    if a.status == 'needs_login':
        return 'needs_sign_in', 'needs a sign-in'
    if a.status == 'claimed' or a.id in snap.claimed:
        return 'in_use_by_run', 'a terminal uses it now (switchboard run)'
    if a.status not in policy.OK_STATUSES:
        return 'not_usable', "can't be used now: %s" % STATUS_WORDS.get(a.status, a.status)
    if rule == 'out':
        return 'kept_out', 'kept out of auto-switch'
    in_use_rule = policy.rule_of(snap.settings, snap.active) if av is not None else None
    if decision.action == 'switch' and decision.target == a.id:    # its pick, also an escape past a line
        return None, None
    if policy.need(alone, a.id).action != 'switch':
        if not any(p is not None for p in v.pcts.values()):
            return 'no_numbers', 'no numbers yet'
        if not v.known or v.age is None or v.age > policy.FORCED_MAX_AGE:
            age = usage.span_text(v.age) if v.age is not None else 'some not known'
            return 'numbers_too_old', 'numbers too old: %s (it moves only onto numbers under %s)' % (
                age, usage.span_text(policy.FORCED_MAX_AGE))
        worst = v.over[0] if v.over else min(((n, v.pcts[n], v.lines[n]) for n in v.pcts), key=lambda o: o[2] - o[1])
        return 'at_line', 'at its line: %s' % _against_line(*worst)
    if in_use_rule == 'out':                    # policy.decide: Claude Code stays on an account kept out, always
        return 'in_use_kept_out', '%s, in use, is kept out of auto-switch, so nothing moves from it' % av.label
    if decision.target == a.id:
        return None, None
    if decision.kind == 'forced':               # policy._forced: an account used last only when no other has room
        if rule == 'last' and decision.action == 'switch' and \
                policy.rule_of(snap.settings, decision.target) != 'last':
            return 'used_last', 'used last: only when no other account has room'
        return None, None
    if snap.settings.strategy != 'refills-first' or av is None or not av.known or av.over:
        return None, None
    pair = dataclasses.replace(snap, accounts=tuple(x for x in snap.accounts if x.id in (snap.active, a.id)))
    if policy.decide(pair).target == a.id:
        return None, None
    if rule == 'last':
        return 'used_last', 'used last: only when no other account has room'
    if in_use_rule != 'last' and policy._rank_key(v) > policy._rank_key(av):     # policy._optional: never later
        return 'ranked_after', '%s, after %s (%s): a move that can wait never goes to a later rank' % (
            _rank_words(v), av.label, _rank_words(av))
    if v.room is not None and v.room < policy.OPTIONAL_ROOM:
        return 'no_gain', 'no gain: too little room for a move that can wait (%s points; it needs %s)' % (
            _pct(v.room), _pct(policy.OPTIONAL_ROOM))
    if v.weekly_reset is None:
        return 'no_gain', 'no gain: its weekly refill time is not known'
    if av.weekly_reset is not None and v.weekly_reset > av.weekly_reset - policy.REFILL_GAIN:
        return 'no_gain', "no gain: its week refills %s, not an hour before %s's (%s)" % (
            usage.clock_text(v.weekly_reset, snap.now), av.label, usage.clock_text(av.weekly_reset, snap.now))
    return 'no_gain', 'no gain over %s' % av.label


def _rank_words(v):
    return 'rank %d' % v.rank if v.rank is not None else 'no rank'


def explain(snap, decision):
    """The parts of the document that say why: {'in_use', 'candidates', 'waiting_for', 'first_refill', 'reasons',
    'oldest_numbers_s'}."""
    views = _views(snap)
    av = views.get(snap.active)
    out = {'in_use': None, 'candidates': [], 'waiting_for': None, 'first_refill': None, 'reasons': [],
           'oldest_numbers_s': None}
    ages = []
    if av is not None:
        out['in_use'] = _account_doc(av, snap.now, snap)
        parts = [_against_line(n, p, av.lines[n]) for n, p in av.pcts.items()]
        out['reasons'].append('In use: %s%s; %s.' % (av.label, ''.join(', ' + x for x in parts), _age_words(av)))
        ages.append(av.age)
    else:
        out['reasons'].append('In use: Claude Code is not on a saved account.')
    alone = _cleared(snap)
    for a in snap.accounts:
        if a.id == snap.active:
            continue
        v = views[a.id]
        word, words = _why_not(snap, alone, a, v, av, decision)
        c = dict(_account_doc(v, snap.now, snap), can_take_it=word in (None, 'used_last', 'no_gain', 'ranked_after'),
                 why_not=word,
                 words=None)
        if word is None:
            c['words'] = 'is the one it picks' if decision.target == a.id else 'could take it'
            out['reasons'].append('%s %s: %s; %s.' % (v.label, c['words'], _plain(v), _age_words(v)))
        elif c['can_take_it']:
            c['words'] = 'has room, but %s' % words
            out['reasons'].append('%s %s.' % (v.label, c['words']))
        else:
            c['words'] = words
            out['reasons'].append('%s: %s.' % (v.label, words))
        out['candidates'].append(c)
        ages.append(v.age)
    known = [x for x in ages if x is not None]
    out['oldest_numbers_s'] = int(round(max(known))) if known else None
    if decision.action == 'stay' and decision.kind == 'optional' and decision.hold and av is not None \
            and decision.target in views:
        out['waiting_for'] = waiting_for(snap, av, views[decision.target], decision.hold)
        out['reasons'].append('Waiting: %s.' % out['waiting_for']['words'])
    if decision.action == 'stay' and decision.kind == 'forced' and decision.hold in NO_ROOM:
        first = policy.earliest_refill(snap)
        if first:
            out['first_refill'] = {'at': iso(first[0]), 'email': names.email_of(first[1]), 'limit': first[2]}
            out['reasons'].append('Nothing has room. First refill: %s (%s, %s).' % (
                usage.clock_text(first[0], snap.now), first[1], first[2]))
    out['would_change'] = would_change(snap, decision, av, views, out)
    if out['would_change']:
        out['reasons'].append(out['would_change'])
    return out


def _span_days(seconds):
    """'5 hours', '3 days'."""
    hours = max(0.0, seconds) / 3600.0
    n, unit = (int(round(hours)), 'hour') if hours < 48 else (int(round(hours / 24)), 'day')
    return '%d %s%s' % (n, unit, '' if n == 1 else 's')


def would_change(snap, decision, av, views, said):
    """One sentence: for a stay, the nearest thing that would make it move; for a move, the one thing that tipped it.
    None when nothing in the snapshot says."""
    now = snap.now
    if decision.action == 'switch':
        if decision.kind == 'forced':
            return 'What tipped it: %s.' % decision.reason.split('; ')[0].rstrip('.')
        tv = views.get(decision.target)
        if decision.kind != 'optional' or av is None or tv is None:
            return None
        if policy.rule_of(snap.settings, av.id) == 'last':
            return 'What tipped it: %s is used last, and %s has room.' % (av.label, tv.label)
        if av.weekly_reset is None or tv.weekly_reset is None:
            return None
        return "What tipped it: %s's week refills %s sooner than %s's." % (
            tv.label, _span_days(av.weekly_reset - tv.weekly_reset), av.label)
    wait = said.get('waiting_for')
    if wait:
        if wait['what'] == 'idle':
            return 'It would move when Claude Code has been idle %d minutes (%s).' % (
                _minutes(policy.IDLE_FOR), usage.clock_text(now + policy.IDLE_FOR - snap.idle_for_s, now))
        if wait['what'] == 'busy':
            return 'It would move when Claude Code has been idle %d minutes.' % _minutes(policy.IDLE_FOR)
        if wait['what'] == 'since_switch':
            return 'It would move %d minutes after the last switch, at %s.' % (
                _minutes(policy.DWELL), usage.clock_text(now + policy.DWELL - snap.since_switch_s, now))
        if wait['what'] == 'optional_moves':
            return 'It would move when fewer than %d optional moves fall in the last %d hours.' % (
                policy.OPTIONAL_CAP, policy.OPTIONAL_CAP_WINDOW // 3600)
        if wait['what'] == 'load':
            return "It would move when the Mac's load is under %g per core." % policy.LOAD_HIGH
        if wait['what'] == 'battery':
            return 'It would move on power, or with the battery at %g%% or more.' % policy.BATTERY_LOW
        return None
    first = said.get('first_refill')
    if first:
        return "It would move at %s, when %s's %s limit refills." % (
            usage.clock_text(policy.earliest_refill(snap)[0], now), first['email'], first['limit'])
    if decision.kind is not None or av is None or not av.known or av.over or not snap.settings.enabled \
            or not snap.live_ok or snap.settling or snap.offline or policy.rule_of(snap.settings, av.id) == 'out':
        return None
    name = min(av.pcts, key=lambda n: av.lines[n] - av.pcts[n])
    at = '%s%% of the %s limit (now %s%%)' % (_pct(av.lines[name]), _window_words(name), _pct(av.pcts[name]))
    if any(c['can_take_it'] for c in said.get('candidates') or []):
        if snap.settings.strategy != 'refills-first':      # policy._optional: no early move in these orders
            return 'It would move at %s, and not before: in this order a move waits for a line.' % at
        return 'It would move at %s.' % at
    return 'At %s it would look for an account with room; none has room now.' % at


NO_ROOM = ('every account is out', 'no account has room')
# The facts a move that can wait waits on, each with the value that takes it away: policy._hold is asked again
# without one fact at a time, and the first whose absence changes its answer is the one it waits for.
HOLD_FACTS = (('restart', {'no_optional': None}), ('label', {'disagree': False}), ('obligation', {'obligations': ()}),
              ('busy', {'busy': False}), ('idle', {'idle_for_s': None}), ('since_switch', {'since_switch_s': None}),
              ('optional_moves', {'optional_recent': 0}), ('load', {'load_per_core': None}),
              ('battery', {'battery_pct': None}))


def _minutes(seconds):
    return int(max(0.0, seconds) // 60)


def waiting_for(snap, av, tv, hold):
    """{'what', 'now', 'needed', 'words'}: what a move that can wait waits for, with its number when it has one."""
    what = 'numbers'
    for name, change in HOLD_FACTS:
        if policy._hold(dataclasses.replace(snap, **change), av, tv) != hold:
            what = name
            break
    now = needed = None
    words = hold
    if what == 'idle':
        now, needed = snap.idle_for_s, policy.IDLE_FOR
        words = 'Claude Code has been idle %d of %d minutes' % (_minutes(now), _minutes(needed))
    elif what == 'since_switch':
        now, needed = snap.since_switch_s, policy.DWELL
        words = '%d of %d minutes since the last switch' % (_minutes(now), _minutes(needed))
    elif what == 'optional_moves':
        now, needed = snap.optional_recent, policy.OPTIONAL_CAP
        words = '%d optional moves in the last %d hours (it makes at most %d)' % (
            now, policy.OPTIONAL_CAP_WINDOW // 3600, needed)
    elif what == 'busy':
        words = 'Claude Code is working now'
    elif what == 'load':
        now, needed = snap.load_per_core, policy.LOAD_HIGH
    elif what == 'battery':
        now, needed = snap.battery_pct, policy.BATTERY_LOW
    return {'what': what, 'now': _n(now), 'needed': _n(needed), 'words': words}


def document(opts, decision, snap, stored, settings, outcome, code):
    """The whole document for a decision on a snapshot (`snap` None: only the decision is known)."""
    doc = blank(opts, outcome, code)
    by_id = dict((a.id, a) for a in snap.accounts) if snap is not None else {}

    def label(aid):
        return policy.label_of(snap, by_id[aid]) if aid in by_id else aid
    frm = label(snap.active) if snap is not None and snap.active in by_id else None
    to = label(decision.target) if decision.target else None
    reason = _sentence(decision.reason)
    if decision.action == 'switch':
        head = '%s Claude Code to %s: %s' % ('Switched' if outcome == 'switched' else
                                           'Would switch' if opts.get('dry_run') else 'Would have switched', to,
                                           reason)
    else:
        head = 'Stays on %s: %s' % (frm, reason) if frm else 'Stays: %s' % reason
    doc.update(said=head, action=decision.action, to=to, settings=settings_doc(stored, settings, opts, snap),
               forced=True if decision.kind == 'forced' else False if decision.kind == 'optional' else None)
    doc['from'], doc['from_number'] = _who(snap, snap.active)[:2] if frm else (None, None)
    doc['to'], doc['to_number'] = _who(snap, decision.target)[:2] if to else (None, None)
    if snap is not None:
        doc['checked_at'] = iso(snap.now)
        doc.update(explain(snap, decision))
    return doc


# ---------- the command ----------

def lines_of(doc):
    """The document as a person reads it: the decision in one line, then why."""
    out = [doc['said']] if doc.get('said') else []
    if doc.get('problem') and doc['outcome'] != 'refused':
        out.append(doc['problem'])
    if doc['outcome'] == 'refused':
        return [doc['problem']]
    out += doc.get('reasons') or []
    s = doc.get('settings') or {}
    if s and not s.get('auto_switch_on'):
        out.append('Auto-switch is off; this check decides as if it were on.')
    own = s.get('this_check_only') or []
    if own:
        words = {'five_hour_line': '5-hour line %s%%' % _pct(s.get('five_hour_line') or 0),
                 'week_line': 'weekly line %s%%' % _pct(s.get('week_line') or 0), 'order': 'order %s' % s.get('order')}
        out.append('For this check only: %s.' % ', '.join(words[k] for k in own))
        kept = [x['email'] for x in s.get('own_lines') or []
                if ('five_hour_line' in own and x['five_hour_line'] is not None)
                or ('week_line' in own and x['week_line'] is not None)]
        if kept:
            out.append('An account with a line of its own keeps it: %s.' % ', '.join(kept))
    return out


def _json(doc):
    return json.dumps(doc, indent=1, ensure_ascii=False) + '\n'


def main(args, deps=None, clock=None, out=None, err=None):
    """`switchboard auto check|why ...`. Returns the exit code. With --json, standard output holds the document and
    nothing else, also when it refuses."""
    out = out or sys.stdout
    as_json = '--json' in args[1:]
    try:
        opts = parse(args)
    except Wrong as e:
        if as_json:
            out.write(_json(refused({'dry_run': args[0] == 'why' or '--dry-run' in args}, str(e), 2)))
            (err or sys.stderr).write('%s\n' % e)
        else:
            out.write('%s\n%s\n' % (e, '\n'.join(FORMS)))
        return 2
    if core.DEMO:
        words = 'The demo has made-up accounts; try auto-switch on its page.'
        if not as_json:
            raise core.SwitchError(words)
        out.write(_json(refused(opts, words)))
        return autoloop.EXIT_ERROR
    code, doc = (dry_run if opts['dry_run'] else one_pass)(opts, deps, clock or usage.Clock())
    out.write(_json(doc) if as_json else ''.join('%s\n' % x for x in lines_of(doc)))
    return code
