"""Switchboard's skill: plain Markdown that lets a coding agent answer questions about Switchboard.

The skill is the folder switchboard/skill/ in the program: SKILL.md (a front matter with `name` and `description`,
then how to answer and the rules) and reference/*.md, which an agent opens only when a question needs one. A copy goes
in two places:

- Claude Code's personal skills folder, <Claude Code's folder>/skills/switchboard (core.claude_dir(): ~/.claude, or
  $CLAUDE_CONFIG_DIR). Claude Code reads SKILL.md there when a question fits its description.
- The shared skills folder Codex reads, ~/.agents/skills/switchboard (AGENTS_SKILLS), only when the providers code
  finds Codex on this Mac (codex_here). Codex's own folder is never written.

Each copy holds a small marker file (MARK): that it is Switchboard's, from which version, and which files it wrote. A
folder there without a valid marker, or one that is a symbolic link, is someone's own skill of the same name: it is
never written over or removed.

The rules are the ones keep.py has for the status line:
- by_itself(): at the first run, the skill goes into each place once. Never after the person turned it on or off
  (skill_set_by_you); never into a place whose skills folder, or the folder above that, is a symbolic link (kept with
  dotfiles, say: the folder may be shared with other Macs); never when Claude Code's folder is not there yet. A folder
  of the person's own is left, and said once in History.
- `switchboard skill on|off` is the person's choice from then on, made under keep's CHOICE lock: off takes out
  Switchboard's own copies, and they never come back by themselves; on puts them in, through a link too.
- refresh(): a copy of Switchboard's own from an older program is brought up to date, at the first run and by the
  page's ten-minute self-check (selfcheck.check_skill). Never through a link, never a copy a newer Switchboard
  wrote, never after the person turned the skill off.
- take_out_all(): for the uninstaller (`switchboard tidy`): only Switchboard's own copies, and in them only the files
  their marker names. Anything else in such a folder stays, with the folder.
"""
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time

from . import core

NAME = 'switchboard'                        # the skill's name, and its folder's in each skills folder
MARK = '.switchboard-skill.json'            # the marker file in each copy Switchboard wrote
AGENTS_SKILLS = os.path.join('.agents', 'skills')   # under the home folder: the skills folder Codex reads
SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'skill')
PLACES = ('claude', 'codex')
LABELS = {'claude': 'Skill for Claude Code', 'codex': 'Skill for Codex'}
SAFE = re.compile(r'^[A-Za-z0-9_-][A-Za-z0-9_.-]*(/[A-Za-z0-9_-][A-Za-z0-9_.-]*)?$')   # a file the marker may name
LINKED = set()                              # linked folders already named in the log

USAGE = 'switchboard skill [on|off|show]'
THEIRS = 'You already have a skill named switchboard in %s, so Switchboard left it.'
SHOW_LINE = 'switchboard skill show prints the whole skill, for any other agent.'
SKILL_ON = '%s: on'


class Refused(Exception):
    """A place that takes no copy now, with the one sentence that says why."""


# ---------- where ----------

def place_dir(place):
    """The skill's folder in one place."""
    if place == 'codex':
        return os.path.join(core.home(), AGENTS_SKILLS, NAME)
    return os.path.join(core.claude_dir(), 'skills', NAME)


def _above(place):
    """The folders above a place's skill folder that, as a symbolic link, keep the skill from going in by itself:
    the skills folder and the folder that holds it."""
    skills = os.path.dirname(place_dir(place))
    return (os.path.dirname(skills), skills)


def linked_above(place):
    """The first of _above(place) that is a symbolic link, or None."""
    return next((p for p in _above(place) if os.path.islink(p)), None)


def short(p):
    """A path under the home folder as ~/..., as the other commands say it."""
    h, p = os.path.normpath(core.home()), os.path.normpath(p)
    return '~' + p[len(h):] if p == h or p.startswith(h + os.sep) else p


def where_name(place):
    return 'Codex' if place == 'codex' else 'Claude Code'


def codex_here():
    """Codex is on this Mac, as the providers code finds it, from files only: its adapter runs under a Reach that
    can start nothing (providers/view.quiet), so no program starts and nothing is asked."""
    try:
        from .providers import codex, run, view
        return bool(codex.CodexProvider(view.quiet(run.default_reach())).detect().installed)
    except Exception as e:  # noqa: BLE001 - not known reads as not here: nothing goes in by itself then
        core.log('looking for Codex: %r' % e)
        return False


# ---------- the skill in the program ----------

def guide_files(source=None):
    """[(relative path, bytes)]: SKILL.md, then each reference file by name. Only Markdown files: nothing else in the
    program's folder is copied."""
    source = source or SOURCE
    out = [('SKILL.md', _read(os.path.join(source, 'SKILL.md')))]
    ref = os.path.join(source, 'reference')
    for name in sorted(os.listdir(ref)) if os.path.isdir(ref) else []:
        if name.endswith('.md') and not name.startswith('.') and os.path.isfile(os.path.join(ref, name)):
            out.append(('reference/' + name, _read(os.path.join(ref, name))))
    return out


def _read(path):
    with open(path, 'rb') as f:
        return f.read()


def digest(files):
    """A short digest of the skill's files: two copies with the same digest hold the same text."""
    h = hashlib.sha256()
    for rel, data in files:
        h.update(rel.encode('utf-8') + b'\0' + hashlib.sha256(data).digest())
    return h.hexdigest()[:16]


def version_key(v):
    """'0.2.0' -> (0, 2, 0); anything else -> (), older than every version."""
    return tuple(int(n) for n in re.findall(r'\d+', v)) if isinstance(v, str) and re.match(r'^\d+(\.\d+)*$', v) \
        else ()


def show_text(source=None):
    """The whole skill as one Markdown text, for an agent that reads no skills folder: SKILL.md without its front
    matter, then each reference file, in the order of SKILL.md's map."""
    files = guide_files(source)
    head = files[0][1].decode('utf-8')
    if head.startswith('---\n'):
        end = head.find('\n---\n', 4)
        head = head[end + 5:] if end >= 0 else head
    parts = [head.strip()]
    for rel, data in files[1:]:
        parts.append('<!-- %s -->\n%s' % (rel, data.decode('utf-8').strip()))
    return '\n\n'.join(parts) + '\n'


# ---------- a copy on disk ----------

def read_mark(folder):
    """The marker of a copy Switchboard wrote ({'by', 'version', 'digest', 'files'}), or None: no marker, one that is
    not a plain file, or one that does not say it is Switchboard's."""
    path = os.path.join(folder, MARK)
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode) or os.path.islink(folder):
            return None
        with open(path, encoding='utf-8') as f:
            m = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(m, dict) or m.get('by') != NAME or not isinstance(m.get('version'), str):
        return None
    files = m.get('files')
    if not isinstance(files, list) or not all(isinstance(x, str) and SAFE.match(x) for x in files):
        return None
    return m


def state(place, files=None):
    """What is in one place now: {'place', 'path', 'is' ('absent', 'ours', 'theirs' or 'unreadable'), 'version',
    'current' (ours, with this program's text and version), 'newer' (ours, from a newer Switchboard), 'linked' (the
    folder above that is a link, or None), 'why' (for 'unreadable')}."""
    d = place_dir(place)
    out = {'place': place, 'path': d, 'is': 'absent', 'version': None, 'current': False, 'newer': False,
           'linked': linked_above(place), 'why': None}
    try:
        if os.path.islink(d) or (os.path.lexists(d) and not os.path.isdir(d)):
            out['is'] = 'theirs'
        elif os.path.lexists(d):
            m = read_mark(d)
            if m is None:
                out['is'] = 'theirs'
            else:
                files = guide_files() if files is None else files
                out['is'], out['version'] = 'ours', m['version']
                out['newer'] = version_key(m['version']) > version_key(core.VERSION)
                out['current'] = m.get('digest') == digest(files) and m['version'] == core.VERSION
    except OSError as e:
        out['is'], out['why'] = 'unreadable', e.strerror or e.__class__.__name__
    return out


def _write_file(path, data):
    """One file written whole: a temporary file beside it, renamed over it. A link in its place is replaced, never
    followed."""
    tmp = os.path.join(os.path.dirname(path), '.%s.%s.tmp' % (os.path.basename(path), os.urandom(4).hex()))
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)


def _fill(root, files, mark):
    """The skill's files, then its marker last, into root. A folder on the way that is a link is refused."""
    for rel, data in files:
        path = os.path.join(root, rel)
        sub = os.path.dirname(path)
        if sub != root:
            if os.path.islink(sub):
                raise OSError('%s is a symbolic link' % short(sub))
            os.makedirs(sub, mode=0o755, exist_ok=True)
        _write_file(path, data)
    _write_file(os.path.join(root, MARK), (json.dumps(mark, indent=1, sort_keys=True) + '\n').encode('utf-8'))


def put(place, files=None):
    """Write Switchboard's copy into one place: a new folder appears whole (built beside it, then renamed into
    place); a copy of Switchboard's own is brought up to date file by file, its marker last, and files it no longer
    has go. Raises Refused for a folder that is not Switchboard's, OSError when the disk refuses. Returns the version
    the copy had before (None when it is new)."""
    files = guide_files() if files is None else files
    d = place_dir(place)
    now = state(place, files)
    if now['is'] == 'theirs':
        raise Refused(THEIRS % where_name(place))
    if now['is'] == 'unreadable':
        raise Refused("%s can't be read (%s)." % (short(d), now['why']))
    mark = {'by': NAME, 'version': core.VERSION, 'digest': digest(files), 'files': [rel for rel, _ in files]}
    parent = os.path.dirname(d)
    os.makedirs(parent, mode=0o755, exist_ok=True)
    if now['is'] == 'absent':
        tmp = tempfile.mkdtemp(prefix='.%s.' % NAME, dir=parent)
        try:
            os.chmod(tmp, 0o755)
            _fill(tmp, files, mark)
            os.rename(tmp, d)
        finally:
            if os.path.lexists(tmp):
                shutil.rmtree(tmp, ignore_errors=True)
        return None
    old = read_mark(d) or {'files': []}
    _fill(d, files, mark)
    keep = set(rel for rel, _ in files)
    for rel in old['files']:
        if rel not in keep:
            _unlink_inside(d, rel)
    _tidy_dirs(d, old['files'])
    return now['version']


def _unlink_inside(d, rel):
    """Remove one file the marker names, never through a folder that is a link."""
    sub = os.path.dirname(rel)
    if sub and os.path.islink(os.path.join(d, sub)):
        return
    p = os.path.join(d, rel)
    if os.path.islink(p) or os.path.isfile(p):
        os.unlink(p)


def _tidy_dirs(d, rels):
    for sub in sorted(set(os.path.dirname(r) for r in rels if os.path.dirname(r)), key=len, reverse=True):
        p = os.path.join(d, sub)
        if os.path.isdir(p) and not os.path.islink(p):
            try:
                os.rmdir(p)                 # only when empty
            except OSError:
                pass


def remove(place):
    """Take Switchboard's own copy out of one place: the files its marker names, then the marker, then the folders
    they leave empty. Returns None when there was no copy of Switchboard's there, else the names left in its folder
    (files that are not Switchboard's: the folder stays with them)."""
    d = place_dir(place)
    m = read_mark(d)
    if m is None:
        return None
    for rel in m['files']:
        _unlink_inside(d, rel)
    _tidy_dirs(d, m['files'])
    os.unlink(os.path.join(d, MARK))
    try:
        os.rmdir(d)
        return []
    except OSError:
        return sorted(os.listdir(d)) if os.path.isdir(d) else []


# ---------- by itself ----------

def _note_linked(path):
    if path not in LINKED:
        LINKED.add(path)
        core.log('%s is a link, so the skill did not go in by itself; switchboard skill on still puts it in' % path)


def _wanted_places(codex=None):
    """The places a copy may go by itself: Claude Code's, once its folder is there; Codex's, when Codex is here."""
    out = []
    if os.path.isdir(core.claude_dir()):
        out.append('claude')
    if codex_here() if codex is None else codex:
        out.append('codex')
    return out


def refresh(files=None):
    """Bring each copy of Switchboard's own from an older program up to date, never through a link and never one a
    newer Switchboard wrote. Not after the person turned the skill off. Returns {place: the version it had}."""
    if core.DEMO or core.settings().get('skill') is False:
        return {}
    files = guide_files() if files is None else files
    done = {}
    for place in PLACES:
        st = state(place, files)
        if st['is'] != 'ours' or st['current'] or st['newer']:
            continue
        if st['linked'] or os.path.islink(os.path.dirname(st['path'])):
            _note_linked(st['linked'] or os.path.dirname(st['path']))
            continue
        try:
            done[place] = put(place, files) or ''
        except (Refused, OSError) as e:
            core.log('the skill in %s was not brought up to date: %s' % (short(st['path']), e))
    return done


def refresh_now():
    """refresh(), under keep's CHOICE lock, so a `switchboard skill off` in between always wins. For the page's
    self-check. Never raises."""
    from . import keep
    try:
        with core.file_lock(keep.CHOICE):
            return refresh()
    except Exception as e:  # noqa: BLE001 - the self-check goes on either way
        core.log('the skill was not brought up to date: %r' % e)
        return {}


def by_itself(now=None, codex=None):
    """At the end of a first run: the skill goes into each place once (see the module's docstring), and a copy of
    Switchboard's own is brought up to date. A refusal or a failure is only logged. Returns {place: 'on' (it went
    in), 'updated', 'theirs' (a skill of the person's own is there: left) or 'linked'}."""
    if core.DEMO:
        return {}
    from . import keep
    out, said = {}, []
    try:
        with core.file_lock(keep.CHOICE):
            s = core.settings()             # read inside the turn: a choice of the person's just before counts
            files = guide_files()
            out.update((p, 'updated') for p in refresh(files))
            if s.get('skill_set_by_you') is True or 'skill' in s or s.get('skill_by_itself'):
                return out
            for place in _wanted_places(codex):
                if place in out:
                    continue
                linked = linked_above(place)
                st = state(place, files)
                if linked:
                    _note_linked(linked)
                    out[place] = 'linked'
                elif st['is'] == 'theirs':
                    out[place] = 'theirs'
                elif st['is'] == 'absent':
                    put(place, files)
                    out[place] = 'on'
                    said.append(SKILL_ON % LABELS[place])
            if 'on' in out.values():
                core.set_setting('skill_by_itself', time.time() if now is None else now)
            theirs = [p for p, v in out.items() if v == 'theirs']
            if theirs and not s.get('skill_theirs_said'):
                core.set_setting('skill_theirs_said', time.time() if now is None else now)
                said += [THEIRS % where_name(p) for p in theirs]
    except Exception as e:  # noqa: BLE001 - the first run goes on either way, and says nothing about it
        core.log('the skill did not go in: %r' % e)
        return out
    for text in said:
        try:
            with core.locked() as st:
                core.event(st, text, 'info', h='setting', by='auto', reason='first run')
        except Exception as e:  # noqa: BLE001 - only the note of it failed
            core.log('the skill was not noted: %r' % e)
    return out


# ---------- the person's choice ----------

def turn(on, codex=None):
    """`switchboard skill on|off`, as the person's choice from now on (marked first, under keep's CHOICE lock).
    Returns (exit code, lines)."""
    from . import keep
    lines, code = [], 0
    with core.file_lock(keep.CHOICE):
        if core.settings().get('skill_set_by_you') is not True:
            core.set_setting('skill_set_by_you', True)
        core.set_setting('skill', bool(on))
        files = guide_files() if on else None
        here = (codex_here() if codex is None else codex) if on else None
        for place in PLACES:
            label, d = LABELS[place], short(place_dir(place))
            try:
                if not on:
                    left = remove(place)
                    st = state(place)
                    if left is None:
                        lines.append('%s: %s' % (label, 'the folder %s is not Switchboard\'s, so it was left as it is.'
                                                 % d if st['is'] == 'theirs' else 'off already.'))
                    else:
                        lines.append('%s: taken out of %s.%s' % (label, d, (
                            " The folder stays: it holds files that are not Switchboard's (%s)." % ', '.join(left))
                            if left else ''))
                    continue
                if place == 'claude' and not os.path.isdir(core.claude_dir()):
                    lines.append("%s: Claude Code's folder %s is not there yet. Start Claude Code once, then run "
                                 'switchboard skill on again.' % (label, short(core.claude_dir())))
                    code = 1
                    continue
                st = state(place, files)
                if st['is'] == 'theirs':
                    lines.append("%s: a skill of your own is in %s, so Switchboard left it. To use Switchboard's, move "
                                 'yours, then run switchboard skill on again.' % (label, d))
                    code = 1
                    continue
                if place == 'codex' and not here and st['is'] != 'ours':
                    lines.append('%s: Codex was not found on this Mac, so nothing was put in %s.'
                                 % (label, short(os.path.dirname(place_dir(place)))))
                    continue
                if st['is'] == 'ours' and st['current']:
                    lines.append('%s: on already, in %s.' % (label, d))
                elif st['is'] == 'ours' and st['newer']:
                    lines.append('%s: on, from a newer Switchboard (%s), in %s. Left as it is.'
                                 % (label, st['version'], d))
                else:
                    was = put(place, files)
                    lines.append('%s: %s, in %s.' % (label, 'brought up to date from %s' % was if was else 'on', d))
            except Refused as e:
                lines.append('%s: %s' % (label, e))
                code = 1
            except OSError as e:
                lines.append("%s: not changed: %s." % (label, e.strerror or e))
                code = 1
    if on:
        lines.append(SHOW_LINE)
    else:
        lines.append('It does not go back in by itself. switchboard skill on puts it back.')
    return code, lines


def status_lines(codex=None):
    """`switchboard skill`: one line per place, each checked now, then how to show it."""
    s = core.settings()
    here = codex_here() if codex is None else codex
    lines = []
    for place in PLACES:
        st = state(place)
        label, d = LABELS[place], short(st['path'])
        if st['is'] == 'ours':
            if st['current']:
                said = 'on, in %s (Switchboard %s)' % (d, st['version'])
            elif st['newer']:
                said = 'on, in %s, from a newer Switchboard (%s)' % (d, st['version'])
            else:
                said = ('on, in %s, from Switchboard %s, older than this one. switchboard skill on brings it up to '
                        'date' % (d, st['version']))
        elif st['is'] == 'theirs':
            said = "off: a skill of your own is in %s, and Switchboard leaves it" % d
        elif st['is'] == 'unreadable':
            said = "unknown: %s can't be read (%s)" % (d, st['why'])
        elif place == 'codex' and not here:
            said = 'off (Codex was not found on this Mac)'
        elif s.get('skill') is False:
            said = 'off (turned off with switchboard skill off)'
        elif st['linked']:
            said = ('off: %s is a symbolic link, so it does not go in by itself. switchboard skill on puts it in'
                    % short(st['linked']))
        else:
            said = 'off. switchboard skill on puts it in'
        lines.append('%s: %s' % (label, said))
    lines.append(SHOW_LINE)
    return lines


def doctor_lines(codex=None):
    """[(good: True, None for not set up, False for a look, text, the one thing to do)] for `switchboard doctor`:
    Claude Code's place, and Codex's when Codex is here or a copy is there."""
    here = codex_here() if codex is None else codex
    out = []
    for place in PLACES:
        st = state(place)
        if place == 'codex' and not here and st['is'] != 'ours':
            continue
        label = LABELS[place]
        if st['is'] == 'ours' and (st['current'] or st['newer']):
            out.append((True, '%s (%s)' % (label, short(st['path'])), ''))
        elif st['is'] == 'ours':
            out.append((False, '%s: from Switchboard %s, older than this one' % (label, st['version']),
                        'Bring it up to date with: switchboard skill on'))
        elif st['is'] == 'theirs':
            out.append((None, '%s: a skill of your own has its name (%s)' % (label, short(st['path'])),
                        'Switchboard leaves it. switchboard skill show prints Switchboard\'s.'))
        elif st['is'] == 'unreadable':
            out.append((False, "%s: %s can't be read" % (label, short(st['path'])), st['why'] or ''))
        else:
            out.append((None, '%s: off' % label, 'Optional. It answers questions about Switchboard in your agent. '
                        'Run: switchboard skill on'))
    return out


def take_out_all():
    """For the uninstaller: Switchboard's own copy out of each place, one line each; a place with none says nothing.
    Forgets that it went in by itself, so an install again puts it back at its first run. Returns (lines, left: what
    could not be taken out)."""
    lines, left = [], []
    for place in PLACES:
        try:
            gone = remove(place)
        except OSError as e:
            left.append((LABELS[place], e.strerror or str(e)))
            continue
        if gone is None:
            continue
        lines.append('%s: taken out of %s%s.' % (LABELS[place], short(place_dir(place)),
                                                  " (the folder stays, with files that are not Switchboard's)"
                                                  if gone else ''))
    try:
        if core.settings().get('skill_by_itself'):
            core.set_setting('skill_by_itself', None)
    except Exception as e:  # noqa: BLE001 - only the next install's first run misses it
        core.log('skill_by_itself was not forgotten: %r' % e)
    return lines, left


# ---------- the command ----------

def main(args):
    """`switchboard skill [on|off|show]`."""
    if args not in ([], ['on'], ['off'], ['show']):
        print(USAGE)
        return 2
    if args == ['show']:
        sys.stdout.write(show_text())
        return 0
    if not args:
        print('\n'.join(status_lines()))
        return 0
    if core.DEMO:
        raise core.SwitchError('The demo has made-up accounts; nothing is changed.')
    code, lines = turn(args[0] == 'on')
    print('\n'.join(lines))
    return code
