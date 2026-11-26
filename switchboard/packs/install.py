"""One switch per pack installs it: plan, consent once, five steps with a progress bar, start, health. Off takes out
exactly what was added.

  plan(pack)          what it would add, from where, how big; changes nothing. Its 'id' is the consent.
  begin(pack, id)     the id from that plan (unchanged since) starts the install in the background; anything else
                      answers with the plan again. Packs: 'claude-mem' (packs/claudemem_install.py) and 'codegraph'
                      (packs/codegraph_install.py).
  job_view(pack)      the progress: step n of 5, its name, a percent, the last line, and the log's end.
  repair(pack)        a stopped install runs again from the start (same consent); an installed claude-mem gets its own
                      `npx claude-mem repair`, then a start.
  remove(pack)        the exact undo (refuses, changing nothing, when the person changed something it added).
  health_tick()       once a minute from the page's loop: claude-mem's worker answers /api/health, or, while Claude
                      Code was used in the last hour, it is started once, then again after 1, 5 and 15 minutes.

claude-mem's installer and its repair run for minutes in a session of their own, so they go on when Switchboard stops.
Each run is in claude-mem's record while it goes; until it has ended, begin, repair and remove refuse with one line
naming its pid.

Every outside command goes through packs/runner.py and every download through packs/fetch.py. One lock (LOCK, which
claudecode.py uses as its own) keeps a click, a job, the weekly cap and the health check from editing at once.

Terminal (the same code path, for engineers and for checks):
    python3 -m switchboard.packs.install plan claude-mem|codegraph
    python3 -m switchboard.packs.install on claude-mem|codegraph --yes      (--yes is the consent to the plan shown)
    python3 -m switchboard.packs.install off|status|repair claude-mem|codegraph
    python3 -m switchboard.packs.install map|refresh FOLDER
"""
import json
import os
import sys
import threading
import time

from switchboard.packs import claudemem_install as cmi
from switchboard.packs import codegraph_install as cgi
from switchboard.packs import common, fetch, memory, runner

LOCK = threading.Lock()
JOBS = {}
MODULES = {'claude-mem': cmi, 'codegraph': cgi}
HEALTH = {'at': 0, 'running': False, 'ok': None, 'line': None, 'why': None, 'next': 0, 'tries': 0, 'port': None}
HEALTH_EVERY = 60
BACKOFF = (60, 300, 900)
RECENT = 3600
KW = {}            # tests and checks: pins, environ, home, claude_dir, machine, battery, free, state_path
_SEEN = {}


def _kw(name, default=None):
    return KW.get(name, default)


def _pins():
    return KW.get('pins') or fetch.load_pins()


def _environ():
    return KW.get('environ') if KW.get('environ') is not None else os.environ


def record(pack):
    return MODULES[pack].record(_kw('state_path'))


def busy(pack=None):
    jobs = [j for p, j in JOBS.items() if (pack is None or p == pack) and j['state'] == 'running']
    return bool(jobs)


def _wait_for(pack, rec):
    """Refused while claude-mem's installer or repair from before still runs: started by a Switchboard that has since
    stopped, it goes on in a session of its own (claudemem_install.installer_running). Off refuses the same way."""
    line = cmi.wait_line(rec) if pack == 'claude-mem' else None
    if line:
        raise common.Refused(line)


# ---------------------------------------------------------------- plan and consent

def plan(pack, log_path=None):
    m = MODULES[pack]
    if pack == 'claude-mem':
        return m.plan(_pins(), _kw('claude_dir'), _kw('home'), _environ(), _kw('machine'), _kw('state_path'),
                      _kw('battery'), _kw('free'), log_path)
    return m.plan(_pins(), _kw('claude_dir'), _kw('home'), _environ(), _kw('machine'), _kw('state_path'),
                  _kw('battery'), _kw('free'))


def plan_lines(p):
    """The plan in a few short lines (the page). plan_detail() has every file, key and host."""
    if p['pack'] == 'claude-mem':
        fetched = [u.split(' from ')[0] for u in p.get('uses') or [] if ' from ' in u]
        lines = ['Installs claude-mem %s and starts it.' % p['version'],
                 'Downloads it from npm%s. It takes about %s.' % (
                     (', and %s from nodejs.org and GitHub' % _and(fetched)) if fetched else '', size(p.get('mb'))),
                 'Adds its plugin to Claude Code, and ~/.local/bin/claude-mem.',
                 'Sets chats only, no auto-update, File Read Gate off, no quota fallback, and its own stop at 85% of '
                 'the weekly limit of the account in use.',
                 'Off takes out what it adds. Your notes stay.']
    else:
        lines = ['Installs codegraph %s.' % p['version'],
                 'Downloads it from GitHub, about %d MB, checked against its pin. It takes about %s.' % (
                     p.get('download_mb', 0), size(p.get('mb'))),
                 'Adds its MCP server to Claude Code, an allow rule for its tools, and the code map block.',
                 'Off takes out what it adds. Project maps stay.']
    return lines + [n for n in p.get('notes') or [] if not n.startswith('Its observer')]


def size(mb):
    """MiB as a person reads it: 285 MB, 2.1 GB."""
    mb = mb or 0
    return '%d MB' % mb if mb < 1000 else '%.1f GB' % (mb / 1024.0)


def _and(xs):
    return xs[0] if len(xs) == 1 else ', '.join(xs[:-1]) + ' and ' + xs[-1]


def plan_detail(p):
    """The plan line by line: every file and key it adds, the settings, the hosts (the terminal and the log)."""
    lines = ['Installs %s %s.' % (p['pack'], p['version'])]
    if p.get('uses'):
        lines.append('Uses: ' + '; '.join(p['uses']) + '.')
    for a in p.get('adds') or []:
        lines.append('Adds ' + a + '.')
    if p.get('settings'):
        lines.append('Sets in ~/.claude-mem/settings.json: ' + ', '.join(p['settings']) + '.')
    if p.get('hosts'):
        lines.append('Downloads from %s. Takes about %s.' % (', '.join(p['hosts']), size(p.get('mb'))))
    if p.get('keeps'):
        lines.append('Off takes out what it adds. ' + p['keeps'] + '.')
    lines += p.get('notes') or []
    return lines


def begin(pack, consent=None, cap=None, say=None, wait=False):
    """Start an install when consent is the id of the plan as it is now. Returns {'state': 'plan', 'plan', 'lines'}
    (nothing changed), {'state': 'present'} (already there: nothing to install), or {'state': 'installing', 'job'}."""
    if busy(pack):
        raise common.Refused('%s is being installed now.' % pack)
    rec = record(pack)
    _wait_for(pack, rec)
    if rec and rec.get('state') == 'installed':
        return {'state': 'present'}                       # turning it on again changes nothing
    log_path = runner.new_log(pack)
    p = plan(pack, log_path)
    if p.get('installed_already'):
        return {'state': 'present'}
    if p.get('blocked'):
        raise common.Refused(p['blocked'])
    if not consent or consent != p.get('id'):
        return {'state': 'plan', 'plan': p, 'lines': plan_lines(p), 'detail': plan_detail(p), 'id': p['id']}
    runner.note(log_path, 'Consent to plan %s:\n  %s' % (p['id'], '\n  '.join(plan_detail(p))))
    _start(pack, MODULES[pack].STEPS, log_path, {'cap': cap, 'consent': p['id']}, say, wait)
    return {'state': 'installing', 'job': job_view(pack)}


def _ctx(pack, log_path, extra):
    m = MODULES[pack]
    ctx = {'pins': _pins(), 'environ': _environ(), 'cd': _kw('claude_dir'), 'machine': _kw('machine'),
           'state_path': _kw('state_path') or common.default_state_path(), 'log': log_path,
           'w': m.where(_kw('claude_dir'), _kw('home'))}
    ctx.update(extra or {})
    return ctx


def _start(pack, steps, log_path, extra, say, wait, after=None):
    m = MODULES[pack]
    job = {'pack': pack, 'state': 'running', 'step': 0, 'of': len(steps), 'name': steps[0][1], 'percent': 0,
           'line': '', 'started': time.time(), 'ended': None, 'log': log_path, 'names': [s[1] for s in steps]}
    JOBS[pack] = job
    ctx = _ctx(pack, log_path, extra)

    def work():
        with LOCK:
            done = 0
            for i, (key, name, weight) in enumerate(steps):
                job.update(step=i + 1, name=name, percent=done)
                try:
                    line = getattr(m, 'step_' + key)(ctx)
                except Exception as e:  # noqa: BLE001 - every failure stops here, said plainly, recorded
                    why = str(e) if isinstance(e, common.Refused) else 'Something went wrong (%s).' % e.__class__.__name__
                    runner.note(log_path, 'step %d of %d (%s) stopped: %s' % (i + 1, len(steps), name, why))
                    if not isinstance(e, common.Refused):
                        import traceback
                        runner.note(log_path, traceback.format_exc())
                    job.update(state='failed', ended=time.time(),
                               line='Stopped at step %d of %d (%s): %s' % (i + 1, len(steps), name, why))
                    _mark(pack, 'partial', ctx)
                    if say:
                        say('%s: %s' % (pack, job['line']))
                    return
                runner.note(log_path, 'step %d of %d (%s): %s' % (i + 1, len(steps), name, line))
                done += weight
            job.update(state='done', percent=100, ended=time.time(), line=_done_line(pack, ctx))
            if after:
                after(ctx)
            if say:
                say(job['line'])
    t = threading.Thread(target=work, daemon=True, name='switchboard-%s' % pack)
    t.start()
    if wait:
        t.join()
    return job


def _done_line(pack, ctx):
    if pack == 'claude-mem':
        return 'claude-mem %s is installed and running on port %s.' % (
            ctx['pins']['claude_mem']['version'], ctx.get('port') or ctx['rec'].get('port'))
    return 'codegraph %s is installed and connected to Claude Code.' % ctx['asset']['version']


def _mark(pack, state, ctx):
    rec = ctx.get('rec')
    if rec is None:
        return
    rec['state'] = state if rec.get('state') != 'installed' else rec['state']
    if ctx.get('consent'):
        rec['consent'] = ctx['consent']
    try:
        MODULES[pack].save_record(rec, ctx['state_path'])
    except common.Refused:
        pass


def job_view(pack):
    j = JOBS.get(pack)
    if not j:
        return None
    out = {k: j[k] for k in ('state', 'step', 'of', 'name', 'percent', 'line', 'names')}
    out['log'] = runner.tail(j['log'], 12)
    return out


# ---------------------------------------------------------------- repair and remove

def repair(pack, say=None, wait=False):
    rec = record(pack)
    if busy(pack):
        raise common.Refused('%s is being installed now.' % pack)
    if not rec:
        raise common.Refused('Switchboard did not install %s here, so there is nothing to repair.' % pack)
    _wait_for(pack, rec)
    log_path = runner.new_log(pack)
    if rec.get('state') != 'installed':
        p = plan(pack, log_path)
        if p.get('blocked'):
            raise common.Refused(p['blocked'])
        if rec.get('consent') and rec['consent'] != p.get('id'):
            return {'state': 'plan', 'plan': p, 'lines': plan_lines(p), 'detail': plan_detail(p), 'id': p['id']}
        _start(pack, MODULES[pack].STEPS, log_path, {'consent': p.get('id')}, say, wait)
        return {'state': 'installing', 'job': job_view(pack)}
    if pack == 'claude-mem':
        _start(pack, (('repair', 'Repair', 70), ('start', 'Start', 30)), log_path, {'rec': rec}, say, wait)
        return {'state': 'installing', 'job': job_view(pack)}
    _start(pack, (('check', 'Check', 100),), log_path, {'rec': rec, 'asset': fetch.asset(_pins(), 'codegraph',
                                                                                         _kw('machine'))}, say, wait)
    return {'state': 'installing', 'job': job_view(pack)}


def remove(pack, delete_maps=False):
    """The exact undo. The caller holds LOCK. Returns lines."""
    rec = record(pack)
    if not rec:
        return []
    if busy(pack):
        raise common.Refused('%s is being installed now; wait for it to finish, then turn it off.' % pack)
    sp = _kw('state_path')
    if pack == 'claude-mem':
        lines = cmi.undo(rec, _environ(), sp, runner.new_log(pack))
    else:
        lines = cgi.undo(rec, delete_maps)
    MODULES[pack].save_record(None, sp)
    JOBS.pop(pack, None)
    if pack == 'claude-mem':
        HEALTH.update(ok=None, line=None, why=None, tries=0, next=0)
    return lines


def plan_remove(pack):
    """Raises Refused (nothing changed) when the undo would refuse; used before anything else is taken out."""
    rec = record(pack)
    if rec:
        (cmi.plan_undo if pack == 'claude-mem' else cgi.plan_undo)(rec)


# ---------------------------------------------------------------- keeping claude-mem running

def claude_code_recent(now, within=RECENT, root=None):
    """True when a Claude Code transcript was written in the last `within` seconds."""
    from switchboard import awake
    root = root or os.path.join(common.claude_dir(_kw('claude_dir')), 'projects')
    t = _SEEN.get(root)
    if t is None:
        t = _SEEN[root] = awake.Transcripts(root)
    try:
        return bool(t.recent(now, within))
    except OSError:
        return False


def health_tick(now=None, say=None):
    """Once a minute: is claude-mem's worker up? Started once when it is down and Claude Code was used lately."""
    now = time.time() if now is None else now
    try:
        if now - HEALTH['at'] < HEALTH_EVERY or busy() or not LOCK.acquire(False):
            return None
        try:
            HEALTH['at'] = now
            return _health(now, say)
        finally:
            LOCK.release()
    finally:
        HEALTH['running'] = False


def _health(now, say):
    environ = _environ()
    s = memory.cm_status(_kw('claude_dir'), _kw('state_path'), environ, _kw('home'))
    if not s['switch'] or not s['installed']['registered']:
        HEALTH.update(ok=None, line=None, why=None, tries=0, next=0)
        return None
    w = cmi.where(_kw('claude_dir'), _kw('home'))
    port = cmi.worker_port(environ, w['home'])
    HEALTH['port'] = port
    h = cmi.health(port, environ=environ, home=w['home'])
    if h['ok']:
        HEALTH.update(ok=True, why=None, line='Running on port %d.' % port, tries=0, next=0)
        return 'ok'
    if h['why'] == 'other':
        HEALTH.update(ok=False, why='port', line='Port %d is in use by another program.' % port)
        return 'port'
    HEALTH.update(ok=False, why=h['why'], line='Not running.' if h['why'] == 'down' else 'Not answering.')
    if not claude_code_recent(now):
        HEALTH['line'] += ' It starts with your next Claude Code session.'
        return 'idle'
    if now < HEALTH['next']:
        return 'waiting'
    rec = cmi.record(_kw('state_path'))
    r = cmi.start(w, environ, rec, _kw('state_path'), runner.new_log('claude-mem'), wait=_kw('start_wait', 30))
    HEALTH['tries'] += 1
    HEALTH['next'] = now + BACKOFF[min(HEALTH['tries'], len(BACKOFF)) - 1]
    HEALTH.update(ok=r['ok'], line=r['line'] if r['ok'] else 'Not running. ' + r['line'])
    if r['ok']:
        HEALTH.update(tries=0, next=0)
        if say:
            say('claude-mem was not running; Switchboard started it.')
        return 'started'
    return 'failed'


def health_now(max_age=5.0):
    """The worker's health for the page: the tick's answer, or a fresh quick look when that is old."""
    if HEALTH['ok'] is None or time.time() - HEALTH.get('looked', 0) > max_age:
        home = cmi.where(_kw('claude_dir'), _kw('home'))['home']
        port = cmi.worker_port(_environ(), home)
        h = cmi.health(port, 0.5, _environ(), home)
        HEALTH['looked'] = time.time()
        if h['ok']:
            HEALTH.update(ok=True, why=None, line='Running on port %d.' % port, port=port)
        elif HEALTH['ok'] is not False:
            HEALTH.update(ok=False, why=h['why'], port=port,
                          line='Port %d is in use by another program.' % port if h['why'] == 'other' else 'Not running.')
    return {'ok': HEALTH['ok'], 'line': HEALTH['line'], 'port': HEALTH['port']}


# ---------------------------------------------------------------- what the page shows

def view(pack, switch_on):
    rec = record(pack)
    job = job_view(pack)
    before = pack == 'claude-mem' and bool(rec) and not busy(pack) and cmi.installer_running(rec)
    out = {'by_switchboard': bool(rec), 'version': (rec or {}).get('version'), 'job': job,
           'stopped': bool(rec) and rec.get('state') != 'installed' and not busy(pack) and not before,
           'notes': (rec or {}).get('notes') or []}
    if before:                                             # its installer (or repair) from before still runs
        out['before'] = True
        out['line'] = '%s (from before).' % ('Repairing' if rec['run'].get('what') == 'repair' else 'Installing')
    elif out['stopped'] and not (job and job['state'] == 'failed'):
        out['line'] = 'Stopped part-way.'                  # Repair and the switch are beside it
    if pack == 'claude-mem' and switch_on and rec and rec.get('state') == 'installed' and not busy(pack):
        out['health'] = health_now()
    if pack == 'codegraph' and switch_on:
        tool, _ = cgi.map_tool(_environ(), _kw('home'), _kw('state_path'))
        out['map_tool'] = tool
        out['projects'] = [{'path': p['path'], 'at': p['at'], 'tool': p.get('tool', 'codegraph')}
                           for p in cgi.maps(_kw('state_path'))]
    return out


# ---------------------------------------------------------------- terminal

def _print_job(pack, out):
    shown = 0
    while True:
        j = JOBS.get(pack)
        if j:
            upto = j['step'] if j['state'] == 'running' else (j['of'] if j['state'] == 'done' else j['step'])
            while shown < upto:                       # every step once, also the ones that went by between looks
                shown += 1
                out.write('Step %d of %d: %s\n' % (shown, j['of'], j['names'][shown - 1]))
                out.flush()
            if j['state'] != 'running':
                out.write(j['line'] + '\n')
                return 0 if j['state'] == 'done' else 1
        time.sleep(0.2)


def main(argv=None, out=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    out = out or sys.stdout
    if '--pins' in argv:                 # a pins file of one's own (tests and checks); the packaged one otherwise
        i = argv.index('--pins')
        KW['pins'] = fetch.load_pins(argv[i + 1])
        del argv[i:i + 2]
    yes = '--yes' in argv
    argv = [a for a in argv if a != '--yes']
    what = argv[0] if argv else 'status'
    target = argv[1] if len(argv) > 1 else None
    try:
        if what in ('map', 'refresh') and target:
            with LOCK:
                r = (cgi.map_project if what == 'map' else cgi.refresh_project)(target, _kw('state_path'),
                                                                               log_path=runner.new_log('codegraph'))
            out.write('%s: %s\n' % (r['path'], r['line'] or 'done'))
            return 0
        if target not in MODULES:
            out.write(__doc__.split('Terminal')[1].split('\n', 1)[1])
            return 2
        if what == 'plan' or (what == 'on' and not yes):
            p = plan(target)
            if p.get('installed_already'):
                out.write('%s is already on this Mac; nothing to install.\n' % target)
                return 0
            out.write('\n'.join(plan_detail(p)) + '\n')
            if p.get('blocked'):
                out.write(p['blocked'] + '\n')
                return 1
            out.write('Run again with --yes to install.\n' if what == 'on' else '')
            return 0
        if what == 'on':
            p = plan(target)
            if p.get('installed_already'):
                out.write('%s is already on this Mac; nothing to install.\n' % target)
                return 0
            r = begin(target, p.get('id'))
            return _print_job(target, out) if r['state'] == 'installing' else 1
        if what == 'repair':
            r = repair(target)
            if r['state'] == 'plan':
                out.write('\n'.join(r['lines']) + '\nThe plan changed: run on --yes again.\n')
                return 1
            return _print_job(target, out)
        if what == 'off':
            with LOCK:
                plan_remove(target)
                lines = remove(target)
                if target == 'claude-mem':
                    p = memory.cm_plan(False, None, _kw('claude_dir'), _kw('state_path'), _environ(), _kw('home'))
                    if not p.get('blocked'):
                        memory.cm_apply(p)
                elif target == 'codegraph':
                    from switchboard.packs import codemap
                    p = codemap.plan(False, _kw('claude_dir'), _kw('state_path'), home=_kw('home'))
                    if p.get('op') and not p.get('blocked'):
                        codemap.apply(p)
            out.write('\n'.join(lines or ['%s: nothing of Switchboard\'s to take out.' % target]) + '\n')
            return 0
        if what == 'status':
            rec = record(target)
            out.write(json.dumps({'record': rec, 'job': job_view(target),
                                  'health': health_now() if target == 'claude-mem' and rec else None},
                                 indent=1, default=str) + '\n')
            return 0
    except common.Refused as e:
        out.write(str(e) + '\n')
        return 1
    out.write('usage: python3 -m switchboard.packs.install plan|on|off|status|repair claude-mem|codegraph [--yes]\n')
    return 2


if __name__ == '__main__':
    sys.exit(main())
