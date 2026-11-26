"""Works my way: a short block of house rules in Claude Code's user instructions file.

One switch, off until turned on. On adds a managed block at the top of <Claude Code folder>/CLAUDE.md
(~/.claude/CLAUDE.md unless CLAUDE_CONFIG_DIR says otherwise), between two marker lines. Off takes
out exactly those lines. The block holds the core the most-used rule packs share, written here in
Switchboard's own words (no text is copied from any of them):

    think before changing code; make the simplest change; touch only what was asked;
    check before saying done; say what was not checked.

Guarantees (tests/test_packs_houserules.py):
  - Adding the block puts bytes only between the start of the start marker line and the end of the
    end marker line. Every byte of the person's file keeps its order and value.
  - Taking it out removes exactly those bytes wherever the block sits, so the file is byte for byte
    what it was, plus anything the person wrote around the block since. A file Switchboard created
    for the block is deleted again.
  - If the person edited inside the block, turning it off refuses and says so.
  - At most 40 lines, markers included, and under 1,500 tokens by common.est_tokens (characters / 3,
    rounded up, which over-counts English prose).

Claude Code strips block-level HTML comments from CLAUDE.md before Claude sees it, so the two
marker lines cost no context (docs: code.claude.com/docs/en/memory, "Block-level HTML comments
(`<!-- maintainer notes -->`) in CLAUDE.md files are stripped before the content is injected").

Manual check (reads only):  python3 -m switchboard.packs.houserules [show|status]
"""
import json
import sys

from switchboard.packs import common

ID = 'house-rules'
NAME = 'House rules'
START = '<!-- switchboard house-rules: start. Managed by Switchboard; edit outside these lines. -->'
END = '<!-- switchboard house-rules: end -->'
MAX_LINES = 40
MAX_TOKENS = 1500

BODY = (
    '## How to work here',
    '',
    'Defaults for every project. Anything else in this file, or in a project\'s own instructions, wins over them.',
    '',
    'Before changing anything',
    '- Say in a line what you take the task to be and what you are assuming. If it could mean two things, ask '
    'rather than pick one quietly.',
    '- If a simpler route would do the job, say so before building the bigger one.',
    '',
    'While changing things',
    '- Keep the change as small as the job allows. Add no features, options or layers nobody asked for.',
    '- Reuse what the project already has before adding a library, a file or a new pattern.',
    '- Change only what the task needs. Leave unrelated code, comments and formatting alone, even if you would '
    'write them differently; mention them instead.',
    '- Tidy up only what your own change left behind, such as imports or files it made unused.',
    '',
    'Before saying it is done',
    '- Decide early how you will know it works: a test, a command, a page to look at. For a bug, make it happen '
    'first.',
    '- Run that check and read what it says. Writing the code is not the same as it working.',
    '- If a check fails, say so, then fix it or stop and explain where things stand.',
    '',
    'When reporting back',
    '- Say what changed and how you checked it, in a few plain lines.',
    '- Name what you did not check or could not run, and anything you are unsure of.',
    '- Claim no more than the checks showed.',
)

SPEC = {'id': ID, 'name': NAME, 'start': START, 'end': END, 'body': BODY}


def text():
    """The whole block as it is written (with '\\n' line endings)."""
    return '\n'.join((START,) + BODY + (END,)) + '\n'


def size():
    t = text()
    return {'lines': len(BODY) + 2, 'chars': len(t), 'tokens_estimate': common.est_tokens(t),
            'how': 'characters / 3, rounded up', 'max_lines': MAX_LINES, 'max_tokens': MAX_TOKENS}


def status(claude_dir=None, state_path=None):
    return common.block_status(SPEC, claude_dir, state_path)


def plan(on, claude_dir=None, state_path=None):
    """What turning the house rules on (True) or off (False) would change. Changes nothing."""
    return common.plan_block(SPEC, on, claude_dir, state_path)


def apply(plan_):
    return common.apply_block(plan_)


def undo(claude_dir=None, state_path=None):
    """Take the block out again. Refuses, changing nothing, when the person edited inside it."""
    return common.apply_block(plan(False, claude_dir, state_path))


def main(argv=None, out=None):
    argv = sys.argv[1:] if argv is None else argv
    out = out or sys.stdout
    what = argv[0] if argv else 'status'
    if what == 'show':
        out.write(text())
        return 0
    if what == 'status':
        out.write(json.dumps({'status': status(), 'size': size()}, indent=1) + '\n')
        return 0
    out.write('usage: python3 -m switchboard.packs.houserules [show|status]\n')
    return 2


if __name__ == '__main__':
    sys.exit(main())
