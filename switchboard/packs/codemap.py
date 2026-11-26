"""It doesn't know my codebase: use a code map that already exists, and say how to add one.

Switchboard does not index code. This pack:

  detect()      finds, without running anything, the code maps this Mac already has:
                  graphify   the /graphify skill (<Claude Code folder>/skills/graphify/SKILL.md),
                             its command (`graphify` on PATH or in ~/.local/bin), and a project's
                             graphify-out/graph.json;
                  codegraph  its command (on PATH, ~/.local/bin, ~/.codegraph/current/bin), its MCP
                             server in Claude Code's ~/.claude.json, its own block in the user
                             CLAUDE.md (<!-- CODEGRAPH_START -->), and a project's .codegraph/.
  ADD_CODEGRAPH the documented commands to add codegraph (colbymchenry/codegraph, MIT), as text.
                Switchboard never runs them.
  plan/apply/undo  one switch: a short block at the top of the user CLAUDE.md that tells Claude Code
                to ask the map before searching or opening files. It names only the maps found here,
                and leaves codegraph out when codegraph's own installer already added its block.
                Same marker-block guarantees as the house rules (switchboard/packs/common.py).

Sources (read 2026-11-13):
  codegraph README at f72c9d2: install.sh, `codegraph install`, `codegraph init`, `codegraph uninstall`,
  and src/installer/instructions-template.ts (its CODEGRAPH_START/END block).
  graphify SKILL.md: `graphify query "<question>"` answers from an existing graphify-out/graph.json.

Manual check (reads only):  python3 -m switchboard.packs.codemap [detect|show] [project folder]
"""
import json
import os
import sys

from switchboard.packs import common

ID = 'code-map'
NAME = 'Code map first'
START = '<!-- switchboard code-map: start. Managed by Switchboard; edit outside these lines. -->'
END = '<!-- switchboard code-map: end -->'
CODEGRAPH_BLOCK_START = '<!-- CODEGRAPH_START -->'

GRAPHIFY_LINE = ('- `graphify-out/graph.json` at the project root (graphify): run `graphify query "<question>"` and '
                 'open only the files it names.')
CODEGRAPH_LINE = ('- `.codegraph/` at the project root (codegraph): use the `codegraph_explore` tool, or '
                  '`codegraph explore "<question>"` in the shell.')

# The documented way to add codegraph, step by step. Shown to the person; never run by Switchboard.
ADD_CODEGRAPH = (
    {'step': 'Install the codegraph command (no Node needed)',
     'run': 'curl -fsSL https://raw.githubusercontent.com/colbymchenry/codegraph/main/install.sh | sh',
     'or': 'npm i -g @colbymchenry/codegraph',
     'then': 'Open a new terminal so the command is found.'},
    {'step': 'Connect it to Claude Code',
     'run': 'codegraph install',
     'then': ('Writes its MCP server to ~/.claude.json, an allow rule to ~/.claude/settings.json and a short '
              'marked section to ~/.claude/CLAUDE.md. Restart Claude Code.')},
    {'step': 'Build the map for one project',
     'run': 'cd <project folder> && codegraph init',
     'then': 'Creates .codegraph/ there; it updates itself while Claude Code runs.'},
)
REMOVE_CODEGRAPH = (
    {'step': 'Take codegraph out of Claude Code (and remove the command)', 'run': 'codegraph uninstall'},
    {'step': "Remove one project's map", 'run': 'cd <project folder> && codegraph uninit'},
)
SOURCE = 'https://github.com/colbymchenry/codegraph (README, read Nov 13 2026)'


def _exe(name, dirs):
    for d in dirs:
        if not d:
            continue
        p = os.path.join(d, name)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def _read_json(path):
    raw = common.read_bytes(path)
    if raw is None:
        return None
    try:
        obj = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def _claude_json(cd, home):
    """Where Claude Code keeps .claude.json: inside CLAUDE_CONFIG_DIR when that is set, else beside ~/.claude."""
    if os.environ.get('CLAUDE_CONFIG_DIR') or os.path.realpath(cd) != os.path.realpath(os.path.join(home, '.claude')):
        return os.path.join(cd, '.claude.json')
    return os.path.join(home, '.claude.json')


def _skill_name(path):
    raw = common.read_bytes(path)
    if raw is None:
        return None
    text = raw.decode('utf-8', 'replace')
    if not text.startswith('---'):
        return None
    for line in text.split('\n')[1:40]:
        if line.strip() == '---':
            break
        if line.startswith('name:'):
            return line[5:].strip().strip('"\'')
    return None


def detect(claude_dir=None, project=None, home=None, path=None):
    """What code maps exist, found by looking at files only. Nothing is run."""
    cd = common.claude_dir(claude_dir)
    home = home or common.home()
    dirs = (os.environ.get('PATH', '') if path is None else path).split(os.pathsep)
    extra = [os.path.join(home, '.local', 'bin')]
    skill = os.path.join(cd, 'skills', 'graphify', 'SKILL.md')
    skill_ok = _skill_name(skill) == 'graphify'
    proj_skill = os.path.join(project, '.claude', 'skills', 'graphify', 'SKILL.md') if project else None
    if proj_skill and _skill_name(proj_skill) == 'graphify':
        skill_ok, skill = True, proj_skill
    g = {'skill': skill if skill_ok else None,
         'command': _exe('graphify', dirs + extra),
         'on_path': bool(_exe('graphify', dirs)),
         'project_map': None}
    if project and os.path.isfile(os.path.join(project, 'graphify-out', 'graph.json')):
        g['project_map'] = os.path.join(project, 'graphify-out', 'graph.json')
    g['present'] = bool(g['skill'] or g['command'])

    cj = _claude_json(cd, home)
    cfg = _read_json(cj) or {}
    servers = cfg.get('mcpServers') if isinstance(cfg.get('mcpServers'), dict) else {}
    proj_servers = {}
    if project:
        pm = _read_json(os.path.join(project, '.mcp.json')) or {}
        proj_servers = pm.get('mcpServers') if isinstance(pm.get('mcpServers'), dict) else {}
    instr = common.read_bytes(common.instructions_path(cd)) or b''
    c = {'command': _exe('codegraph', dirs + extra + [os.path.join(home, '.codegraph', 'current', 'bin')]),
         'mcp': cj if 'codegraph' in servers else (os.path.join(project, '.mcp.json')
                                                    if 'codegraph' in proj_servers else None),
         'own_block': CODEGRAPH_BLOCK_START.encode() in instr,
         'project_map': None}
    if project and os.path.isdir(os.path.join(project, '.codegraph')):
        c['project_map'] = os.path.join(project, '.codegraph')
    c['present'] = bool(c['command'] or c['mcp'])
    return {'graphify': g, 'codegraph': c}


def body(found):
    """The block's lines for what was found, or None when there is no map tool to point at."""
    lines = []
    if found['graphify']['present']:
        lines.append(GRAPHIFY_LINE)
    if found['codegraph']['present'] and not found['codegraph']['own_block']:
        lines.append(CODEGRAPH_LINE)
    if not lines:
        return None
    return (('## Ask the code map first',
             'When the project you are in has a code map, ask it before searching or opening files to find code:')
            + tuple(lines)
            + ('With no map in the project, work as usual; building one is the person\'s call.',))


def spec(found):
    return {'id': ID, 'name': NAME, 'start': START, 'end': END, 'body': body(found) or ()}


def plan(on, claude_dir=None, state_path=None, project=None, home=None, path=None):
    """What turning the instruction on or off would change, plus what was found and how to add codegraph.
    Changes nothing and runs nothing."""
    found = detect(claude_dir, project, home, path)
    sp = spec(found)
    if on and not sp['body']:
        out = common.plan_block(dict(sp, body=('',)), on, claude_dir, state_path)
        out['op'] = None
        if found['codegraph']['present'] and found['codegraph']['own_block']:
            out['blocked'] = None
            out['says'] = ("codegraph's own section in %s already tells Claude Code to ask it first, and no other "
                           'map was found, so there is nothing to add.' % out['file'])
        else:
            out['blocked'] = ('No code map tool was found on this Mac, so there is nothing to point Claude Code at. '
                              'Add codegraph first (the steps below), then turn this on.')
    else:
        out = common.plan_block(sp, on, claude_dir, state_path)
    out['found'] = found
    out['add_codegraph'] = [] if found['codegraph']['present'] else [dict(s) for s in ADD_CODEGRAPH]
    out['remove_codegraph'] = [dict(s) for s in REMOVE_CODEGRAPH] if found['codegraph']['present'] else []
    out['source'] = SOURCE
    if on and found['graphify']['command'] and not found['graphify']['on_path']:
        out['notes'].append('graphify is at %s, which is not on this PATH. If Claude Code\'s shell cannot find it, '
                            'Claude can still use the /graphify skill.' % found['graphify']['command'])
    if on and found['codegraph']['own_block']:
        out['notes'].append("codegraph's installer already put its own section in the same file, so this block "
                            'does not repeat it.')
    if on and project and not (found['graphify']['project_map'] or found['codegraph']['project_map']):
        out['notes'].append('%s has no map yet; Claude Code will work as usual there until one is built.' % project)
    return out


def apply(plan_):
    return common.apply_block(plan_)


def undo(claude_dir=None, state_path=None, home=None, path=None):
    """Take the instruction out again. Refuses, changing nothing, when the person edited inside it."""
    return common.apply_block(plan(False, claude_dir, state_path, home=home, path=path))


def status(claude_dir=None, state_path=None, project=None, home=None, path=None):
    found = detect(claude_dir, project, home, path)
    s = common.block_status(spec(found), claude_dir, state_path)
    s['found'] = found
    return s


def main(argv=None, out=None):
    argv = sys.argv[1:] if argv is None else argv
    out = out or sys.stdout
    what = argv[0] if argv else 'detect'
    project = os.path.abspath(argv[1]) if len(argv) > 1 else os.getcwd()
    found = detect(project=project)
    if what == 'detect':
        out.write(json.dumps(found, indent=1) + '\n')
        if not found['codegraph']['present']:
            out.write('\nTo add codegraph (Switchboard does not run these):\n')
            for s in ADD_CODEGRAPH:
                out.write('  %s:\n    %s\n' % (s['step'], s['run']))
        return 0
    if what == 'show':
        b = body(found)
        out.write(('\n'.join((START,) + b + (END,)) + '\n') if b else 'No code map tool found.\n')
        return 0
    out.write('usage: python3 -m switchboard.packs.codemap [detect|show] [project folder]\n')
    return 2


if __name__ == '__main__':
    sys.exit(main())
